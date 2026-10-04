"""One undoable transition change on an outgoing scene or the whole video."""
from __future__ import annotations

from voxframe.config.transitions import TransitionTreatment
from voxframe.plan.editing import EditError, _replace, _scene
from voxframe.plan.scene_plan import ScenePlan


def set_transition(plan: ScenePlan, index: int, treatment: TransitionTreatment | None,
                   *, all_joins: bool = False) -> ScenePlan:
    scene = _scene(plan, index)
    if index >= len(plan.scenes) - 1:
        raise EditError("The last scene has no following join.")
    if all_joins:
        return plan.model_copy(update={"transition_treatment": treatment,
            "scenes": tuple(s.model_copy(update={"transition_after": None}) for s in plan.scenes)})
    return _replace(plan, scene.model_copy(update={"transition_after": treatment}))
