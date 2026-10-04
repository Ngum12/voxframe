"""Conservative, editable composition guides for short portrait exports."""
from typing import Literal

from pydantic import BaseModel, Field

from voxframe.config.style import CaptionPosition, CaptionStyle

Platform = Literal["youtube", "tiktok", "reels", "whatsapp"]


class SafeArea(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    top: float = Field(default=.10, ge=.04, le=.25)
    bottom: float = Field(default=.22, ge=.08, le=.35)
    left: float = Field(default=.06, ge=.04, le=.25)
    right: float = Field(default=.18, ge=.04, le=.25)


class ShortExport(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    platform: Platform = "youtube"
    height: Literal[1280, 1920] = 1920
    safe_area: SafeArea = Field(default_factory=SafeArea)
    progress: bool = True
    accent: str = Field(default="#70F0D0", pattern=r"^#[0-9a-fA-F]{6}$")


PRESETS = {
    "youtube": {"label": "YouTube Shorts", "export": ShortExport()},
    "tiktok": {"label": "TikTok", "export": ShortExport(platform="tiktok",
        safe_area=SafeArea(top=.12, bottom=.25, right=.20))},
    "reels": {"label": "Instagram Reels", "export": ShortExport(platform="reels",
        safe_area=SafeArea(top=.12, bottom=.24, right=.18))},
    "whatsapp": {"label": "WhatsApp Status", "export": ShortExport(platform="whatsapp",
        height=1280, safe_area=SafeArea(top=.12, bottom=.14, right=.06))},
}


def safe_caption_style(style: CaptionStyle, area: SafeArea, *, progress: bool = False,
                       top: bool = False) -> CaptionStyle:
    """Fit every caption look, including emphasis, inside the text guides."""
    position = CaptionPosition.TOP if top else style.position
    upper = area.top + (.035 if progress else .01)
    lower = area.bottom + .01
    span = (2 * min(.5 - upper, .5 - lower) if position == CaptionPosition.CENTER
            else 1 - upper - lower)
    ratio = min(style.font_size_ratio, span / (style.max_lines * 2.7))
    margin = max(style.margin_vertical_ratio,
                 upper if position == CaptionPosition.TOP else lower)
    if position != CaptionPosition.CENTER:
        opposite = lower if position == CaptionPosition.TOP else upper
        margin = min(margin, 1 - opposite - ratio * style.max_lines * 1.8)
    return style.model_copy(update={
        "position": position, "font_size_ratio": ratio,
        "margin_horizontal_ratio": max(style.margin_horizontal_ratio,
                                        area.left + .01, area.right + .01),
        "margin_vertical_ratio": min(.45, margin),
        "margin_vertical_ratio_wide": min(.45, margin),
    })
