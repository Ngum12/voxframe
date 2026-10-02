"""Render scene segments from a scene plan, then concatenate them.

This is the structure described in D-011: each scene renders to an independent
segment, segments are concatenated, and the audio is laid in once at the end.
Peak memory stays flat regardless of audio length, every scene is independently
cacheable, and re-rendering after editing one scene touches only that scene.

Motion
------
Scenes with an asset get a Ken Burns move rendered at 2x internal scale and
downscaled, which is what makes slow zooms smooth rather than stepped (D-036).
Scenes without one render a flat background, which is the correct outcome when
the library holds nothing relevant (D-042): an empty frame reads as a deliberate
choice where a wrong image reads as a bug.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.config.settings import QualityPreset
from voxframe.config.style import MotionStyle
from voxframe.plan.scene_plan import MotionKind, PlannedScene, ScenePlan
from voxframe.render.compose.cards import Card, render_card_segment
from voxframe.render.compose.clips import render_clip_segment
from voxframe.render.compose.footage import render_footage_segment
from voxframe.render.compose.segment_cache import SegmentCache, segment_key
from voxframe.render.compose.transitions import (
    TransitionPlan,
    crossfade_filter,
    total_frames_after,
)
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import filter_path_context, run_ffmpeg
from voxframe.render.motion.ken_burns import OVERSAMPLE, plan_move, zoompan_filter
from voxframe.render.motion.saliency import find_subject_center

__all__ = ["SegmentResult", "concat_segments", "render_scene_segments"]

log = structlog.get_logger(__name__)

#: Intermediate codec for scene segments (D-016). CRF 16 in yuv444p: visually
#: lossless through the single caption-burn pass that follows, at roughly 40%
#: of FFV1's disk cost.
#: Gradient endpoints for a scene with no asset. Dark enough that captions
#: read clearly over them, light enough not to look like a dropped frame.
_BACKGROUND_TOP = "0x12141F"
_BACKGROUND_BOTTOM = "0x2A2F42"

_INTERMEDIATE_ARGS = [
    "-c:v", "libx264",
    "-crf", "16",
    "-preset", "veryfast",
    "-pix_fmt", "yuv444p",
]


@dataclass(frozen=True, slots=True)
class SegmentResult:
    """One rendered scene segment."""

    scene_index: int
    path: Path
    frames: int
    had_asset: bool


def _scene_filter(
    scene: PlannedScene,
    width: int,
    height: int,
    fps: float,
    motion: MotionStyle,
    background: str,
    subject_center: tuple[float, float] | None = None,
) -> str:
    """Build the filter chain for one scene.

    Returns a chain that takes the scene's input and produces exactly
    ``scene.duration_frames`` frames at the output size.
    """
    if scene.asset is None or scene.motion is MotionKind.NONE:
        # No asset, or motion disabled: the input is already a solid colour
        # source, so only the pixel format needs fixing.
        return "null"

    move = plan_move(
        scene.index,
        scene.asset.id,
        scene.duration_frames,
        intensity=motion.intensity,
        subject_center=subject_center,
    )

    return zoompan_filter(move, width, height, fps, oversample=OVERSAMPLE)


def render_scene_segments(
    plan: ScenePlan,
    caps: FFmpegCapabilities,
    work_dir: Path,
    *,
    width: int,
    height: int,
    motion: MotionStyle,
    quality: QualityPreset = QualityPreset.STANDARD,
    background: str = "0x141824",
    frame_overrides: list[int] | None = None,
    cache: SegmentCache | None = None,
) -> list[SegmentResult]:
    """Render every scene to its own segment file.

    Args:
        plan: The scene plan. The only input, per D-011.
        caps: Probed FFmpeg capabilities.
        work_dir: Where segments are written.
        width: Output frame width.
        height: Output frame height.
        motion: Motion style, supplying intensity.
        quality: Quality preset. Affects the final encode, not segments.
        background: Colour for scenes with no asset.
        frame_overrides: Frames to render per scene, replacing the scene's own
            duration. A scene followed by a crossfade needs extra frames for
            the overlap to consume (D-097); without them the video loses the
            total transition length.
        cache: Reuses segments across renders, so a long render that fails
            partway can resume rather than restarting (D-101).

    Returns:
        One result per scene, in order.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[SegmentResult] = []

    for position, scene in enumerate(plan.scenes):
        segment = work_dir / f"scene_{scene.index:05d}.mp4"
        frames = (
            frame_overrides[position]
            if frame_overrides is not None and position < len(frame_overrides)
            else scene.duration_frames
        )

        key = ""
        if cache is not None and cache.enabled:
            key = segment_key(
                scene,
                frames=frames,
                width=width,
                height=height,
                fps=plan.fps,
                motion_signature=_motion_signature(motion),
                quality=quality.value,
                background=background,
                footage_signature=_footage_signature(plan, scene),
            )
            if cache.fetch(key, segment):
                results.append(
                    SegmentResult(
                        scene.index, segment, frames, scene.asset is not None
                    )
                )
                continue

        if scene.is_card:
            # A card carries its own text and needs no asset (D-099).
            render_card_segment(
                caps,
                Card(
                    kind=scene.card_kind,
                    text=scene.card_text,
                    before_scene=scene.index,
                    seconds=frames / plan.fps,
                ),
                segment,
                width=width,
                height=height,
                fps=plan.fps,
                background=background,
                intermediate_args=_INTERMEDIATE_ARGS,
                font_path=_card_font(),
            )
            results.append(SegmentResult(scene.index, segment, frames, False))

        elif plan.shows_speaker(scene) and plan.footage is not None:
            # The speaker, cut to the frames the scene's words were spoken
            # over (D-192). The plan's validation guarantees a start.
            assert scene.footage_start is not None
            if not Path(plan.footage.path).is_file():
                # Moved or deleted since the plan was made: the scene falls
                # back to its picture rather than failing the whole video.
                log.warning("render.footage.missing", path=plan.footage.path)
                _render_fallback(
                    plan, scene, caps, segment, width, height, frames, motion, background
                )
                results.append(SegmentResult(scene.index, segment, frames, scene.asset is not None))
            else:
                render_footage_segment(
                    caps,
                    plan.footage,
                    scene.footage_start,
                    segment,
                    width=width,
                    height=height,
                    fps=plan.fps,
                    frames=frames,
                    intermediate_args=_INTERMEDIATE_ARGS,
                )
                results.append(SegmentResult(scene.index, segment, frames, True))

        elif scene.asset is not None and scene.asset.is_video:
            # A clip supplies its own motion, so it takes a different path
            # entirely: no Ken Burns, audio stripped, trimmed or stretched to
            # the grid (D-085).
            clip_path = Path(scene.asset.path)
            if not clip_path.is_file():
                log.warning(
                    "render.clip.missing", scene=scene.index, path=str(clip_path)
                )
                _render_background(
                    caps, segment, width, height, plan.fps, frames, background
                )
                results.append(SegmentResult(scene.index, segment, frames, False))
                continue

            render_clip_segment(
                caps,
                clip_path,
                segment,
                width=width,
                height=height,
                fps=plan.fps,
                frames=frames,
                clip_seconds=scene.asset.duration,
                intermediate_args=_INTERMEDIATE_ARGS,
            )
            results.append(SegmentResult(scene.index, segment, frames, True))

        elif scene.asset is not None and scene.motion is not MotionKind.NONE:
            asset_path = Path(scene.asset.path)
            if not asset_path.is_file():
                # A moved or deleted file must not abort the render: the plan
                # may be older than the library, and a missing image degrades
                # to a background rather than failing outright.
                log.warning(
                    "render.asset.missing",
                    scene=scene.index,
                    path=str(asset_path),
                )
                _render_background(
                    caps, segment, width, height, plan.fps, frames, background
                )
                results.append(SegmentResult(scene.index, segment, frames, False))
                continue

            # Aim the move at the subject rather than the frame centre, which
            # on a landscape photograph is usually sky (D-047). A low-confidence
            # estimate is discarded: aiming at noise is worse than aiming at
            # the centre.
            saliency = find_subject_center(asset_path)
            subject = saliency.center if saliency.is_confident else None

            fp = filter_path_context(asset_path)
            chain = _scene_filter(
                scene, width, height, plan.fps, motion, background, subject
            )

            run_ffmpeg(
                caps.ffmpeg_path,
                [
                    "-loglevel", "error",
                    "-loop", "1",
                    "-i", fp.name if fp.cwd else str(asset_path.resolve()),
                    "-vf", chain,
                    "-frames:v", str(frames),
                    "-r", str(plan.fps),
                    *_INTERMEDIATE_ARGS,
                    "-y", str(segment.resolve()),
                ],
                cwd=fp.cwd,
            )
            results.append(SegmentResult(scene.index, segment, frames, True))
        else:
            _render_background(
                caps, segment, width, height, plan.fps, frames, background
            )
            results.append(SegmentResult(scene.index, segment, frames, False))

        if key and cache is not None:
            cache.store(key, segment)

    log.info(
        "render.segments.done",
        segments=len(results),
        with_assets=sum(1 for r in results if r.had_asset),
        cache=cache.summary() if cache is not None else "disabled",
    )

    return results


