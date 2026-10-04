"""Caption treatments shared by plans, the studio and the ASS renderer."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from voxframe.config.style import CaptionBacking, CaptionPosition, CaptionStyle


class CaptionAnimation(StrEnum):
    HIGHLIGHT = "highlight"
    KARAOKE = "karaoke"
    POP = "pop"
    TYPEWRITER = "typewriter"
    EMPHASIS = "emphasis"
    PLAIN = "plain"
    SPOTLIGHT = "spotlight"
    PULSE = "pulse"


def ass_color(color: str) -> str:
    return "&H00" + color[5:7] + color[3:5] + color[1:3]


class CaptionTreatment(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    animation: CaptionAnimation = CaptionAnimation.HIGHLIGHT
    position: CaptionPosition = CaptionPosition.BOTTOM
    accent: str = Field(default="#FFD700", pattern=r"^#[0-9a-fA-F]{6}$")
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    size: float = Field(default=1, ge=0.7, le=1.5)
    backing: CaptionBacking = CaptionBacking.BOX
    uppercase: bool = False
    words_per_page: int = Field(default=8, ge=1, le=18)
    max_lines: int = Field(default=2, ge=1, le=3)
    lift: float = Field(default=0, ge=0, le=0.15)
    emphasis_scale: float = Field(default=1.18, ge=1, le=1.35)

    def apply(self, base: CaptionStyle) -> CaptionStyle:
        return base.model_copy(update={
            "primary_color": ass_color(self.color), "highlight_color": ass_color(self.accent),
            "font_size_ratio": min(0.15, base.font_size_ratio * self.size),
            "position": self.position, "backing": self.backing, "caption_box": True,
            "uppercase": self.uppercase, "max_lines": self.max_lines,
            "margin_vertical_ratio": min(0.45, base.margin_vertical_ratio + self.lift),
            "margin_vertical_ratio_wide": min(0.45, base.margin_vertical_ratio_wide + self.lift),
            "highlight_enabled": self.animation != CaptionAnimation.PLAIN,
        })


CAPTION_PRESETS: dict[str, CaptionTreatment] = {
    "classic": CaptionTreatment(),
    "electric": CaptionTreatment(animation=CaptionAnimation.POP, accent="#70F0D0", uppercase=True,
                                 backing=CaptionBacking.OUTLINE, words_per_page=5, size=1.25),
    "cinema": CaptionTreatment(animation=CaptionAnimation.TYPEWRITER, accent="#F5C38B",
                               backing=CaptionBacking.OUTLINE, words_per_page=7, size=0.85),
    "karaoke": CaptionTreatment(animation=CaptionAnimation.KARAOKE, accent="#70F0D0"),
    "impact": CaptionTreatment(animation=CaptionAnimation.EMPHASIS, accent="#FFCE56",
                               words_per_page=5, uppercase=True, size=1.15),
    "spotlight": CaptionTreatment(animation=CaptionAnimation.SPOTLIGHT, accent="#FFD700",
                                  backing=CaptionBacking.OUTLINE, words_per_page=1, size=1.5),
    "pulse": CaptionTreatment(animation=CaptionAnimation.PULSE, accent="#F4A9FF",
                              backing=CaptionBacking.OUTLINE, words_per_page=5, size=1.15),
    "quiet": CaptionTreatment(animation=CaptionAnimation.PLAIN, words_per_page=12, size=0.85),
}
