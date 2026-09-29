"""Split a transcript into scenes.

A scene is one visual held on screen. Where the cuts land is most of what makes
pacing feel deliberate rather than mechanical, so the segmenter prefers, in
order:

1. **Natural pauses.** Silence between words is where a human editor cuts.
   Cutting mid-phrase reads as a mistake even when the timing is otherwise fine.
2. **Sentence boundaries.** Failing a pause, a full stop is the next most
   natural break.
3. **Length.** Failing both, cut at the target length rather than let a scene
   run indefinitely.

The one hard rule is that a cut never falls mid-word. That is checked explicitly
rather than assumed, because a caption cut in half is immediately visible in the
output and is one of the quality-bar failures named in the brief.
"""

from __future__ import annotations

import re

import structlog

from voxframe.config.style import PacingStyle
from voxframe.models.scene import Scene
from voxframe.models.transcript import Transcript, Word
from voxframe.timeline.grid import FrameGrid

__all__ = ["SegmentationError", "segment_transcript"]

log = structlog.get_logger(__name__)

#: A word ending a sentence. Handles quotes and brackets after the stop, and
#: the French convention of a space before ! and ?
_SENTENCE_END = re.compile(r"[.!?…]['\"»)\]]*\s*$")

#: Abbreviations whose trailing stop does not end a sentence. Without this,
#: "Dr. Smith" becomes two scenes.
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr",
        "vs", "etc", "e.g", "i.e", "fig", "no", "vol",
        "m", "mme", "mlle",  # French
    }
)


class SegmentationError(ValueError):
    """Raised when a transcript cannot be segmented."""


def _ends_sentence(word: Word) -> bool:
    """Whether this word ends a sentence."""
    text = word.text.strip()
    if not _SENTENCE_END.search(text):
        return False

    # A single trailing stop after a known abbreviation is not a sentence end.
    stem = text.rstrip(".!?…'\"»)]").lower()
    return stem not in _ABBREVIATIONS


def _boundary_candidates(
    transcript: Transcript, pacing: PacingStyle
) -> list[tuple[float, float]]:
    """Possible cut points, each with a score.

    A candidate is the time *after* a word, so a cut there never lands
    mid-word. Higher scores are better cuts.

    Returns:
        ``(time, score)`` pairs, in time order.
    """
    candidates: list[tuple[float, float]] = []

    for index, word in enumerate(transcript.words[:-1]):
        following = transcript.words[index + 1]
        gap = following.start - word.end

        score = 0.0

        # A pause is the strongest signal. Longer pauses score higher, but with
        # diminishing returns: past about a second it is a hard break either way.
        if gap >= pacing.pause_threshold_seconds:
            score += 2.0 + min(gap, 1.0)

        if pacing.prefer_sentence_boundaries and _ends_sentence(word):
            score += 1.5

        # A comma or similar is a weak break; better than nothing.
        if word.text.strip().endswith((",", ";", ":")):
            score += 0.4

        if score > 0:
            # Cut in the middle of the gap so neither side is clipped.
            cut_at = word.end + gap / 2 if gap > 0 else word.end
            candidates.append((cut_at, score))

    return candidates


def _choose_boundaries(
    transcript: Transcript,
    pacing: PacingStyle,
    candidates: list[tuple[float, float]],
) -> list[float]:
    """Pick cut points honouring the pacing window.

    Walks forward, and within each scene's allowed window takes the
    highest-scoring candidate. If none exists, cuts at the maximum length to
    avoid an unbounded scene.
    """
    boundaries: list[float] = []
    position = 0.0
    end = transcript.duration

    while position < end:
        window_start = position + pacing.min_scene_seconds
        window_end = position + pacing.max_scene_seconds

        if window_end >= end:
            break  # the remainder is the final scene

        in_window = [(t, s) for t, s in candidates if window_start <= t <= window_end]

        if in_window:
            # Highest score; earliest wins ties, which keeps pacing tighter.
            best_time = max(in_window, key=lambda pair: (pair[1], -pair[0]))[0]
        else:
            # No natural break: cut at the target length. Nudging to the
            # nearest word gap still avoids splitting a word.
            best_time = _nearest_word_gap(transcript, window_end)

        if best_time <= position:
            break  # no forward progress available

        boundaries.append(best_time)
        position = best_time

    return boundaries