def _footage_signature(plan: ScenePlan, scene: PlannedScene) -> str:
    """What makes a speaker shot look as it does, or ``""`` for any other scene.

    The file is identified by its size and modification time as well as its
    path, so replacing the recording at the same path is a different picture.
    """
    if not plan.shows_speaker(scene) or plan.footage is None:
        return ""
    footage = plan.footage
    source = Path(footage.path)
    try:
        stat = source.stat()
        identity = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        identity = "missing"
    return (
        f"speaker:{footage.path}:{identity}:{footage.audio_offset:.6f}"
        f":{footage.subject_x:.4f}:{scene.footage_start:.6f}"
    )


def _render_fallback(
    plan: ScenePlan,
    scene: PlannedScene,
    caps: FFmpegCapabilities,
    segment: Path,
    width: int,
    height: int,
    frames: int,
    motion: MotionStyle,
    background: str,
) -> None:
    """A speaker shot whose footage is gone: its picture, else the background."""
    if (
        scene.asset is not None
        and not scene.asset.is_video
        and scene.motion is not MotionKind.NONE
        and Path(scene.asset.path).is_file()
    ):
        asset_path = Path(scene.asset.path)
        saliency = find_subject_center(asset_path)
        fp = filter_path_context(asset_path)
        run_ffmpeg(
            caps.ffmpeg_path,
            [
                "-loglevel", "error",
                "-loop", "1",
                "-i", fp.name if fp.cwd else str(asset_path.resolve()),
                "-vf", _scene_filter(
                    scene, width, height, plan.fps, motion, background,
                    saliency.center if saliency.is_confident else None,
                ),
                "-frames:v", str(frames),
                "-r", str(plan.fps),
                *_INTERMEDIATE_ARGS,
                "-y", str(segment.resolve()),
            ],
            cwd=fp.cwd,
        )
        return
    _render_background(caps, segment, width, height, plan.fps, frames, background)


