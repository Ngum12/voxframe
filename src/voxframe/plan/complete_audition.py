"""A full current story with a visual direction and an explicit soundtrack."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.visual_director import direct

PROFILES = {
    "authority": {"label": "Clean authority", "arc": "steady", "music_db": -6},
    "energy": {"label": "High energy", "arc": "punch", "music_db": 0},
    "cinema": {"label": "Cinematic story", "arc": "rise", "music_db": -3},
}


class CompleteChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{24}$")
    look: Literal["authority", "energy", "cinema"] | None = None
    match_captions: bool = False
    music_source: Literal["project", "library", "none"] = "project"
    music_library_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    mix: AudioMix


def audition(plan: ScenePlan, choice: CompleteChoice, *, track: str = "",
             credit: str = "") -> ScenePlan:
    if not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7:
        raise EditError("Compose a 3-60 second story in Shorts first.")
    if choice.music_source != "library" and choice.music_library_id is not None:
        raise EditError("Choose Saved track before selecting a music library track.")
    if choice.music_source == "library" and (not choice.music_library_id or not track):
        raise EditError("Choose a saved music track.")
    draft = direct(plan, choice.look, match_captions=choice.match_captions) if choice.look else plan
    changes = {"audio_mix": choice.mix}
    if choice.music_source == "none":
        changes.update(music_path="", music_credit="", score=None)
    elif choice.music_source == "library":
        changes.update(music_path=track, music_credit=credit, score=None)
    return ScenePlan.model_validate({**draft.model_dump(), **changes})
