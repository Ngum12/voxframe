"""Render a captioned video from audio and scenes.

Phase 2's deliverable: audio plus a static background plus burned-in captions.
Phase 3 replaces the background with matched imagery and motion, but the
structure here is the final one:

1. Build the video track at the exact frame count the grid demands.
2. Burn captions in one pass through libass.
3. Lay the original audio in once, unmodified.

Step 3 is why captions cannot drift: the audio is never sliced, re-encoded or
resampled, so there is no second clock to fall out of step with. Step 1 is why
the video cannot end early or late.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.assets import FontsMissing, fonts_dir
from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import StyleTemplate
from voxframe.models.scene import Scene
from voxframe.render.captions.ass import write_ass
from voxframe.render.captions.subtitles import write_srt, write_vtt
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import (
    ass_filter,
    needs_staging,
    run_ffmpeg,
    stage_for_filters,
)
from voxframe.timeline.grid import FrameGrid

__all__ = ["RenderError", "RenderResult", "render_captioned_video"]

log = structlog.get_logger(__name__)

#: CRF per quality preset. Lower is better quality and larger files.
_CRF = {
    QualityPreset.DRAFT: 30,
    QualityPreset.STANDARD: 23,
    QualityPreset.HIGH: 20,
    QualityPreset.ULTRA: 17,
}

#: x264 speed preset per quality. Draft favours speed; ultra favours size.
_SPEED = {
    QualityPreset.DRAFT: "veryfast",
    QualityPreset.STANDARD: "medium",
    QualityPreset.HIGH: "slow",
    QualityPreset.ULTRA: "slower",
}


class RenderError(RuntimeError):
    """Raised when a render cannot be completed."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    """What a render produced, and what it cost."""

    video_path: Path
    srt_path: Path | None
    vtt_path: Path | None
    ass_path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    audio_duration: float
    elapsed_seconds: float
    #: What happened to the music, when the person should know (D-170).
    music_note: str = ""
    #: The sound's measurements and checks (D-171), when it was mixed.
    sound: dict[str, object] | None = None
    #: The video without captions, for the studio's live preview (D-196).
    studio_path: Path | None = None

    @property
    def realtime_factor(self) -> float:
        """Render time relative to audio length.

        Below 1.0 means faster than realtime. Reported in phase reports per the
        brief's performance requirement.
        """
        return self.elapsed_seconds / self.audio_duration if self.audio_duration else 0.0


def _dimensions(aspect: AspectRatio, height: int) -> tuple[int, int]:
    """Frame size for an aspect ratio at a given height.

    Both dimensions are forced even: yuv420p subsamples chroma by two, and an
    odd dimension makes encoders fail or silently pad.
    """
    base_width, base_height = aspect.dimensions_1080
    width = round(height * base_width / base_height)
    return (width - (width % 2), height - (height % 2))


def render_captioned_video(
    audio_path: Path,
    scenes: tuple[Scene, ...],
    grid: FrameGrid,
    style: StyleTemplate,
    caps: FFmpegCapabilities,
    output_path: Path,
    *,
    aspect: AspectRatio = AspectRatio.HORIZONTAL,
    quality: QualityPreset = QualityPreset.STANDARD,
    height: int = 1080,
    background: str = "0x141824",
    write_sidecars: bool = True,
) -> RenderResult:
    """Render audio and scenes into a captioned video.

    Args:
        audio_path: Source audio. Copied in unmodified.
        scenes: Scenes to caption.
        grid: The frame grid. Determines the output's exact frame count.
        style: Style template supplying caption appearance.
        caps: Probed FFmpeg capabilities.
        output_path: Where to write the video.
        aspect: Output aspect ratio.
        quality: Quality preset.
        height: Output height in pixels. Width follows from the aspect ratio.
        background: FFmpeg colour for the background.
        write_sidecars: Also write ``.srt`` and ``.vtt`` next to the video.

    Returns:
        Paths written and timing measurements.

    Raises:
        RenderError: If the audio is missing or FFmpeg produces no output.
    """
    if not audio_path.is_file():
        raise RenderError(f"Audio file not found: {audio_path}")
    if not caps.has_libass:
        raise RenderError(
            "This FFmpeg build has no libass, so captions cannot be rendered.\n"
            "Install a full build (Windows: winget install Gyan.FFmpeg)."
        )

    started = time.monotonic()

    # Dimensions are computed once and used for both the video and the ASS
    # PlayRes header. A mismatch would make libass silently scale every font
    # size and margin by the ratio between them.
    width, out_height = _dimensions(aspect, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = output_path.with_suffix(".ass")
    write_ass(ass_path, scenes, style.captions, width, out_height, grid.fps)

    srt_path = vtt_path = None
    if write_sidecars:
        srt_path = write_srt(output_path.with_suffix(".srt"), scenes, grid.fps)
        vtt_path = write_vtt(output_path.with_suffix(".vtt"), scenes, grid.fps)

    # Bundled fonts make rendering identical on every machine. Without them
    # libass falls back to whatever the system has, so captions shift and
    # golden-frame tests compare against machine-dependent output.
    #
    # An invocation has only one working directory, and the subtitle path may
    # need it (D-021). When the font directory *also* needs rescuing — a user
    # whose install path contains an apostrophe — the subtitle is staged into a
    # safe directory so the font path can use the escaped form.
    try:
        font_directory: Path | None = fonts_dir()
    except FontsMissing as exc:
        log.warning("render.fonts.missing", error=str(exc))
        font_directory = None

    staging: Path | None = None
    if font_directory is not None and needs_staging(ass_path, font_directory):
        staging = output_path.parent / ".voxframe_staging"
        ass_path_for_filter = stage_for_filters({"captions": ass_path}, staging)["captions"]
        log.debug("render.staging", reason="multiple paths need a working directory")
    else:
        ass_path_for_filter = ass_path

    caption_filter, cwd = ass_filter(ass_path_for_filter, fontsdir=font_directory)

    # Frame count and end-of-audio policy (D-025, D-032).
    #
    # `-frames:v` pins the video to exactly the grid's count. `-shortest` must
    # NOT appear alongside it: audio rarely ends on a frame boundary, so
    # `-shortest` truncates the video below the grid's count and silently
    # reintroduces the drift D-013 exists to prevent.
    #
    # Since the grid rounds *up*, the video always covers the audio, and the
    # only possible mismatch is a video marginally longer than the audio. Three
    # ways to resolve that, and only one is acceptable:
    #
    #   - Truncate the video: breaks the frame grid (this was D-025).
    #   - Truncate the audio: cuts off the speaker, possibly mid-word. Worst.
    #   - Pad the audio with silence to the frame boundary: costs under 33 ms
    #     of inaudible silence and keeps both invariants.
    #
    # `apad` appends silence indefinitely; `-t` then bounds the stream to the
    # video's exact duration. Nothing is ever removed, so a speaker's final
    # syllable cannot be clipped.
    video_seconds = grid.total_frames / grid.fps

    arguments = [
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c={background}:s={width}x{out_height}:r={grid.fps}",
        "-i", str(audio_path.resolve()),
        "-vf", f"{caption_filter},format=yuv420p",
        "-af", "apad",
        "-frames:v", str(grid.total_frames),
        "-t", f"{video_seconds:.6f}",
        "-c:v", "libx264",
        "-crf", str(_CRF[quality]),
        "-preset", _SPEED[quality],
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", str(output_path.resolve()),
    ]

    log.info(
        "render.start",
        width=width,
        height=out_height,
        fps=grid.fps,
        frames=grid.total_frames,
        scenes=len(scenes),
        quality=quality.value,
    )

    run_ffmpeg(caps.ffmpeg_path, arguments, cwd=cwd)

    if not output_path.exists() or output_path.stat().st_size == 0:
        # FFmpeg can exit zero having written nothing, so success is verified
        # rather than assumed.
        raise RenderError(
            f"FFmpeg reported success but {output_path.name} is missing or empty."
        )

    _verify_frame_count(caps, output_path, grid.total_frames)

    if staging is not None:
        shutil.rmtree(staging, ignore_errors=True)

    elapsed = time.monotonic() - started

    result = RenderResult(
        video_path=output_path,
        srt_path=srt_path,
        vtt_path=vtt_path,
        ass_path=ass_path,
        width=width,
        height=out_height,
        fps=grid.fps,
        frame_count=grid.total_frames,
        audio_duration=grid.audio_duration,
        elapsed_seconds=elapsed,
    )

    log.info(
        "render.done",
        output=output_path.name,
        size_kb=output_path.stat().st_size // 1024,
        elapsed=round(elapsed, 2),
        realtime_factor=round(result.realtime_factor, 2),
    )

    return result


def _verify_frame_count(
    caps: FFmpegCapabilities, video_path: Path, expected: int
) -> None:
    """Check the rendered file holds exactly the frames the grid demanded.

    The frame grid guarantees that scene durations sum to the audio length
    (D-013), but that guarantee covers the *plan*. Nothing stops an FFmpeg
    argument from quietly overriding it: ``-shortest`` combined with
    ``-frames:v`` truncated output by one frame until D-025 removed it, and the
    render reported success throughout.

    Checking the actual file closes the gap between what was planned and what
    was produced.

    Raises:
        RenderError: If the count differs by more than one frame. One frame of
            tolerance covers container-level rounding, which is real and
            harmless; anything larger indicates a genuine problem.
    """
    if caps.ffprobe_path is None:
        log.warning("render.verify.skipped", reason="ffprobe not available")
        return

    result = run_ffmpeg(
        caps.ffprobe_path,
        [
            "-v", "error",
            "-count_frames",
            "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=nw=1:nk=1",
            str(video_path.resolve()),
        ],
        check=False,
    )

    raw = result.stdout.strip()
    if not raw.isdigit():
        log.warning("render.verify.unreadable", output=raw[:80])
        return

    actual = int(raw)
    difference = abs(actual - expected)

    if difference > 1:
        raise RenderError(
            f"Frame count mismatch in {video_path.name}: "
            f"expected {expected} from the frame grid, got {actual} "
            f"(difference {difference}).\n"
            "The video no longer matches the timeline it was planned against. "
            "See DECISIONS.md D-013 and D-025."
        )

    if difference == 1:
        log.debug("render.verify.off_by_one", expected=expected, actual=actual)
    else:
        log.debug("render.verify.exact", frames=actual)
