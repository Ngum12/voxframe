"""Alternative openings on real words, with the rest of the saved story intact."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.visuals import LOOKS, VisualBeat
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import END, revision, tokens
from voxframe.plan.visual_placement import Placement, _window, place

PROFILES = {
    "authority": {
        "label": "Quiet confidence",
        "note": "One clear quote, steady framing and clean captions.",
    },
    "energy": {
        "label": "Word punch",
        "note": "Two short text beats and a tighter second speaker shot.",
    },
    "cinema": {
        "label": "Slow reveal",
        "note": "One restrained quote with cinematic caption pacing.",
    },
}


class OpeningChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{24}$")
    last_word: int = Field(ge=0)
    look: Literal["authority", "energy", "cinema"] = "authority"
    shot: Literal["keep", "speaker", "picture"] = "keep"
    asset_scene: int | None = Field(default=None, ge=0)
    match_captions: bool = True
    replace_pinned: bool = False


def controls(plan: ScenePlan) -> dict:
    timed = tokens(plan)
    eligible = 3 <= plan.total_frames / plan.fps <= 60 + 1e-7
    endings = []
    if eligible and timed:
        for index, token in enumerate(timed[:12]):
            quote = " ".join(t.value.text for t in timed[: index + 1])
            if (
                token.group != timed[0].group
                or token.value.end - timed[0].value.start > 6
                or len(quote) > 96
            ):
                break
            try:
                start, end = _window(plan, Placement(first_word=0, last_word=index))
            except EditError:
                continue
            overlapping = [s for s in plan.scenes if s.start_frame < end and s.end_frame > start]
            endings.append(
                {
                    "last_word": index,
                    "quote": quote,
                    "start": start / plan.fps,
                    "end": end / plan.fps,
                    "pinned": any(
                        s.visual_beat and s.visual_beat.source == "user" for s in overlapping
                    ),
                    "has_speaker": plan.footage is not None
                    and all(s.footage_start is not None for s in overlapping),
                }
            )
    default = next(
        (
            option["last_word"]
            for option in endings
            if END.search(option["quote"]) and option["end"] - option["start"] >= 1
        ),
        None,
    )
    if default is None and endings:
        close = [option for option in endings if option["end"] - option["start"] <= 3.5]
        default = (close or endings[:1])[-1]["last_word"]
    return {
        "revision": revision(plan),
        "eligible": eligible,
        "endings": endings,
        "default_last_word": default,
        "profiles": PROFILES,
        "visuals": [
            {"scene": s.index, "quote": s.display_text, "credit": s.asset.license_author}
            for s in plan.scenes
            if not s.is_card and s.asset
        ],
    }


def audition(plan: ScenePlan, choice: OpeningChoice) -> tuple[ScenePlan, dict]:
    data = controls(plan)
    option = next(
        (ending for ending in data["endings"] if ending["last_word"] == choice.last_word), None
    )
    if option is None:
        raise EditError(
            "Choose an opening from the first six seconds of spoken words in a 3-60 second story."
        )
    if option["pinned"] and not choice.replace_pinned:
        raise EditError(
            "This opening has pinned text. Allow replacing pinned opening text to audition it."
        )
    if choice.shot != "picture" and choice.asset_scene is not None:
        raise EditError("Choose Picture before selecting a supporting visual.")
    timed = tokens(plan)
    ranges = [(0, choice.last_word)]
    if choice.look == "energy" and choice.last_word >= 2:
        middle = min(2, choice.last_word // 2)
        low = math.ceil(timed[middle].value.end * plan.fps - 1e-7)
        high = math.floor(timed[middle + 1].value.start * plan.fps + 1e-7)
        if low <= high:
            ranges = [(0, middle), (middle + 1, choice.last_word)]
    edits = []
    for number, (first, last) in enumerate(ranges):
        quoted = " ".join(t.value.text for t in timed[first : last + 1])
        edits.append(
            Placement(
                first_word=first,
                last_word=last,
                shot=choice.shot,
                asset_scene=choice.asset_scene,
                beat=VisualBeat(
                    text=quoted,
                    kind="opening",
                    look=choice.look,
                    zoom=LOOKS[choice.look]["zoom"] if number or choice.look != "energy" else 1,
                ),
            )
        )
    draft = place(plan, edits)
    if choice.match_captions:
        start, end = _window(plan, Placement(first_word=0, last_word=choice.last_word))
        draft = draft.model_copy(
            update={
                "scenes": tuple(
                    s.model_copy(
                        update={"caption_treatment": CAPTION_PRESETS[LOOKS[choice.look]["caption"]]}
                    )
                    if start <= s.start_frame and s.end_frame <= end
                    else s
                    for s in draft.scenes
                )
            }
        )
    return draft, option
