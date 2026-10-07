"""Pin visuals to spoken ranges without moving the recording's clock."""
from __future__ import annotations

import math
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.transitions import TransitionTreatment
from voxframe.config.visuals import VisualBeat
from voxframe.plan.editing import EditError, _motion_after_new_image
from voxframe.plan.highlights import _rebase_words
from voxframe.plan.scene_plan import PlanWord, ScenePlan, Shot
from voxframe.plan.shorts import bounds, revision, tokens
from voxframe.plan.storyboard import storyboard


class Placement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    first_word: int = Field(ge=0)
    last_word: int = Field(ge=0)
    shot: Literal["keep", "speaker", "picture"] = "keep"
    asset_scene: int | None = Field(default=None, ge=0)
    beat: VisualBeat | None = None


def _window(plan: ScenePlan, edit: Placement) -> tuple[int, int]:
    timed = tokens(plan)
    start, end = bounds(plan, timed, edit.first_word, edit.last_word)
    first, last = timed[edit.first_word], timed[edit.last_word]
    if first.group != last.group:
        raise EditError("Place visuals within a spoken passage, without crossing a card.")
    lower = plan.scenes[first.scene].start_frame
    if edit.first_word and timed[edit.first_word - 1].scene == first.scene:
        lower = max(lower, math.ceil(timed[edit.first_word - 1].value.end * plan.fps - 1e-7))
    upper = math.floor(first.value.start * plan.fps + 1e-7)
    end_lower = math.ceil(last.value.end * plan.fps - 1e-7)
    end_upper = plan.scenes[last.scene].end_frame
    if edit.last_word + 1 < len(timed) and timed[edit.last_word + 1].scene == last.scene:
        end_upper = min(end_upper, math.floor(
            timed[edit.last_word + 1].value.start * plan.fps + 1e-7))
    if lower > upper or end_lower > end_upper:
        raise EditError("These words overlap at the cut. Choose a wider passage.")
    return max(lower, min(start, upper)), max(end_lower, min(end, end_upper))


