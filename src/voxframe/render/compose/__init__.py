"""Composition: building FFmpeg graphs and assembling output."""

from voxframe.render.compose.captioned import (
    RenderError,
    RenderResult,
    render_captioned_video,
)
from voxframe.render.compose.from_plan import render_from_plan
from voxframe.render.compose.scenes import (
    SegmentResult,
    concat_segments,
    render_scene_segments,
)

__all__ = [
    "RenderError",
    "RenderResult",
    "SegmentResult",
    "concat_segments",
    "render_captioned_video",
    "render_from_plan",
    "render_scene_segments",
]
