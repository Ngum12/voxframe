"""The generated score's choice for a video: a style and a seed (D-176).

Kept in the scene plan like every other decision (D-011): the same plan
always gives the same score, and "New variation" is a new seed.
"""

from __future__ import annotations

import secrets

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ScoreChoice", "new_seed"]


def new_seed() -> int:
    """A fresh seed: a new piece in the same style."""
    return secrets.randbelow(2**31)


class ScoreChoice(BaseModel):
    """Score this video, in ``style``, as variation ``seed``."""

    model_config = ConfigDict(frozen=True)

    style: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    seed: int = Field(ge=0, lt=2**31)
    #: Calmer (-1) to more driving (1) than the speech alone would make it;
    #: 0 follows the speech (D-179). Changing it composes the score again.
    intensity: float = Field(default=0.0, ge=-1.0, le=1.0)
