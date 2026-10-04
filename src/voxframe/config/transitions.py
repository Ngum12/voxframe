"""Saved visual transitions; sound and word clocks are never changed."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from voxframe.config.style import TransitionKind


class TransitionDirection(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


class TransitionTreatment(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    kind: TransitionKind = TransitionKind.CROSSFADE
    seconds: float = Field(default=.4, ge=0, le=1.5)
    direction: TransitionDirection = TransitionDirection.LEFT


TRANSITION_PRESETS = {
    "clean": TransitionTreatment(kind=TransitionKind.CUT, seconds=0),
    "gentle": TransitionTreatment(seconds=.4),
    "cinematic": TransitionTreatment(kind=TransitionKind.DIP_TO_BLACK, seconds=.6),
    "slide": TransitionTreatment(kind=TransitionKind.SLIDE, seconds=.4),
    "push": TransitionTreatment(kind=TransitionKind.PUSH, seconds=.5),
    "zoom": TransitionTreatment(kind=TransitionKind.ZOOM, seconds=.4),
    "soft": TransitionTreatment(kind=TransitionKind.SOFT_BLUR, seconds=.5),
}
