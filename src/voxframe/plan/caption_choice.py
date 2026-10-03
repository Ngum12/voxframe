"""A person's choices for a video's captions, over its template's (D-196).

Kept in the scene plan like every other decision (D-011), so the studio's
live preview and the finished video are drawn from the same record: what is
chosen here is what both show.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.style import CaptionAnimation, CaptionStyle, CaptionTransition

__all__ = ["CaptionChoice"]


class CaptionChoice(BaseModel):
    """What a person changed about this video's captions. Anything left
    ``None`` is the template's."""

    model_config = ConfigDict(frozen=True)

    animation: CaptionAnimation | None = None
    transition: CaptionTransition | None = None
    #: Where the middle of the captions sits, as a fraction of the frame's
    #: height, when they have been moved.
    anchor_y: float | None = Field(default=None, ge=0.05, le=0.95)
    #: Larger or smaller than the template's size.
    size: float = Field(default=1.0, ge=0.6, le=1.8)
    uppercase: bool | None = None

    @property
    def is_default(self) -> bool:
        return self == CaptionChoice()

    def apply(self, style: CaptionStyle) -> CaptionStyle:
        """The template's caption style, with these choices made."""
        changes: dict[str, object] = {}
        if self.animation is not None:
            changes["animation"] = self.animation
            # Choosing an animation is choosing to have one.
            changes["highlight_enabled"] = self.animation is not CaptionAnimation.PLAIN
        if self.transition is not None:
            changes["transition"] = self.transition
        if self.anchor_y is not None:
            changes["anchor_y"] = self.anchor_y
        if self.size != 1.0:
            changes["font_size_ratio"] = min(0.2, style.font_size_ratio * self.size)
        if self.uppercase is not None:
            changes["uppercase"] = self.uppercase
        return style.model_copy(update=changes) if changes else style
