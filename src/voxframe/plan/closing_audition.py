"""Reviewed endings quote the actual final words without changing the recording."""

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
    "authority": {"label": "Land the point", "note": "One clear final quote and measured framing."},
    "energy": {
        "label": "Last-word punch",
        "note": "Two text beats with emphasis on the final words.",
    },
    "cinema": {
        "label": "Quiet resolve",
        "note": "A restrained quote and cinematic caption pacing.",
    },
}


class ClosingChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{24}$")
    first_word: int = Field(ge=0)
    look: Literal["authority", "energy", "cinema"] = "authority"
    shot: Literal["keep", "speaker", "picture"] = "keep"
    asset_scene: int | None = Field(default=None, ge=0)
    match_captions: bool = True
    replace_pinned: bool = False


def controls(plan: ScenePlan) -> dict:
    timed = tokens(plan)
    eligible = 3 <= plan.total_frames / plan.fps <= 60 + 1e-7
    starts = []
    last = len(timed) - 1
    trailing_untimed = bool(timed) and any(
        not scene.is_card and scene.display_text and not scene.caption_words()
        for scene in plan.scenes[timed[-1].scene + 1 :]
    )
    if eligible and timed and not trailing_untimed:
        for index in range(last, max(-1, last - 12), -1):
            token = timed[index]
            quote = " ".join(t.value.text for t in timed[index:])
            if (
                token.group != timed[last].group
                or timed[last].value.end - token.value.start > 6
                or len(quote) > 96
            ):
                break
            try:
                start, end = _window(plan, Placement(first_word=index, last_word=last))
            except EditError:
                continue
            overlapping = [s for s in plan.scenes if s.start_frame < end and s.end_frame > start]
            starts.append(
                {
                    "first_word": index,
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
            option["first_word"]
            for option in starts
            if option["end"] - option["start"] >= 1
            and (
                option["first_word"] == 0 or END.search(timed[option["first_word"] - 1].value.text)
            )
        ),
        None,
    )
    starts.reverse()
    if default is None and starts:
        close = [option for option in starts if option["end"] - option["start"] <= 3.5]
        default = (close or starts[-1:])[0]["first_word"]
    return {
        "revision": revision(plan),
        "eligible": eligible,
        "starts": starts,
        "default_first_word": default,
        "profiles": PROFILES,
        "visuals": [
            {"scene": s.index, "quote": s.display_text, "credit": s.asset.license_author}
            for s in plan.scenes
            if not s.is_card and s.asset
        ],
    }


def audition(plan: ScenePlan, choice: ClosingChoice) -> tuple[ScenePlan, dict]:
    data = controls(plan)
    option = next(
        (start for start in data["starts"] if start["first_word"] == choice.first_word), None
    )
    if option is None:
        raise EditError(
            "Choose a closing from the final six seconds of spoken words in a 3-60 second story."
        )
    if option["pinned"] and not choice.replace_pinned:
        raise EditError(
            "This closing has pinned text. Allow replacing pinned closing text to audition it."
        )
    if choice.shot != "picture" and choice.asset_scene is not None:
        raise EditError("Choose Picture before selecting a supporting visual.")
    timed = tokens(plan)
    first, last = choice.first_word, len(timed) - 1
    ranges = [(first, last)]
    if choice.look == "energy" and last - first >= 2:
        middle = last - min(2, (last - first + 1) // 2)
        low = math.ceil(timed[middle].value.end * plan.fps - 1e-7)
        high = math.floor(timed[middle + 1].value.start * plan.fps + 1e-7)
        if low <= high:
            ranges = [(first, middle), (middle + 1, last)]
    edits = [
        Placement(
            first_word=a,
            last_word=b,
            shot=choice.shot,
            asset_scene=choice.asset_scene,
            beat=VisualBeat(
                text=" ".join(t.value.text for t in timed[a : b + 1]),
                kind="closing",
                look=choice.look,
                zoom=LOOKS[choice.look]["zoom"] if number or choice.look != "energy" else 1,
            ),
        )
        for number, (a, b) in enumerate(ranges)
    ]
    draft = place(plan, edits)
    start, end = _window(plan, Placement(first_word=first, last_word=last))
    # Placement clears automatic quotes on split scenes. Earlier text belongs
    # to the existing edit and must survive this ending-only audition.
    restored = []
    for scene in draft.scenes:
        if scene.end_frame <= start:
            original = next(
                s for s in plan.scenes if s.start_frame <= scene.start_frame < s.end_frame
            )
            scene = scene.model_copy(update={"visual_beat": original.visual_beat})
        restored.append(scene)
    draft = draft.model_copy(update={"scenes": tuple(restored)})
    if choice.match_captions:
        caption = "impact" if choice.look == "energy" else LOOKS[choice.look]["caption"]
        draft = draft.model_copy(
            update={
                "scenes": tuple(
                    s.model_copy(update={"caption_treatment": CAPTION_PRESETS[caption]})
                    if start <= s.start_frame and s.end_frame <= end
                    else s
                    for s in draft.scenes
                )
            }
        )
    return draft, option
