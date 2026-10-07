"""Audition two disjoint spoken treatments as one complete story edit."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voxframe.plan import closing_audition as closing
from voxframe.plan import opening_audition as opening
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision


class BookendChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: str = Field(pattern=r"^[a-f0-9]{24}$")
    last_word: int = Field(ge=0)
    first_word: int = Field(ge=0)
    opening_look: Literal["authority", "energy", "cinema"] = "authority"
    closing_look: Literal["authority", "energy", "cinema"] = "authority"
    opening_shot: Literal["keep", "speaker"] = "keep"
    closing_shot: Literal["keep", "speaker"] = "keep"
    match_captions: bool = True
    replace_opening: bool = False
    replace_closing: bool = False


def controls(plan: ScenePlan) -> dict:
    first, last = opening.controls(plan), closing.controls(plan)
    pairs = [
        (a, b)
        for a in first["endings"]
        for b in last["starts"]
        if a["last_word"] < b["first_word"] and a["end"] <= b["start"]
    ]
    preferred = next(
        (
            pair
            for pair in pairs
            if pair[0]["last_word"] == first["default_last_word"]
            and pair[1]["first_word"] == last["default_first_word"]
        ),
        None,
    )
    if preferred is None and pairs:
        # Prefer the closest valid pair to the individual sentence defaults.
        preferred = min(
            pairs,
            key=lambda pair: (
                abs(pair[0]["last_word"] - (first["default_last_word"] or 0))
                + abs(pair[1]["first_word"] - (last["default_first_word"] or 0))
            ),
        )
    return {
        "revision": revision(plan),
        "eligible": first["eligible"],
        "opening": first,
        "closing": last,
        "default_last_word": preferred[0]["last_word"] if preferred else None,
        "default_first_word": preferred[1]["first_word"] if preferred else None,
    }


def audition(plan: ScenePlan, choice: BookendChoice) -> tuple[ScenePlan, dict, dict]:
    if choice.revision != revision(plan):
        raise EditError("This story changed. Reopen bookend auditions.")
    start_choice = opening.OpeningChoice(
        revision=choice.revision,
        last_word=choice.last_word,
        look=choice.opening_look,
        shot=choice.opening_shot,
        match_captions=choice.match_captions,
        replace_pinned=choice.replace_opening,
    )
    end_choice = closing.ClosingChoice(
        revision=choice.revision,
        first_word=choice.first_word,
        look=choice.closing_look,
        shot=choice.closing_shot,
        match_captions=choice.match_captions,
        replace_pinned=choice.replace_closing,
    )
    # Validate both against the original: new opening beats must not grant
    # permission to replace pre-existing pinned closing text.
    begun, start = opening.audition(plan, start_choice)
    _, end = closing.audition(plan, end_choice)
    if choice.last_word >= choice.first_word or start["end"] > end["start"]:
        raise EditError("Opening and closing overlap. Choose shorter, separate phrases.")
    # Opening placement clears automatic quotes on split scenes. Restore the
    # original treatment outside the selected opening before adding the closing.
    restored = []
    for scene in begun.scenes:
        if scene.start_frame / plan.fps >= start["end"]:
            original = next(
                s for s in plan.scenes if s.start_frame <= scene.start_frame < s.end_frame
            )
            scene = scene.model_copy(update={"visual_beat": original.visual_beat})
        restored.append(scene)
    begun = begun.model_copy(update={"scenes": tuple(restored)})
    draft, _ = closing.audition(begun, end_choice)
    return draft, start, end
