"""Ken Burns pans and zooms over stills.

Two problems make the naive implementation look amateurish, and both are
addressed here:

**Repetition.** A slow zoom-in on every scene reads as a template within about
thirty seconds. Moves are therefore varied deterministically from the scene
index and the asset id, so the sequence looks composed rather than random, and
a re-render of the same plan produces identical output.

**Dead space.** Zooming into the centre of an image often lands on sky or
background. A saliency estimate biases the move toward the subject.

The zoompan jitter problem (D-015)
----------------------------------
FFmpeg's ``zoompan`` computes pan offsets with integer pixel rounding. On a slow
zoom this produces visible stepping: the image holds still for several frames,
jumps a pixel, holds again.

The fix used here is **oversampling**: render at a larger internal scale, do the
move there, then downscale to the output size. A sub-pixel move at output scale
becomes a whole-pixel move at the internal scale, so the rounding that caused
stepping now lands between output pixels and is resolved by the downscale
filter. ``test_zoom_smoothness`` measures this against real frames.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["KenBurnsMove", "MotionDirection", "plan_move", "zoompan_filter"]


class MotionDirection(StrEnum):
    """Where the camera travels."""

    IN = "in"
    OUT = "out"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    IN_LEFT = "in_left"
    IN_RIGHT = "in_right"
    OUT_LEFT = "out_left"
    OUT_RIGHT = "out_right"


#: Ordered so consecutive scenes get contrasting moves: an in-zoom followed by
#: an out-zoom reads as deliberate, two in-zooms read as a template.
_ROTATION: tuple[MotionDirection, ...] = (
    MotionDirection.IN,
    MotionDirection.OUT_RIGHT,
    MotionDirection.LEFT,
    MotionDirection.IN_RIGHT,
    MotionDirection.OUT,
    MotionDirection.RIGHT,
    MotionDirection.IN_LEFT,
    MotionDirection.DOWN,
    MotionDirection.OUT_LEFT,
    MotionDirection.UP,
)

#: Internal render scale for the oversampling fix (D-015). 2x turns a
#: half-pixel output move into a whole-pixel internal one. Higher values cost
#: memory and time for no visible gain.
OVERSAMPLE = 2


@dataclass(frozen=True, slots=True)
class KenBurnsMove:
    """A planned camera move.

    Attributes:
        direction: Which way the camera travels.
        start_zoom: Zoom at the first frame. 1.0 is the full image.
        end_zoom: Zoom at the last frame.
        start_center: ``(x, y)`` focal point at the start, in ``[0, 1]``.
        end_center: Focal point at the end.
        duration_frames: How long the move lasts.
    """

    direction: MotionDirection
    start_zoom: float
    end_zoom: float
    start_center: tuple[float, float]
    end_center: tuple[float, float]
    duration_frames: int

    @property
    def zoom_range(self) -> float:
        return abs(self.end_zoom - self.start_zoom)

    @property
    def pan_distance(self) -> float:
        """Total travel of the focal point, in image widths."""
        dx = self.end_center[0] - self.start_center[0]
        dy = self.end_center[1] - self.start_center[1]
        distance: float = (dx**2 + dy**2) ** 0.5
        return distance


def _stable_index(scene_index: int, asset_id: str) -> int:
    """A deterministic index for choosing a move.

    Derived from both the position and the asset, so the same plan renders
    identically while two scenes using the same image still differ.
    """
    digest = hashlib.sha256(f"{scene_index}/{asset_id}".encode()).digest()
    return digest[0]


def plan_move(
    scene_index: int,
    asset_id: str,
    duration_frames: int,
    *,
    intensity: float = 0.5,
    subject_center: tuple[float, float] | None = None,
) -> KenBurnsMove:
    """Choose a camera move for one scene.

    Args:
        scene_index: Position in the sequence, used to vary the move.
        asset_id: The asset being shown, mixed into the variation.
        duration_frames: Length of the scene.
        intensity: 0 is static, 1 is maximum travel. Scales zoom and pan
            together so a style's motion reads consistently.
        subject_center: Where the subject sits, in ``[0, 1]``. Defaults to the
            centre when saliency is unavailable.

    Returns:
        The planned move.
    """
    intensity = max(0.0, min(1.0, intensity))

    # Rotate through directions by position, perturbed by the asset so that
    # repeated images do not move identically.
    offset = _stable_index(scene_index, asset_id) % 3
    direction = _ROTATION[(scene_index + offset) % len(_ROTATION)]

    # Keep zoom modest: beyond about 15% the crop starts to soften on
    # ordinary-resolution images, which reads as low quality.
    zoom_span = 0.04 + 0.11 * intensity
    pan_span = 0.05 + 0.15 * intensity

    focus_x, focus_y = subject_center or (0.5, 0.5)
    # Keep the focal point away from the edge, or the crop hits the boundary
    # and the move stalls against it.
    focus_x = max(0.3, min(0.7, focus_x))
    focus_y = max(0.3, min(0.7, focus_y))

    start_zoom = end_zoom = 1.0
    start = end = (focus_x, focus_y)

    if direction in (MotionDirection.IN, MotionDirection.IN_LEFT, MotionDirection.IN_RIGHT):
        start_zoom, end_zoom = 1.0, 1.0 + zoom_span
    elif direction in (
        MotionDirection.OUT,
        MotionDirection.OUT_LEFT,
        MotionDirection.OUT_RIGHT,
    ):
        start_zoom, end_zoom = 1.0 + zoom_span, 1.0

    horizontal = {
        MotionDirection.LEFT: -1.0,
        MotionDirection.RIGHT: 1.0,
        MotionDirection.IN_LEFT: -0.6,
        MotionDirection.IN_RIGHT: 0.6,
        MotionDirection.OUT_LEFT: -0.6,
        MotionDirection.OUT_RIGHT: 0.6,
    }.get(direction, 0.0)

    vertical = {
        MotionDirection.UP: -1.0,
        MotionDirection.DOWN: 1.0,
    }.get(direction, 0.0)

    if horizontal or vertical:
        half = pan_span / 2
        start = (focus_x - horizontal * half, focus_y - vertical * half)
        end = (focus_x + horizontal * half, focus_y + vertical * half)

    return KenBurnsMove(
        direction=direction,
        start_zoom=round(start_zoom, 4),
        end_zoom=round(end_zoom, 4),
        start_center=(round(start[0], 4), round(start[1], 4)),
        end_center=(round(end[0], 4), round(end[1], 4)),
        duration_frames=duration_frames,
    )


def zoompan_filter(
    move: KenBurnsMove,
    output_width: int,
    output_height: int,
    fps: float,
    *,
    oversample: int = OVERSAMPLE,
) -> str:
    """Build the FFmpeg filter chain for a move.

    Args:
        move: The planned move.
        output_width: Final frame width.
        output_height: Final frame height.
        fps: Output frame rate.
        oversample: Internal scale factor. ``1`` disables the smoothness fix,
            which ``test_zoom_smoothness`` uses to demonstrate the difference.

    Returns:
        A filter chain string.

    Note:
        The chain scales up, runs ``zoompan`` at the larger size, then scales
        down. ``zoompan`` rounds pan offsets to whole pixels, so doing the move
        at 2x makes those steps half an output pixel, which the downscale
        resolves into smooth motion (D-015).
    """
    frames = max(1, move.duration_frames)
    internal_width = output_width * oversample
    internal_height = output_height * oversample

    # A linear ramp in `on` (output frame number) over the scene's length.
    progress = f"(on/{max(1, frames - 1)})"

    zoom = f"{move.start_zoom}+({move.end_zoom}-{move.start_zoom})*{progress}"

    # zoompan's x and y are the top-left corner of the crop, so the focal point
    # is converted by subtracting half the visible extent.
    center_x = f"({move.start_center[0]}+({move.end_center[0]}-{move.start_center[0]})*{progress})"
    center_y = f"({move.start_center[1]}+({move.end_center[1]}-{move.start_center[1]})*{progress})"

    x_expression = f"iw*{center_x}-(iw/zoom/2)"
    y_expression = f"ih*{center_y}-(ih/zoom/2)"

    return (
        # Cover the frame, then crop: an image of a different aspect ratio must
        # fill the output rather than letterbox.
        f"scale={internal_width}:{internal_height}:force_original_aspect_ratio=increase,"
        f"crop={internal_width}:{internal_height},"
        f"zoompan=z='{zoom}':x='{x_expression}':y='{y_expression}'"
        f":d={frames}:s={internal_width}x{internal_height}:fps={fps},"
        f"scale={output_width}:{output_height}:flags=lanczos,"
        # Each scale above preserves *display* aspect by adjusting the sample
        # aspect ratio, so a photo whose proportions do not divide evenly ends a
        # hair off square -- 1843200:1843417 on a real one. The frame is
        # exactly the output size, so square pixels are simply correct, and
        # concat refuses inputs whose SAR differs (D-125).
        "setsar=1"
    )
