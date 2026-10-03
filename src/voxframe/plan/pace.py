"""The hook and the pace: cuts, a cold open and punch-ins (D-199).

The plan keeps the whole recording, as it was made. What is cut is a list
beside it, so every cut can be seen, and undone on its own, at any time; and
the video is a projection of the two (``plan/projection.py``).

All times here are on the plan's own clock: the recording's, plus any cards
(D-144), before anything is cut.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["Cut", "CutKind", "PaceEdits", "PunchIn"]


class CutKind(StrEnum):
    #: A pause longer than the pace allows: trimmed, not removed.
    SILENCE = "silence"
    #: "um", "uh" and their kind.
    FILLER = "filler"
    #: A word or phrase said twice in a row: the first is cut.
    REPEAT = "repeat"
    #: The silence before the first word and after the last.
    EDGE = "edge"
    #: Chosen by a person.
    MANUAL = "manual"


class Cut(BaseModel):
    """A stretch of the recording left out of the video."""

    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: CutKind
    #: Off, it is shown but not made: the person undid it.
    on: bool = True
    #: What it removes, for people: "um", "0.9 s pause", "the the".
    label: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def _forwards(self) -> Cut:
        if self.end <= self.start:
            raise ValueError("a cut must end after it starts")
        return self

    @property
    def seconds(self) -> float:
        return self.end - self.start


class PunchIn(BaseModel):
    """A zoom in on the speaker for a stressed word, and back out."""

    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    #: How far in: 1.15 is 15% closer.
    zoom: float = Field(default=1.15, ge=1.02, le=1.6)
    #: The word it is for, for people.
    word: str = Field(default="", max_length=40)
    on: bool = True


class PaceEdits(BaseModel):
    """Everything that changes the video's timing rather than its look."""

    model_config = ConfigDict(frozen=True)

    cuts: tuple[Cut, ...] = Field(default=())
    #: A stretch of the recording played first, before the video begins: the
    #: line that hooks the viewer. It plays again in its place.
    cold_open: tuple[float, float] | None = Field(default=None)
    punch_ins: tuple[PunchIn, ...] = Field(default=())
    #: Zoom the speaker in a little after each cut inside a scene, and back out
    #: after the next, as editors do, so a jump cut reads as a cut and not a
    #: glitch.
    alternate_zoom: bool = True

    @model_validator(mode="after")
    def _cold_open_forwards(self) -> PaceEdits:
        if self.cold_open is not None and self.cold_open[1] <= self.cold_open[0]:
            raise ValueError("the cold open must end after it starts")
        return self

    @property
    def active(self) -> bool:
        """Whether the video differs from the recording in its timing."""
        return any(cut.on for cut in self.cuts) or self.cold_open is not None or any(
            punch.on for punch in self.punch_ins
        )

    def merged_cuts(self) -> list[tuple[float, float]]:
        """The cuts that are on, overlapping ones joined, in order."""
        spans = sorted((cut.start, cut.end) for cut in self.cuts if cut.on)
        merged: list[tuple[float, float]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 1e-6:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged
