"""A reversible portrait delivery choice; source editing and mix levels stay intact."""
from voxframe.config.settings import AspectRatio
from voxframe.config.short_export import PRESETS, ShortExport
from voxframe.plan.audio_mix import Destination
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision


def configure(plan: ScenePlan, settings: ShortExport | None) -> ScenePlan:
    if settings is None:
        return plan.model_copy(update={"short_export": None})
    if not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7:
        raise EditError("Choose a 3-60 second passage in Shorts before choosing an export preset.")
    destination = (Destination.WHATSAPP if settings.platform == "whatsapp" else
                   Destination.YOUTUBE if settings.platform == "youtube" else Destination.SOCIAL)
    return plan.model_copy(update={"short_export": settings, "aspect": AspectRatio.VERTICAL,
        "audio_mix": plan.audio_mix.model_copy(update={"destination": destination})})


def controls(plan: ScenePlan) -> dict:
    return {"revision": revision(plan), "settings": plan.short_export,
            "presets": PRESETS, "seconds": plan.total_frames / plan.fps,
            "note": "Guides reserve space for app controls. Adjust them for your device; "
                    "they are not burned into the video."}
