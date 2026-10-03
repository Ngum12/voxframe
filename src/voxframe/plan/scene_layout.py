"""How a scene shares the frame between the speaker and its picture (D-197).

A scene shows the speaker or its picture (D-192). With a layout it can show
both: a **split** screen, the picture on top and the speaker below, explaining
it (side by side in a landscape frame); or an **inset**, one in a small round
or rounded frame over the other. Kept in the scene plan like every other
decision (D-011).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["InsetShape", "LayoutKind", "SceneLayout"]


class LayoutKind(StrEnum):
    #: One thing fills the frame: the speaker or the picture, as the shot says.
    FULL = "full"
    #: The frame divided: the picture in one part, the speaker in the other.
    SPLIT = "split"
    #: One in a small frame over the other.
    INSET = "inset"


class InsetShape(StrEnum):
    CIRCLE = "circle"
    ROUNDED = "rounded"


class SceneLayout(BaseModel):
    """The layout of one scene. Only the fields of its kind matter."""

    model_config = ConfigDict(frozen=True)

    kind: LayoutKind = LayoutKind.FULL

    #: Split: the share of the frame the picture takes: its height in a
    #: vertical or square frame, its width in a landscape one.
    split: float = Field(default=0.5, ge=0.25, le=0.75)
    #: Split: the speaker on top (or on the left) instead of the picture.
    speaker_first: bool = False
    #: Split: a thin line between the two.
    divider: bool = True

    #: Inset: the speaker in the small frame over the picture; off, the
    #: picture over the speaker.
    inset_speaker: bool = True
    inset_shape: InsetShape = InsetShape.CIRCLE
    #: Inset: its width, as a fraction of the frame's width.
    inset_size: float = Field(default=0.36, ge=0.15, le=0.6)
    #: Inset: where its centre is, as fractions of the frame.
    inset_x: float = Field(default=0.74, ge=0.0, le=1.0)
    inset_y: float = Field(default=0.22, ge=0.0, le=1.0)

    @property
    def is_full(self) -> bool:
        return self.kind is LayoutKind.FULL