def _motion_signature(motion: MotionStyle) -> str:
    """A stable description of the settings that change how motion looks.

    Only the fields the renderer actually reads: including one it ignores
    would miss cache hits, and omitting one it uses would serve stale frames
    (D-101).
    """
    return (
        f"kb={motion.ken_burns_enabled}"
        f":i={motion.intensity:.3f}"
        f":px={motion.parallax_enabled}"
    )


def _card_font() -> Path | None:
    """The bold face for card text, or ``None`` to let FFmpeg choose.

    A missing font must not fail a render: drawtext falls back to a system
    face, which is worse-looking but still correct.
    """
    from voxframe.assets import FontsMissing, fonts_dir

    try:
        bold = sorted(fonts_dir().glob("*Bold*.ttf"))
    except FontsMissing:
        return None

    return bold[0] if bold else None


def _render_background(
    caps: FFmpegCapabilities,
    output: Path,
    width: int,
    height: int,
    fps: float,
    frames: int,
    background: str,
) -> None:
    """Render a segment for a scene with no asset.

    A flat panel between photographs reads as a fault rather than a choice
    (D-050). A slow vertical gradient drift gives the frame the same sense of
    movement as its neighbours, so an unmatched scene looks intentional.

    The drift is deliberately subtle — a few percent of brightness over the
    scene — because the point is to avoid deadness, not to draw attention to a
    scene that has nothing to show.
    """
    # A vertical gradient, rendered by the `gradients` source rather than
    # `geq` (D-054).
    #
    # `geq` evaluates an expression per pixel per frame: measured at over 60
    # seconds for 159 frames at 720p, which made the *background* scenes cost
    # more than the Ken Burns ones. `gradients` is a native source and costs
    # effectively nothing.
    #
    # The gentle drift is dropped with it. It was a nicety; the gradient alone
    # is enough to stop the frame reading as a dropped frame, and no user is
    # going to notice a 12-unit brightness oscillation they were never told
    # about.
    # `speed=0` and `nb_colors=2` pin the gradient: without them the source
    # animates its angle, which reads as a rotating wash rather than a still
    # background.
    gradient = (
        f"gradients=s={width}x{height}"
        f":c0={_BACKGROUND_TOP}:c1={_BACKGROUND_BOTTOM}:nb_colors=2"
        f":x0=0:y0=0:x1=0:y1={height}"
        f":speed=0:d={frames / fps:.3f}:r={fps}"
    )

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", gradient,
            "-frames:v", str(frames),
            *_INTERMEDIATE_ARGS,
            "-y", str(output.resolve()),
        ],
    )


