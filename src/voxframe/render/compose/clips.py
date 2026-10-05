"""Render a video clip into a scene segment.

A clip differs from a still in four ways that all matter:

**It has audio, which must go.** The narration is the soundtrack. A clip's own
audio — wind, traffic, a stranger talking — would fight it. ``-an`` drops the
stream entirely rather than muting it, so nothing survives the concat.

**It has its own length, which rarely matches the scene.** Scene durations come
from the speech, clip durations from whatever the contributor uploaded. The
segment must be exactly ``scene.duration_frames`` on the global frame grid
(D-013), so a clip is trimmed when long and stretched when short.

**It supplies its own motion.** Ken Burns over a moving clip is two competing
camera moves. Clips get no zoompan (D-085).

**It has its own aspect.** A 16:9 clip in a 9:16 render must be cropped, not
letterboxed or squashed — the same `scale`/`crop` discipline stills already use.

The interesting decision is what to do with a clip **shorter** than its scene.
See :func:`_short_clip_filter`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import filter_path_context, run_ffmpeg

__all__ = ["ShortClipStrategy", "clip_filter_chain", "render_clip_segment"]

log = structlog.get_logger(__name__)

#: Beyond this, slowing a clip to fill its scene looks wrong rather than
#: deliberate: people move at visibly impossible speeds and motion blur
#: smears. Measured by eye on real clips, not derived.
MAX_SLOWDOWN = 2.5

#: Below this, a speed change is not worth its cost in re-timing: a clip within
#: a few percent of the scene is simply trimmed.
MIN_SPEED_CHANGE = 0.02


@dataclass(frozen=True, slots=True)
class ShortClipStrategy:
    """How a clip shorter than its scene was made to fit.

    Attributes:
        kind: ``trim`` when no stretching was needed, ``slow`` when the clip
            was slowed, ``slow_and_hold`` when slowing alone was not enough and
            the final frame is held.
        speed: The ``setpts`` multiplier applied. 1.0 means unchanged.
        held_seconds: How long the last frame is frozen, 0.0 when none.
        reason: Why this was chosen, recorded in the log and the phase report.
    """

    kind: str
    speed: float = 1.0
    held_seconds: float = 0.0
    reason: str = ""


def plan_short_clip(clip_seconds: float, scene_seconds: float) -> ShortClipStrategy:
    """Decide how to cover a scene with the clip available.

    **Never loops.** A visible loop is the single most recognisable mark of
    cheap automated video: the eye catches the jump instantly, and it reads as
    a fault rather than a style. Every option below is chosen to avoid it.

    The order is:

    1. **Clip is long enough** — trim it. Nothing to decide.
    2. **Slightly short** — slow it down. Up to :data:`MAX_SLOWDOWN`, a slowed
       clip reads as a deliberate slow-motion shot, which suits narration.
    3. **Far too short** — slow it to the limit, then hold the final frame.
       A held frame is a still, and the preceding scenes are full of stills, so
       it is consistent with the rest of the video in a way a loop never is.

    Returns:
        The chosen strategy, with its reason recorded.
    """
    if clip_seconds <= 0 or scene_seconds <= 0:
        return ShortClipStrategy("trim", reason="unknown clip duration")

    if clip_seconds >= scene_seconds * (1 - MIN_SPEED_CHANGE):
        return ShortClipStrategy(
            "trim", reason=f"clip {clip_seconds:.1f}s covers scene {scene_seconds:.1f}s"
        )

    needed = scene_seconds / clip_seconds

    if needed <= MAX_SLOWDOWN:
        return ShortClipStrategy(
            "slow",
            speed=needed,
            reason=(
                f"clip {clip_seconds:.1f}s slowed {needed:.2f}x to cover "
                f"{scene_seconds:.1f}s"
            ),
        )

    # Slow as far as it stays believable, then freeze. Holding a frame is
    # visually the same as the stills elsewhere in the video; looping is not.
    stretched = clip_seconds * MAX_SLOWDOWN
    return ShortClipStrategy(
        "slow_and_hold",
        speed=MAX_SLOWDOWN,
        held_seconds=scene_seconds - stretched,
        reason=(
            f"clip {clip_seconds:.1f}s is far shorter than {scene_seconds:.1f}s; "
            f"slowed {MAX_SLOWDOWN}x then holding the last frame for "
            f"{scene_seconds - stretched:.1f}s rather than looping"
        ),
    )


def clip_filter_chain(
    width: int,
    height: int,
    fps: float,
    strategy: ShortClipStrategy,
) -> str:
    """The filter chain for one clip.

    Scale-then-crop rather than `scale=w:h`, which would squash a clip whose
    aspect differs from the output. `increase` fills the frame and the crop
    takes the centre, matching how stills are framed.
    """
    # Rebase timestamps before resampling: downloaded media may start late or
    # use a variable frame rate. -frames:v is only a maximum, not a guarantee
    # that the source supplies that many frames.
    parts = [
        (f"setpts={strategy.speed:.6f}*(PTS-STARTPTS)"
         if strategy.speed > 1.0 else "setpts=PTS-STARTPTS"),
        # Preserve even a single decoded frame at fractional output rates.
        f"fps={fps}:start_time=0:eof_action=pass",
        # EOF can arrive early even when duration metadata says the clip fits.
        # Clone only as needed; the output frame limit trims every strategy to
        # the exact slot. Never loop the clip or add black frames.
        "tpad=stop_mode=clone:stop=-1",
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        "setsar=1",
    ]

    return ",".join(parts)


def render_clip_segment(
    caps: FFmpegCapabilities,
    clip_path: Path,
    output: Path,
    *,
    width: int,
    height: int,
    fps: float,
    frames: int,
    clip_seconds: float | None,
    intermediate_args: Sequence[str],
) -> ShortClipStrategy:
    """Render one clip to a segment of exactly ``frames`` frames.

    Args:
        caps: Probed FFmpeg capabilities.
        clip_path: The downloaded clip.
        output: Segment to write.
        width: Output frame width.
        height: Output frame height.
        fps: Output frame rate.
        frames: Exact frame count required by the grid (D-013).
        clip_seconds: Clip length, when known from the plan.
        intermediate_args: Encoder arguments shared with still segments.

    Returns:
        The strategy used, for logging and reporting.
    """
    scene_seconds = frames / fps
    strategy = plan_short_clip(clip_seconds or 0.0, scene_seconds)

    chain = clip_filter_chain(width, height, fps, strategy)
    fp = filter_path_context(clip_path)

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", fp.name if fp.cwd else str(clip_path.resolve()),
            "-vf", chain,
            # The narration is the soundtrack; the clip's own audio would
            # fight it. Dropped rather than muted so nothing survives concat.
            "-an",
            "-frames:v", str(frames),
            "-r", str(fps),
            *intermediate_args,
            "-y", str(output.resolve()),
        ],
        cwd=fp.cwd,
    )

    log.info(
        "render.clip",
        clip=clip_path.name,
        frames=frames,
        strategy=strategy.kind,
        speed=round(strategy.speed, 2),
        held=round(strategy.held_seconds, 2),
        reason=strategy.reason,
    )

    return strategy


def clip_download_budget(
    scene_count: int, clip_ratio: float, max_clip_mb: int
) -> int:
    """How many clips a render should fetch.

    Clips are an order of magnitude larger than stills, so the count is capped
    by the configured ratio rather than fetching one per scene and discarding
    most of them.
    """
    return max(1, math.ceil(scene_count * max(0.0, min(1.0, clip_ratio))))