def place(plan: ScenePlan, edits: list[Placement]) -> ScenePlan:
    if not 1 <= len(edits) <= 12:
        raise EditError("Choose between one and twelve visual placements.")
    if not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7:
        raise EditError("Choose a 3-60 second story in Shorts before placing visuals.")
    ranges = []
    for edit in edits:
        start, end = _window(plan, edit)
        if any(edit.first_word <= other.last_word and edit.last_word >= other.first_word
               for _, _, other in ranges):
            raise EditError("Visual placements overlap. Adjust their boundaries.")
        if edit.shot == "picture":
            if edit.asset_scene is None or edit.asset_scene >= len(plan.scenes):
                raise EditError("Choose a supporting visual already in this project.")
            if plan.scenes[edit.asset_scene].asset is None:
                raise EditError("That scene has no supporting visual. Add one in Scenes first.")
        elif edit.asset_scene is not None:
            raise EditError("Select Picture before choosing a supporting visual.")
        if edit.shot == "speaker" and (plan.footage is None or any(
            s.footage_start is None for s in plan.scenes
            if s.start_frame < end and s.end_frame > start)):
            raise EditError("This passage has no speaker footage. Use a video recording.")
        if edit.shot == "keep" and edit.beat is None:
            raise EditError("Choose a shot or enable a text treatment for this placement.")
        ranges.append((start, end, edit))
    timed = tokens(plan)
    ranges.sort(key=lambda item: item[0])
    for i in range(len(ranges) - 1):
        a, b, left = ranges[i]
        c, d, right = ranges[i + 1]
        if b > c:
            low = math.ceil(timed[left.last_word].value.end * plan.fps)
            high = math.floor(timed[right.first_word].value.start * plan.fps)
            if low > high:
                raise EditError("These words overlap at the cut. Choose a wider passage.")
            split = (low + high) // 2
            ranges[i] = (a, split, left)
            ranges[i + 1] = (split, d, right)
    scenes = []
    placement_boundaries = {point for start, end, _ in ranges for point in (start, end)}
    for scene in plan.scenes:
        cuts = sorted({scene.start_frame, scene.end_frame, *(point for a, b, _ in ranges
            for point in (a, b) if scene.start_frame < point < scene.end_frame)})
        displayed = tuple(PlanWord.from_word(w) for w in scene.caption_words())
        for a, b in pairwise(cuts):
            chosen = next((edit for start, end, edit in ranges if start <= a and b <= end), None)
            changes = {"index": len(scenes)}
            source = scene.audio_start
            if source is None:
                source = max(0, scene.start_frame / plan.fps
                             - plan.card_seconds_before(scene.index))
            offset = (a - scene.start_frame) / plan.fps
            if not scene.is_card:
                changes["audio_start"] = source + offset
            if len(cuts) > 2:
                selected = [(i, w) for i, w in enumerate(displayed)
                            if a / plan.fps <= (w.start + w.end) / 2 < b / plan.fps]
                words = _rebase_words(tuple(w for _, w in selected), source_start=a / plan.fps,
                                      source_end=b / plan.fps, destination_start=a / plan.fps)
                spoken = " ".join(w.text for w in words)
                raw = " ".join(w.text for w in scene.words
                    if a / plan.fps <= (w.start + w.end) / 2 < b / plan.fps)
                changes.update(start_frame=a, end_frame=b, words=words,
                    text=raw if scene.is_corrected else spoken,
                    caption_text=spoken if scene.is_corrected else "",
                    caption_emphasis=tuple(n for n, (i, _) in enumerate(selected)
                                           if i in scene.caption_emphasis),
                    audio_start=source + offset, director_join=False)
                if scene.footage_start is not None:
                    changes["footage_start"] = scene.footage_start + offset
                if b != scene.end_frame:
                    changes["transition_after"] = TransitionTreatment(kind="cut")
                # Automatic quotes spanning a new boundary must not linger.
                if scene.visual_beat and scene.visual_beat.source == "director":
                    changes["visual_beat"] = scene.visual_beat.model_copy(update={"text": ""})
            if chosen:
                if chosen.shot != "keep":
                    changes.update(shot=Shot(chosen.shot), shot_source="user",
                                   shot_reason="placed on this passage by you")
                if chosen.shot == "picture":
                    asset = plan.scenes[chosen.asset_scene].asset
                    changes.update(asset=asset, asset_source="user")
                    if scene.asset is None or scene.asset.id != asset.id:
                        alternatives = scene.alternatives
                        if scene.asset is not None:
                            alternatives = (scene.asset.model_copy(update={
                                "similarity": scene.semantic_score or None}), *alternatives)
                        changes.update(camera_move=None, alternatives=alternatives,
                            semantic_score=0, match_score=0, match_reason="placed project visual",
                            **_motion_after_new_image(scene, "placed project visual"))
                if chosen.beat is not None:
                    changes["visual_beat"] = chosen.beat.model_copy(update={"source": "user"})
            if b in placement_boundaries and b != plan.total_frames:
                changes.update(transition_after=TransitionTreatment(kind="cut"),
                               director_join=False)
            scenes.append(scene.model_copy(update=changes))
    return ScenePlan.model_validate({**plan.model_dump(), "scenes": scenes})


def controls(plan: ScenePlan) -> dict:
    return {"revision": revision(plan), "has_speaker": plan.footage is not None,
            "words": [{"index": i, "text": t.value.text, "start": t.value.start,
                       "end": t.value.end} for i, t in enumerate(tokens(plan))],
            "visuals": [{"scene": s.index, "quote": s.display_text, "id": s.asset.id,
                         "credit": s.asset.license_author} for s in plan.scenes
                        if not s.is_card and s.asset], "storyboard": storyboard(plan)}