def _concat_with_transitions(
    segments: list[SegmentResult],
    caps: FFmpegCapabilities,
    output: Path,
    transitions: list[TransitionPlan],
    fps: float,
) -> Path:
    """Join segments with xfade, re-encoding at the blends.

    Raises:
        RenderError: If the output frame count does not match the sum of the
            scene durations. A crossfade that silently shortens the video
            desynchronises every later caption (D-025, D-097), so this is
            checked rather than trusted.
    """
    # Transitions are matched to boundaries by position, as crossfade_filter
    # does: the i-th joins segment i to segment i + 1.
    boundaries = list(transitions[: max(0, len(segments) - 1)])
    expected = total_frames_after([s.frames for s in segments], boundaries)
    runs = transition_chunks(boundaries, len(segments))

    if len(runs) == 1:
        _crossfade_run(segments, boundaries, caps, output, fps)
    else:
        # A long video is joined in runs that break only at hard cuts, so no
        # blend is lost; the runs are then joined by the concat demuxer from a
        # list file, which has no length limit. One call for every segment put
        # 157 inputs and their filter graph on one command line, past Windows'
        # limit: WinError 206, and no video (D-167).
        folder = output.parent / f".{output.stem}_runs"
        folder.mkdir(parents=True, exist_ok=True)
        pieces = []
        for number, (start, end) in enumerate(runs):
            piece = folder / f"run_{number:03d}.mp4"
            _crossfade_run(
                segments[start:end], boundaries[start : end - 1], caps, piece, fps
            )
            pieces.append(piece)
        _concat_listed(pieces, caps, output, folder / "runs.txt")
        shutil.rmtree(folder, ignore_errors=True)

    log.info(
        "render.concat.transitions",
        segments=len(segments),
        blends=sum(1 for t in boundaries if t.is_blend),
        frames=expected,
        runs=len(runs),
    )

    return output


#: Most segments one FFmpeg call joins with crossfades. Each is an input on
#: the command line, which Windows caps at 32,767 characters (D-167): 40
#: inputs with long paths and their filter graph stay under a third of that.
MAX_SEGMENTS_PER_RUN = 40