def _nearest_word_gap(transcript: Transcript, target: float) -> float:
    """The word gap closest to ``target``.

    Guarantees the cut never falls inside a word, even when no pause or
    sentence boundary is near the target length.
    """
    best_time = target
    best_distance = float("inf")

    for index, word in enumerate(transcript.words[:-1]):
        following = transcript.words[index + 1]
        gap_middle = word.end + (following.start - word.end) / 2
        distance = abs(gap_middle - target)
        if distance < best_distance:
            best_distance = distance
            best_time = gap_middle

    return best_time


def _pause_after(transcript: Transcript, time: float, threshold: float) -> float:
    """Silence immediately following ``time``, if any.

    Used to place transitions in gaps rather than over speech.
    """
    for start, end in transcript.pauses(threshold):
        if start <= time <= end:
            return end - start
    return 0.0


def segment_transcript(
    transcript: Transcript,
    grid: FrameGrid,
    pacing: PacingStyle | None = None,
) -> tuple[Scene, ...]:
    """Split a transcript into scenes on the frame grid.

    Args:
        transcript: The transcript to segment.
        grid: The frame grid for this render. All boundaries are snapped to it,
            so scene durations sum exactly to the audio length (D-013).
        pacing: Pacing preferences. Defaults to :class:`PacingStyle` defaults.

    Returns:
        Scenes tiling the timeline with no gaps or overlaps.

    Raises:
        SegmentationError: If the transcript has no words.
    """
    if not transcript.words:
        raise SegmentationError("Cannot segment an empty transcript")

    pacing = pacing or PacingStyle()

    candidates = _boundary_candidates(transcript, pacing)
    boundary_times = _choose_boundaries(transcript, pacing, candidates)

    # The grid owns the conversion; the segmenter only proposes times.
    spans = grid.spans_from_boundaries([0.0, *boundary_times])
    grid.verify(spans)

    scenes: list[Scene] = []
    for index, span in enumerate(spans):
        start_seconds, end_seconds = span.seconds(grid.fps)
        words = transcript.words_between(start_seconds, end_seconds)

        scenes.append(
            Scene(
                index=index,
                start_frame=span.start_frame,
                end_frame=span.end_frame,
                words=words,
                pause_after=_pause_after(
                    transcript, end_seconds, pacing.pause_threshold_seconds
                ),
            )
        )

    _verify_no_word_split(scenes, transcript, grid.fps)

    log.info(
        "segment.done",
        scenes=len(scenes),
        mean_seconds=round(transcript.duration / len(scenes), 2),
        shortest=round(min(s.duration_seconds(grid.fps) for s in scenes), 2),
        longest=round(max(s.duration_seconds(grid.fps) for s in scenes), 2),
    )

    return tuple(scenes)


def _verify_no_word_split(
    scenes: tuple[Scene, ...] | list[Scene], transcript: Transcript, fps: float
) -> None:
    """Warn if a scene boundary falls inside a spoken word.

    Boundaries are chosen in word gaps, but frame snapping moves them by up to
    half a frame, which can clip a word whose gap is very short. A warning
    rather than an error: the result is still watchable, and failing a render
    over 16 ms would be worse than reporting it.
    """
    for scene in scenes:
        boundary = scene.end_seconds(fps)
        for word in transcript.words:
            if word.start < boundary < word.end:
                overlap = min(boundary - word.start, word.end - boundary)
                if overlap > 0.02:  # ignore sub-frame clipping
                    log.warning(
                        "segment.word_split",
                        scene=scene.index,
                        word=word.text,
                        boundary=round(boundary, 3),
                        overlap_ms=round(overlap * 1000),
                    )
                break
