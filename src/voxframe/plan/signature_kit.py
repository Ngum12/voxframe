"""Apply portable styling without copying content or changing source clocks."""
from voxframe.config.creative_presets import CreativeSettings
from voxframe.plan.scene_plan import ScenePlan


def apply_kit(plan: ScenePlan, settings: CreativeSettings) -> ScenePlan:
    scenes = []
    for scene in plan.scenes:
        changes = {}
        if not scene.card_kind:
            # A selected kit deliberately replaces caption overrides, not words.
            changes["caption_treatment"] = None
            if settings.camera_move is not None and scene.motion.value != "none":
                changes["camera_move"] = settings.camera_move
            if settings.beat_style is not None and scene.visual_beat is not None:
                changes["visual_beat"] = scene.visual_beat.model_copy(
                    update=settings.beat_style.model_dump())
        if settings.transition_treatment is not None:
            changes["transition_after"] = None
        scenes.append(scene.model_copy(update=changes))
    changes = {"scenes": tuple(scenes), "caption_treatment": settings.caption_treatment,
               "audio_mix": settings.audio_mix}
    if settings.transition_treatment is not None:
        changes["transition_treatment"] = settings.transition_treatment
    return plan.model_copy(update=changes)