def transition_chunks(
    boundaries: list[TransitionPlan], segment_count: int, limit: int = MAX_SEGMENTS_PER_RUN
) -> list[tuple[int, int]]:
    """Split segments into runs of at most ``limit``, breaking only at hard cuts.

    A blend needs both its segments in one FFmpeg call, so a run can only end
    at a cut. A run with no cut in reach grows past ``limit`` until one comes;
    the runner's command-line check stops the rare case where that is still
    too long, with a message rather than WinError 206.

    Returns:
        ``(start, end)`` segment ranges, end exclusive, covering every segment.
    """
    runs: list[tuple[int, int]] = []
    start = 0
    last_cut: int | None = None
    for index in range(segment_count - 1):
        boundary = boundaries[index] if index < len(boundaries) else None
        if boundary is None or not boundary.is_blend:
            last_cut = index
        if index + 1 - start >= limit and last_cut is not None:
            runs.append((start, last_cut + 1))
            start = last_cut + 1
            last_cut = None
    runs.append((start, segment_count))
    return runs


def _crossfade_run(
    segments: list[SegmentResult],
    boundaries: list[TransitionPlan],
    caps: FFmpegCapabilities,
    output: Path,
    fps: float,
) -> None:
    """Join a run of segments with its blends and cuts, in one FFmpeg call."""
    durations = [segment.frames for segment in segments]
    expected = total_frames_after(durations, boundaries)

    inputs: list[str] = []
    for segment in segments:
        inputs.extend(["-i", str(segment.path.resolve())])

    if len(segments) == 1:
        # Re-encoded like every other run, so the pieces the demuxer joins
        # share one encoder's settings and timebase.
        chain, final = "[0:v]settb=AVTB,setpts=PTS-STARTPTS,setsar=1[v]", "v"
    else:
        chain, final = crossfade_filter(
            [s.path for s in segments], boundaries, fps, durations
        )

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            *inputs,
            "-filter_complex", chain,
            "-map", f"[{final}]",
            "-frames:v", str(expected),
            "-r", str(fps),
            *_INTERMEDIATE_ARGS,
            "-y", str(output.resolve()),
        ],
    )


def _concat_listed(
    files: list[Path], caps: FFmpegCapabilities, output: Path, listing: Path
) -> None:
    """Join files that share codec and parameters, without re-encoding.

    The paths go in a list file, not on the command line, so any number fit.
    """
    lines = []
    for path in files:
        # The concat demuxer's own quoting: a single quote is closed, escaped
        # and reopened.
        escaped = str(path.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", str(listing.resolve()),
            "-c", "copy",
            "-y", str(output.resolve()),
        ],
    )


def concat_segments(
    segments: list[SegmentResult],
    caps: FFmpegCapabilities,
    output: Path,
    work_dir: Path,
    transitions: list[TransitionPlan] | None = None,
    fps: float = 30.0,
) -> Path:
    """Concatenate segments into one video.

    Uses the concat *demuxer* rather than the filter when every boundary is a
    hard cut: the segments share codec and parameters, so this copies streams
    without re-encoding. That keeps the intermediate quality intact and costs
    seconds rather than minutes.

    With any crossfade the segments must be re-encoded, because blending two
    streams cannot be done by copying either. Only then is the slower path
    taken — a video of all cuts pays nothing for the feature (D-097).

    Args:
        segments: Rendered segments, in order.
        caps: Probed FFmpeg capabilities.
        output: Where to write.
        work_dir: Scratch directory.
        transitions: One per boundary. ``None`` means all cuts.
        fps: Frame rate, for converting transition lengths to offsets.

    Returns:
        The concatenated file.
    """
    if transitions and any(t.is_blend for t in transitions):
        return _concat_with_transitions(
            segments, caps, output, transitions, fps
        )

    # The concat demuxer resolves paths relative to the list file, and its own
    # quoting rules differ from the filter parser's, so single quotes are
    # escaped in its own way.
    _concat_listed(
        [segment.path for segment in segments], caps, output, work_dir / "segments.txt"
    )
    return output


def cleanup_segments(work_dir: Path) -> None:
    """Remove the segment working directory."""
    shutil.rmtree(work_dir, ignore_errors=True)
