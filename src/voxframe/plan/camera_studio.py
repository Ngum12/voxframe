"""Photo camera choices, shared by saving and the read-only preview."""
from voxframe.config.camera import CameraMove
from voxframe.plan.editing import EditError, set_motion
from voxframe.plan.scene_plan import ScenePlan


def configure(plan: ScenePlan, index: int, on: bool, settings: CameraMove | None) -> ScenePlan:
    updated = set_motion(plan, index, on)
    scene = updated.scenes[index]
    if plan.shows_speaker(scene):
        raise EditError("Choose Picture in Scenes to direct movement over this photo.")
    scenes = list(updated.scenes)
    scenes[index] = scene.model_copy(update={"camera_move": settings})
    return updated.model_copy(update={"scenes": tuple(scenes)})
