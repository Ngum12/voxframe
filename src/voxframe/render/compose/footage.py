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

import itertools
import math
from collections.abc import Sequence
from pathlib import Path

import structlog

from voxframe.plan.scene_plan import Footage, TrackPoint
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import filter_path_context, run_ffmpeg

__all__ = [
    "EYE_LINE",
    "crop_window",
    "face_extent",
    "footage_filter_chain",
    "render_footage_segment",
    "track_between",
]

#: Where a followed face sits down a frame that is shorter than the footage:
#: a little above the middle, where a viewer expects eyes to be.
EYE_LINE = 0.40

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


def footage_filter_chain(
    footage: Footage,
    width: int,
    height: int,
    fps: float,
    *,
    start: float = 0.0,
    seconds: float | None = None,
    zoom: str | None = None,
) -> str:
    """The filter chain turning the footage into ``width`` x ``height`` on the grid.

    With a camera path (D-193) and the segment's ``start`` (on the sound's
    clock) and length, the crop moves along the path frame by frame. ``zoom``
    is how close the camera is at each frame (see :func:`zoom_expression`),
    toward the speaker's eye line (D-199).
    """
    scaled_w, scaled_h, x, y = crop_window(footage, width, height)
    crop = f"crop={width}:{height}:{x}:{y}"
    if footage.track and seconds is not None:
        crop = _moving_crop(footage, width, height, scaled_w, scaled_h, start, seconds)
    return ",".join(
        [
            # The grid first, so every later filter sees the video's own frames.
            # It also holds the first frame from the seek point until the
            # picture begins, for a picture that starts after its sound.
            f"fps={fps}",
            f"scale={scaled_w}:{scaled_h}:flags=lanczos",
            crop,
            *([_zoom_filter(zoom, width, height, fps)] if zoom else []),
            "setsar=1",
            # Holds the last frame if the footage ends before the scene does;
            # ``-frames:v`` stops the output at exactly the scene's length.
            "tpad=stop_mode=clone:stop=-1",
        ]
    )


#: How long a punch-in takes to arrive and to leave, in seconds, and how long
#: it holds after its word.
PUNCH_IN, PUNCH_OUT, PUNCH_HOLD = 0.12, 0.2, 0.25


def zoom_expression(base: float, zooms: Sequence[tuple[float, float, float]]) -> str | None:
    """How close the camera is at each frame of a segment: ``base`` (an
    alternate cut's 8% closer), and each punch-in ``(start, end, factor)``, in
    seconds from the segment's start, arriving quickly and easing away.

    ``None`` when the camera never moves in: the chain is then as it was.
    """
    if base == 1.0 and not zooms:
        return None
    t = "(on/FPS)"
    terms = []
    for start, end, factor in zooms:
        rise = f"({t}-{start:.4f})/{PUNCH_IN}"
        fall = f"({end + PUNCH_HOLD + PUNCH_OUT:.4f}-{t})/{PUNCH_OUT}"
        terms.append(f"{factor - 1:.4f}*clip(min({rise},{fall}),0,1)")
    lift = "+".join(terms) if terms else "0"
    return f"{base:.4f}*(1+{lift})"


def _zoom_filter(expression: str, width: int, height: int, fps: float) -> str:
    """Zoom toward the eye line, where the framing put the speaker's eyes."""
    z = expression.replace("FPS", f"{fps:g}")
    return (
        f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih*{EYE_LINE}-ih*{EYE_LINE}/zoom':"
        f"d=1:s={width}x{height}:fps={fps:g}"
    )


def _moving_crop(
    footage: Footage,
    width: int,
    height: int,
    scaled_w: int,
    scaled_h: int,
    start: float,
    seconds: float,
) -> str:
    """A crop whose corner follows the camera path across this segment.

    ``t`` in the expression is the frame's time from the seek point, which is
    the segment's own start. Clamped to the frame, like the still crop.
    """
    points = track_between(footage.track, start, start + seconds)
    local = [(round(p.t - start, 4), p) for p in points]
    x_path = _piecewise([(t, p.x * scaled_w - width / 2) for t, p in local])
    crop_x = f"clip({x_path},0,{scaled_w - width})"
    if scaled_h > height:
        y_path = _piecewise([(t, p.y * scaled_h - EYE_LINE * height) for t, p in local])
        crop_y = f"clip({y_path},0,{scaled_h - height})"
    else:
        crop_y = "0"
    return f"crop={width}:{height}:x='{crop_x}':y='{crop_y}'"


def _piecewise(points: list[tuple[float, float]]) -> str:
    """A linear-between-points function of ``t``, held at both ends."""
    expression = f"{points[-1][1]:.2f}"
    for (t0, v0), (t1, v1) in reversed(list(itertools.pairwise(points))):
        if t1 - t0 <= 1e-6:
            continue
        slope = (v1 - v0) / (t1 - t0)
        expression = f"if(lt(t,{t1:.4f}),{v0:.2f}+(t-{t0:.4f})*{slope:.4f},{expression})"
    return f"if(lt(t,{points[0][0]:.4f}),{points[0][1]:.2f},{expression})"


#: A face detector's box runs from brow to chin; the head, and hair, reach
#: further. Captions keep clear of the box grown by this share of its height.
HEAD_MARGIN = 0.25


def face_extent(
    footage: Footage, width: int, height: int, start: float, seconds: float
) -> tuple[float, float] | None:
    """How far up and down the frame the speaker's head reaches in a scene.

    Follows the same scaling and crop as the render, at every point of the
    camera path across the scene.

    Returns:
        ``(top, bottom)`` in output pixels, the extremes across the scene; or
        ``None`` when the footage has no camera path, so where the face is
        is not known.
    """
    if not footage.track:
        return None
    _, scaled_h, _, still_y = crop_window(footage, width, height)
    top, bottom = float(height), 0.0
    for point in track_between(footage.track, start, start + seconds):
        if point.h <= 0:
            continue
        crop_y = (
            min(max(0.0, point.y * scaled_h - EYE_LINE * height), scaled_h - height)
            if scaled_h > height
            else still_y
        )
        centre = point.y * scaled_h - crop_y
        half = point.h * scaled_h * (0.5 + HEAD_MARGIN)
        top, bottom = min(top, centre - half), max(bottom, centre + half)
    return (top, bottom) if bottom > top else None


def track_between(
    track: Sequence[TrackPoint], start: float, end: float
) -> list[TrackPoint]:
    """The camera path from ``start`` to ``end``: its value at each end, and
    every point between, so a segment's crop moves exactly as the path does."""
    inside = [point for point in track if start < point.t < end]
    return [_track_at(track, start), *inside, _track_at(track, end)]


def _track_at(track: Sequence[TrackPoint], t: float) -> TrackPoint:
    """The path at ``t``: held before its first point and after its last."""
    first, last = track[0], track[-1]
    if t <= first.t:
        return first.model_copy(update={"t": t})
    for before, after in itertools.pairwise(track):
        if before.t <= t <= after.t:
            span = after.t - before.t
            share = 0.0 if span <= 0 else (t - before.t) / span
            return TrackPoint(
                t=t,
                x=before.x + (after.x - before.x) * share,
                y=before.y + (after.y - before.y) * share,
                h=before.h + (after.h - before.h) * share,
            )
    return last.model_copy(update={"t": t})


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
    zoom: str | None = None,
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
            "-vf", footage_filter_chain(
                footage, width, height, fps, start=start, seconds=frames / fps, zoom=zoom
            ),
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
