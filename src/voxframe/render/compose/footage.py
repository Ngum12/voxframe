"""Render a scene from the recording's own picture, in sync to the frame (D-192).

A speaker shot is a window into the footage: the frames that were filmed while
the scene's words were spoken. Three things have to be exactly right, because
a face makes every error visible in a way a photograph never does:

**Sync.** A scene's ``footage_start`` is on the sound's clock, the clock the
word timings and the soundtrack count from. FFmpeg seeks the picture from the
file's start, which can be earlier than its first sound, so the seek adds the
footage's ``audio_offset``. Seeking before the input (``-ss`` then ``-i``) is
exact when re-encoding: FFmpeg decodes from the keyframe before and discards
up to the requested time.

**The frame grid.** The footage's own frame rate, often variable on a phone,
is resampled to the video's with the ``fps`` filter, and exactly ``frames``
frames are written (D-013). A scene followed by a crossfade asks for more
frames than it lasts (D-097); the footage simply carries on, which is what an
editor's handle is. Past the end of the footage the last frame is held.

**Framing.** The footage is scaled to cover the output and cropped, never
squashed or letterboxed, with the crop placed on the speaker (``subject_x``)
rather than the middle, and kept inside the frame.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import structlog

from voxframe.plan.scene_plan import Footage
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import filter_path_context, run_ffmpeg

__all__ = ["crop_window", "footage_filter_chain", "render_footage_segment"]

log = structlog.get_logger(__name__)


def _even(value: float) -> int:
    """Rounded up to an even number: H.264 in 4:2:0 needs even sizes."""
    return max(2, math.ceil(value / 2) * 2)


def crop_window(footage: Footage, width: int, height: int) -> tuple[int, int, int, int]:
    """How the footage is scaled and where it is cropped to fill the output.

    Returns:
        ``(scaled_width, scaled_height, x, y)``: the size the footage is scaled
        to so it covers ``width`` x ``height``, and the crop's top-left corner.
    """
    scale = max(width / footage.width, height / footage.height)
    scaled_w = max(width, _even(footage.width * scale))
    scaled_h = max(height, _even(footage.height * scale))
    # Centred on the speaker, but never past an edge: a crop that ran off the
    # frame would show black.
    x = round(footage.subject_x * scaled_w - width / 2)
    x = min(max(0, x), scaled_w - width)
    # Vertically, a little above the middle: the eyes, not the chest, belong
    # at the centre of a tall frame. Matters only when the output is wider.
    y = round(0.42 * scaled_h - height / 2)
    y = min(max(0, y), scaled_h - height)
    return scaled_w, scaled_h, x, y


def footage_filter_chain(footage: Footage, width: int, height: int, fps: float) -> str:
    """The filter chain turning the footage into ``width`` x ``height`` on the grid."""
    scaled_w, scaled_h, x, y = crop_window(footage, width, height)
    return ",".join(
        [
            # The grid first, so every later filter sees the video's own frames.
            # It also holds the first frame from the seek point until the
            # picture begins, for a picture that starts after its sound.
            f"fps={fps}",
            f"scale={scaled_w}:{scaled_h}:flags=lanczos",
            f"crop={width}:{height}:{x}:{y}",
            "setsar=1",
            # Holds the last frame if the footage ends before the scene does;
            # ``-frames:v`` stops the output at exactly the scene's length.
            "tpad=stop_mode=clone:stop=-1",
        ]
    )


def render_footage_segment(
    caps: FFmpegCapabilities,
    footage: Footage,
    start: float,
    output: Path,
    *,
    width: int,
    height: int,
    fps: float,
    frames: int,
    intermediate_args: Sequence[str],
) -> None:
    """Render ``frames`` frames of footage, from ``start`` on the sound's clock.

    Args:
        caps: Probed FFmpeg capabilities.
        footage: The recording's picture.
        start: Where the scene starts, in seconds on the sound's clock.
        output: Segment to write.
        width: Output frame width.
        height: Output frame height.
        fps: The video's frame rate.
        frames: Exact frame count required by the grid (D-013).
        intermediate_args: Encoder arguments shared with every other segment.
    """
    source = Path(footage.path)
    seek = start + footage.audio_offset
    fp = filter_path_context(source)
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-ss", f"{seek:.6f}",
            "-i", fp.name if fp.cwd else str(source.resolve()),
            "-vf", footage_filter_chain(footage, width, height, fps),
            # The recording's sound is laid in once, for the whole video.
            "-an", "-sn", "-dn",
            "-frames:v", str(frames),
            "-r", str(fps),
            *intermediate_args,
            "-y", str(output.resolve()),
        ],
        cwd=fp.cwd,
    )
    log.info("render.footage", start=round(start, 3), frames=frames, source=source.name)
