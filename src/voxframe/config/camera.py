"""A person's chosen camera direction over a still photograph."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CameraMove(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    direction: Literal["in", "out", "left", "right", "up", "down"] = "in"
    strength: float = Field(default=.5, ge=0, le=1)
