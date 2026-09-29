"""The scene plan: the editable edit-decision list the renderer consumes."""

from voxframe.plan.builder import build_plan
from voxframe.plan.caption_edit import (
    CorrectionResult,
    EditOperation,
    apply_caption_correction,
)
from voxframe.plan.highlights import (
    HighlightSelection,
    build_highlights_plan,
    select_highlights,
)
from voxframe.plan.scene_plan import (
    PLAN_VERSION,
    MotionKind,
    PlanAsset,
    PlanError,
    PlannedScene,
    PlanWord,
    QuerySource,
    ScenePlan,
)

__all__ = [
    "PLAN_VERSION",
    "CorrectionResult",
    "EditOperation",
    "HighlightSelection",
    "MotionKind",
    "PlanAsset",
    "PlanError",
    "PlanWord",
    "PlannedScene",
    "QuerySource",
    "ScenePlan",
    "apply_caption_correction",
    "build_highlights_plan",
    "build_plan",
    "select_highlights",
]
