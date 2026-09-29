"""Render a video from a scene plan.

The plan is the only input (D-011). Nothing upstream — transcript, library,
matcher — is consulted, so hand-editing the JSON, editing it in the web UI, and
regenerating it are the same operation as far as this code is concerned.

Order of operations, and why:

1. **Scene segments**, each rendered independently with its motion. Flat
   memory, independently cacheable, and a re-render after editing one scene
   touches only that scene.
2. **Concatenate** by stream copy, so intermediate quality survives.
3. **Burn captions** in one pass over the whole video, so caption timing
   derives directly from word timestamps and cannot drift.
4. **Lay in the audio** once, unmodified, padded to the frame boundary rather
   than truncated (D-032).
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from voxframe.assets import FontsMissing, fonts_dir
from voxframe.config.settings import QualityPreset
from voxframe.config.style import StyleTemplate, TransitionKind
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio import MusicSettings, music_filter_chain
from voxframe.render.captions.ass import write_ass
from voxframe.render.captions.subtitles import write_srt, write_vtt
from voxframe.render.compose.captioned import (
    _CRF,
    _SPEED,
    RenderError,
    RenderResult,
    _dimensions,
    _verify_frame_count,
)
from voxframe.render.compose.scenes import (
    cleanup_segments,
    concat_segments,
    render_scene_segments,
)
from voxframe.render.compose.segment_cache import SegmentCache
from voxframe.render.compose.transitions import (
    padded_durations,
    plan_transitions,
    total_frames_after,
)
from voxframe.render.encode.probe import FFmpegCapabilities, FFmpegNotFound
from voxframe.render.ffpath import (
    ass_filter,
    needs_staging,
    run_ffmpeg,
    stage_for_filters,
)

__all__ = ["render_from_plan"]

log = structlog.get_logger(__name__)


def _narration_graph(plan: ScenePlan) -> tuple[str, bool]:
    """How the narration is laid under the video, pausing for every card.

    A card adds time to the video that the recording does not have, so the
    speech must wait while it is shown: silence the length of the card, at
    the point it was inserted (D-144). Only cards at the start used to delay
    the audio; after a chapter card mid-video, pictures and captions ran late
    by the card's length -- 2s after one, 4s after two, measured against the
    rendered soundtrack.

    Returns:
        ``(filter, is_graph)``. A simple ``-af`` chain when no card sits
        mid-video, as before; otherwise a complex graph reading ``[1:a]`` and
        ending in ``[narr]``. Both pad the end, so the audio is never clipped
        (D-032).
    """
    pauses = plan.card_pauses()
    lead = sum(seconds for at, seconds in pauses if at <= 1e-6)
    middle = [(at, seconds) for at, seconds in pauses if at > 1e-6]
    delay = f"adelay={round(lead * 1000)}:all=1," if lead > 0 else ""

    if not middle:
        return f"{delay}apad", False

    pieces = len(middle) + 1
    parts = ["[1:a]asplit=" + str(pieces) + "".join(f"[s{i}]" for i in range(pieces))]
    for i in range(pieces):
        start = 0.0 if i == 0 else middle[i - 1][0]
        trim = f"atrim=start={start:.6f}"
        if i < len(middle):
            trim += f":end={middle[i][0]:.6f}"
        pad = f",apad=pad_dur={middle[i][1]:.6f}" if i < len(middle) else ""
        parts.append(f"[s{i}]{trim},asetpts=PTS-STARTPTS{pad}[p{i}]")
    parts.append(
        "".join(f"[p{i}]" for i in range(pieces))
        + f"concat=n={pieces}:v=0:a=1,{delay}apad[narr]"
    )
    return ";".join(parts), True


def _scenes_for_captions(plan: ScenePlan) -> tuple[Scene, ...]:
    """Rebuild caption-bearing scenes from the plan.

    Word timings come from the plan itself (``PlanWord``), so captions
    highlight exactly as precisely on a re-render as on the first render. Any
    correction in ``caption_text`` is applied here, which is what makes editing
    the JSON sufficient — the corrected words inherit the spoken words' timings,
    including when a correction splits or merges words.

    Scenes written by hand with text but no word timings fall back to even
    distribution across the span. That is less precise, but a hand-authored
    scene has no true timings to preserve, so there is nothing being lost.
    """
    scenes: list[Scene] = []

    for planned in plan.scenes:
        if planned.is_card:
            # A card has no captions: its text is drawn into the frame.
            continue

        # Already on the video's clock: inserting cards moves the words with
        # their scenes (D-110). Adding the leading cards' length again here
        # made every titled video's highlights late by the title (D-144).
        words = planned.caption_words()

        if not words:
            tokens = planned.display_text.split()
            if not tokens:
                scenes.append(
                    Scene(
                        index=planned.index,
                        start_frame=planned.start_frame,
                        end_frame=planned.end_frame,
                    )
                )
                continue

            words = _evenly_spaced(
                tokens,
                planned.start_seconds(plan.fps),
                planned.duration_seconds(plan.fps),
            )

        scenes.append(
            Scene(
                index=planned.index,
                start_frame=planned.start_frame,
                end_frame=planned.end_frame,
                # Scene.text is derived from its words, so the corrected text
                # arrives with them and must not be passed separately.
                words=words,
            )
        )

    return tuple(scenes)


def _evenly_spaced(
    tokens: list[str], start: float, duration: float
) -> tuple[Word, ...]:
    """Spread words evenly across a span, for scenes with no real timings."""
    per_word = duration / len(tokens)
    return tuple(
        Word(
            text=token,
            start=start + index * per_word,
            # A small gap keeps consecutive highlights distinct rather than
            # abutting exactly, which reads as smoother.
            end=start + (index + 1) * per_word * 0.95,
        )
        for index, token in enumerate(tokens)
    )


def render_from_plan(
    plan: ScenePlan,
    audio_path: Path,
    style: StyleTemplate,
    caps: FFmpegCapabilities,
    output_path: Path,
    *,
    quality: QualityPreset = QualityPreset.STANDARD,
    music: MusicSettings | None = None,
    cache_dir: Path | None = None,
    height: int = 1080,
    background: str = "0x141824",
    write_sidecars: bool = True,
    keep_segments: bool = False,
) -> RenderResult:
    """Render a video from a scene plan.

    Args:
        plan: The edit decision list. The only source of decisions.
        audio_path: Source audio. Laid in unmodified.
        style: Style template, supplying caption appearance and motion
            intensity.
        caps: Probed FFmpeg capabilities.
        output_path: Where to write the video.
        quality: Quality preset for the final encode.
        height: Output height. Width follows from the plan's aspect ratio.
        background: Colour for scenes with no asset.
        write_sidecars: Also write ``.srt`` and ``.vtt``.
        keep_segments: Leave the intermediate segments on disk, for debugging.

    Returns:
        Paths written and timing measurements.

    Raises:
        RenderError: If the audio is missing, libass is unavailable, or the
            rendered frame count disagrees with the plan.
    """
    if not audio_path.is_file():
        raise RenderError(f"Audio file not found: {audio_path}")
    if not caps.has_libass:
        raise RenderError(
            "This FFmpeg build has no libass, so captions cannot be rendered.\n"
            "Install a full build (Windows: winget install Gyan.FFmpeg)."
        )

    started = time.monotonic()

    width, out_height = _dimensions(plan.aspect, height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / f".{output_path.stem}_segments"

    log.info(
        "render.plan.start",
        scenes=len(plan.scenes),
        matched=plan.matched_scenes,
        frames=plan.total_frames,
        size=f"{width}x{out_height}",
    )

    # --- 1. transitions, decided before rendering ---
    #
    # A crossfade overlaps its two segments, so the outgoing one must be
    # rendered longer by the blend length or the timeline loses those frames
    # (D-097). That has to be known before the segments exist.
    transitions = plan_transitions(
        plan,
        default_seconds=style.motion.transition_seconds,
        # The template's own choice: a style that asks for hard cuts gets them.
        enabled=style.motion.transition is not TransitionKind.CUT,
    )

    scene_frames = [scene.duration_frames for scene in plan.scenes]
    render_frames = padded_durations(scene_frames, transitions)

    # --- 2. scene segments ---
    segments = render_scene_segments(
        plan,
        caps,
        work_dir,
        width=width,
        height=out_height,
        motion=style.motion,
        quality=quality,
        background=background,
        frame_overrides=render_frames,
        cache=SegmentCache(cache_dir) if cache_dir else None,
    )

    # The grid check is against the *scene* durations, not the padded ones:
    # padding exists to be consumed by the overlaps, and the output must still
    # equal what the plan declares (D-025).
    produced = total_frames_after([s.frames for s in segments], transitions)
    if produced != plan.total_frames:
        raise RenderError(
            f"After transitions the video would be {produced} frames but the "
            f"plan declares {plan.total_frames}. Transition padding and "
            f"overlaps disagree."
        )

    # --- 3. concatenate ---
    concatenated = work_dir / "concatenated.mp4"
    concat_segments(segments, caps, concatenated, work_dir, transitions, plan.fps)

    # --- 4. captions and audio, in one pass ---
    ass_path = output_path.with_suffix(".ass")
    caption_scenes = _scenes_for_captions(plan)
    write_ass(ass_path, caption_scenes, style.captions, width, out_height, plan.fps)

    srt_path = vtt_path = None
    if write_sidecars:
        srt_path = write_srt(output_path.with_suffix(".srt"), caption_scenes, plan.fps)
        vtt_path = write_vtt(output_path.with_suffix(".vtt"), caption_scenes, plan.fps)

    try:
        font_directory: Path | None = fonts_dir()
    except FontsMissing as exc:
        log.warning("render.fonts.missing", error=str(exc))
        font_directory = None

    staging: Path | None = None
    if font_directory is not None and needs_staging(ass_path, font_directory):
        staging = output_path.parent / ".voxframe_staging"
        ass_for_filter = stage_for_filters({"captions": ass_path}, staging)["captions"]
    else:
        ass_for_filter = ass_path

    caption_filter, cwd = ass_filter(ass_for_filter, fontsdir=font_directory)

    video_seconds = plan.total_frames / plan.fps
    # The speech waits for every card, and the end is padded so the audio is
    # never clipped (D-032, D-099, D-144).
    narration, is_graph = _narration_graph(plan)
    narration_graph = narration if is_graph else f"[1:a]{narration}[narr]"

    music_inputs: list[str] = []
    audio_arguments: list[str] = (
        ["-filter_complex", narration_graph, "-map", "0:v", "-map", "[narr]"]
        if is_graph
        else ["-af", narration]
    )

    if music is not None:
        if not music.path.is_file():
            raise RenderError(f"Music track not found: {music.path}")

        music_inputs = ["-i", str(music.path.resolve())]

        # The narration keeps its own pauses and padding before the mix, so
        # cards and music compose rather than fighting (D-100).
        chain = (
            f"{narration_graph};"
            + music_filter_chain(
                music,
                video_seconds,
                _music_duration(music.path, caps),
                narration_label="narr",
                music_label="2:a",
            )
        )
        audio_arguments = ["-filter_complex", chain, "-map", "0:v", "-map", "[aout]"]


    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(concatenated.resolve()),
            "-i", str(audio_path.resolve()),
            *music_inputs,
            "-vf", f"{caption_filter},format=yuv420p",
            *audio_arguments,
            "-frames:v", str(plan.total_frames),
            "-t", f"{video_seconds:.6f}",
            "-c:v", "libx264",
            "-crf", str(_CRF[quality]),
            "-preset", _SPEED[quality],
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-y", str(output_path.resolve()),
        ],
        cwd=cwd,
    )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderError(
            f"FFmpeg reported success but {output_path.name} is missing or empty."
        )

    _verify_frame_count(caps, output_path, plan.total_frames)

    if staging is not None:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
    if not keep_segments:
        cleanup_segments(work_dir)

    elapsed = time.monotonic() - started

    result = RenderResult(
        video_path=output_path,
        srt_path=srt_path,
        vtt_path=vtt_path,
        ass_path=ass_path,
        width=width,
        height=out_height,
        fps=plan.fps,
        frame_count=plan.total_frames,
        audio_duration=plan.audio_duration,
        elapsed_seconds=elapsed,
    )

    log.info(
        "render.plan.done",
        output=output_path.name,
        size_kb=output_path.stat().st_size // 1024,
        elapsed=round(elapsed, 2),
        realtime_factor=round(result.realtime_factor, 2),
    )

    return result


def _music_duration(path: Path, caps: FFmpegCapabilities) -> float:
    """Length of a music track, or 0.0 when it cannot be read.

    Zero means "unknown", which makes the length chain trim and pad — correct
    whether the track is long or short, just not optimal.
    """
    from voxframe.render.encode.probe import probe_media

    try:
        return probe_media(path, caps).duration
    except (OSError, FFmpegNotFound) as exc:
        log.warning("render.music.duration_unknown", error=str(exc))
        return 0.0
