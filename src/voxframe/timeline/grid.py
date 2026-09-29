"""The frame grid: the single authority on time-to-frame conversion.

Every boundary in Voxframe is expressed in frames on one global grid. This is
what makes the "no audio drift" property structural rather than aspirational
(see DECISIONS.md D-013).

The failure this prevents
-------------------------
The naive approach rounds each scene's *duration* independently::

    frames = round(scene.duration * fps)      # WRONG

Each scene then carries up to half a frame of error, and the errors accumulate.
Across 400 scenes of a 30-minute video the end can drift by several frames, and
the audio ends up visibly out of sync with the picture by the end.

The fix is to round *absolute positions* rather than durations, and to derive
each duration from the difference between neighbouring absolute boundaries::

    start_frame = round(t * fps)                        # absolute, exact
    duration    = next.start_frame - this.start_frame   # derived, exact

Errors cannot accumulate because no boundary is ever computed relative to
another boundary's rounded value. The final boundary is pinned to the audio
duration, so total video length is anchored to the audio itself rather than to
the sum of its parts.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import ceil

__all__ = ["FrameGrid", "GridError", "Span"]


class GridError(ValueError):
    """Raised when a timeline cannot be represented on the grid."""


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open frame interval ``[start_frame, end_frame)``.

    Half-open is deliberate: one span's ``end_frame`` is exactly the next
    span's ``start_frame``, so spans tile the timeline with neither gaps nor
    double-counted frames.
    """

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        if self.start_frame < 0:
            raise GridError(f"start_frame must be >= 0, got {self.start_frame}")
        if self.end_frame <= self.start_frame:
            raise GridError(
                f"end_frame must exceed start_frame, got [{self.start_frame}, {self.end_frame})"
            )

    @property
    def duration_frames(self) -> int:
        """Length in frames. Always >= 1."""
        return self.end_frame - self.start_frame

    def seconds(self, fps: float) -> tuple[float, float]:
        """Return ``(start, end)`` in seconds. For display and FFmpeg only."""
        return (self.start_frame / fps, self.end_frame / fps)


class FrameGrid:
    """Converts times to frames for one render.

    A single instance is created per job and threaded through every stage that
    needs to know about time. Nothing else in the codebase may call ``round()``
    on a timestamp.

    Args:
        fps: Frames per second. Must be positive.
        audio_duration: Audio length in seconds. Pins the end of the timeline.

    Raises:
        GridError: If ``fps`` or ``audio_duration`` is not positive.
    """

    def __init__(self, fps: float, audio_duration: float) -> None:
        if fps <= 0:
            raise GridError(f"fps must be positive, got {fps}")
        if audio_duration <= 0:
            raise GridError(f"audio_duration must be positive, got {audio_duration}")
        self.fps = fps
        self.audio_duration = audio_duration

    @property
    def total_frames(self) -> int:
        """Total frames in the render, covering the whole audio.

        This is the authoritative length of the output video. The concatenated
        segments must sum to exactly this value.

        Rounded **up**, not to nearest (D-032). Rounding to nearest can produce
        a video marginally shorter than the audio, which forces the renderer to
        either truncate the audio — clipping the speaker's final syllable — or
        leave the streams disagreeing. Rounding up guarantees the video always
        covers the audio, so silence padding is the only adjustment ever
        needed, and padding is inaudible where truncation is not.

        The cost is at most one extra frame, under 33 ms at 30 fps, holding the
        final image.
        """
        return ceil(self.audio_duration * self.fps)

    def frame_at(self, t: float) -> int:
        """Convert an absolute timestamp to an absolute frame number.

        Clamped to ``[0, total_frames]``. Rounds to nearest, so a boundary is
        placed on the frame whose presentation time is closest to ``t``.
        """
        return max(0, min(self.total_frames, round(t * self.fps)))

    def spans_from_boundaries(self, boundaries: list[float]) -> list[Span]:
        """Build a gapless list of spans covering the whole timeline.

        Args:
            boundaries: Scene *start* times in seconds, ascending. A leading
                ``0.0`` is added if absent; the audio end is always the final
                boundary.

        Returns:
            Spans tiling ``[0, total_frames)`` with no gaps or overlaps.

        Raises:
            GridError: If boundaries are not strictly ascending, or if the
                timeline is too short to hold one frame per span.

        Note:
            Boundaries that collapse onto the same frame after rounding are
            merged, since a span must be at least one frame long. Callers
            needing to know which scenes merged should compare lengths.
        """
        if not boundaries:
            return [Span(0, self.total_frames)]

        ordered = sorted(boundaries)
        if ordered != boundaries:
            raise GridError("boundaries must be supplied in ascending order")

        frames = [self.frame_at(t) for t in boundaries]
        if frames[0] != 0:
            frames.insert(0, 0)

        # Collapse duplicates: a span must be >= 1 frame (see docstring note).
        unique: list[int] = []
        for f in frames:
            if not unique or f > unique[-1]:
                unique.append(f)

        if unique[-1] >= self.total_frames:
            unique = [f for f in unique if f < self.total_frames]
        unique.append(self.total_frames)

        if len(unique) < 2:
            raise GridError(
                f"timeline of {self.total_frames} frames cannot hold any span; "
                f"audio may be shorter than one frame at {self.fps} fps"
            )

        return [Span(a, b) for a, b in pairwise(unique)]

    def verify(self, spans: list[Span]) -> None:
        """Assert that spans tile the timeline exactly.

        The invariant behind the no-drift guarantee. Called before rendering
        and again after concatenation.

        Raises:
            GridError: On any gap, overlap, or total-length mismatch.
        """
        if not spans:
            raise GridError("no spans to verify")

        if spans[0].start_frame != 0:
            raise GridError(f"timeline must start at frame 0, got {spans[0].start_frame}")

        for prev, nxt in pairwise(spans):
            if prev.end_frame != nxt.start_frame:
                raise GridError(
                    f"gap or overlap: span ends at {prev.end_frame}, "
                    f"next begins at {nxt.start_frame}"
                )

        if spans[-1].end_frame != self.total_frames:
            raise GridError(
                f"timeline must end at frame {self.total_frames} "
                f"(audio duration {self.audio_duration:.3f}s at {self.fps} fps), "
                f"got {spans[-1].end_frame}"
            )

        total = sum(s.duration_frames for s in spans)
        if total != self.total_frames:
            raise GridError(
                f"span durations sum to {total}, expected {self.total_frames}"
            )
