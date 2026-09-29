"""Domain models for transcription output.

These are the contract between the transcriber and everything downstream.
Word-level timing is the load-bearing part: caption highlighting, scene
boundaries and transition placement all derive from it, so words carry their
own start and end times rather than being interpolated from segment spans.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

__all__ = ["Transcript", "Word"]


class Word(BaseModel):
    """A single spoken word with its timing.

    Times are seconds from the start of the audio, as reported by the
    transcriber. They are converted to frames only through
    :class:`~voxframe.timeline.FrameGrid` (D-013).
    """

    model_config = {"frozen": True}

    text: str = Field(description="The word, including attached punctuation.")
    start: float = Field(ge=0, description="Start time in seconds.")
    end: float = Field(ge=0, description="End time in seconds.")
    probability: float = Field(
        default=1.0, ge=0, le=1, description="Transcriber confidence."
    )

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end < self.start:
            raise ValueError(
                f"word {self.text!r} ends ({self.end}) before it starts ({self.start})"
            )
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def stripped(self) -> str:
        """The word without surrounding punctuation or whitespace.

        Used for matching and emphasis decisions, where punctuation would
        otherwise cause false negatives.
        """
        return self.text.strip().strip(".,!?;:\"'()[]{}—–-…")  # noqa: RUF001


class Transcript(BaseModel):
    """A complete transcription with word-level timing.

    Attributes:
        words: Every word in order.
        language: Detected or forced language code, e.g. ``"en"``.
        language_probability: Confidence in detection; 1.0 when forced.
        duration: Audio length in seconds, from the decoder.
        model_id: Which model produced this, recorded so a cached transcript
            from a different model is not silently reused.
        audio_sha256: Hash of the source audio, the cache key.
    """

    model_config = {"frozen": True}

    words: tuple[Word, ...]
    language: str
    language_probability: float = Field(default=1.0, ge=0, le=1)
    duration: float = Field(gt=0)
    model_id: str
    audio_sha256: str

    @model_validator(mode="after")
    def _words_are_ordered(self) -> Self:
        """Reject out-of-order words.

        Downstream code assumes chronological order for segmentation and
        caption timing; an unordered transcript would produce captions that
        jump backwards, which is confusing to debug from the output alone.
        """
        for previous, current in zip(self.words, self.words[1:], strict=False):
            if current.start < previous.start:
                raise ValueError(
                    f"words out of order: {previous.text!r} starts at "
                    f"{previous.start}, but {current.text!r} starts at {current.start}"
                )
        return self

    @property
    def text(self) -> str:
        """The full transcript as a single string."""
        return " ".join(word.text.strip() for word in self.words if word.text.strip())

    @property
    def word_count(self) -> int:
        return len(self.words)

    def words_between(self, start: float, end: float) -> tuple[Word, ...]:
        """Words whose midpoint falls within ``[start, end)``.

        Midpoint rather than overlap: a word straddling a boundary belongs to
        exactly one scene, which prevents it being captioned twice.
        """
        return tuple(w for w in self.words if start <= (w.start + w.end) / 2 < end)

    def pauses(self, threshold: float) -> tuple[tuple[float, float], ...]:
        """Gaps between consecutive words of at least ``threshold`` seconds.

        These are the natural breathing points a human editor cuts on.

        Returns:
            ``(start, end)`` pairs, in order.
        """
        gaps: list[tuple[float, float]] = []
        for previous, current in zip(self.words, self.words[1:], strict=False):
            if current.start - previous.end >= threshold:
                gaps.append((previous.end, current.start))
        return tuple(gaps)
