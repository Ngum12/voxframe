"""Configuration: settings and style templates."""

from voxframe.config.settings import (
    AspectRatio,
    IntermediateFormat,
    QualityPreset,
    Settings,
    get_settings,
)
from voxframe.config.style import (
    BUILTIN_TEMPLATES,
    CaptionPosition,
    CaptionStyle,
    MotionStyle,
    PacingStyle,
    StyleTemplate,
    TransitionKind,
    get_template,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "AspectRatio",
    "CaptionPosition",
    "CaptionStyle",
    "IntermediateFormat",
    "MotionStyle",
    "PacingStyle",
    "QualityPreset",
    "Settings",
    "StyleTemplate",
    "TransitionKind",
    "get_settings",
    "get_template",
]
