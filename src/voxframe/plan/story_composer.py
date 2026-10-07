"""An explicit sequence of transcript passages, with recording clocks intact."""
from __future__ import annotations

import math
import re
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.transitions import TransitionTreatment
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import END, bounds, build_passage, hook_details, revision, tokens


class StoryBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["hook", "keypoint", "payoff", "ending"] = "keypoint"
    first_word: int = Field(ge=0)
    last_word: int = Field(ge=0)


def compose(plan: ScenePlan, blocks: list[StoryBlock], *, vertical: bool = True) -> ScenePlan:
    if not 1 <= len(blocks) <= 8:
        raise EditError("Choose between one and eight story passages.")
    timed = tokens(plan)
    ranges = []
    for block in blocks:
        start, end = bounds(plan, timed, block.first_word, block.last_word)
        if any(block.first_word <= other.last_word and block.last_word >= other.first_word
               for other in blocks[:len(ranges)]):
            raise EditError("Story passages overlap. Trim the boundaries or remove a duplicate.")
        ranges.append((start, end))
    ordered = sorted(range(len(blocks)), key=lambda i: blocks[i].first_word)
    for left, right in pairwise(ordered):
        if ranges[left][1] <= ranges[right][0]:
            continue
        low = math.ceil(timed[blocks[left].last_word].value.end * plan.fps)
        high = math.floor(timed[blocks[right].first_word].value.start * plan.fps)
        if low > high:
            raise EditError("Selected words overlap in time. Adjust the passage boundaries.")
        split = (low + high) // 2
        ranges[left] = (ranges[left][0], split)
        ranges[right] = (split, ranges[right][1])
    scenes = []
    cursor = 0
    for number, block in enumerate(blocks):
        fragment = build_passage(plan, block.first_word, block.last_word, vertical=vertical,
                                 window=ranges[number])
        for scene in fragment.scenes:
            beat = scene.visual_beat
            if beat and beat.source == "director" and beat.text:
                def normalize(text: str) -> str:
                    return " ".join(re.findall(r"\w+", text.casefold()))
                quote = normalize(scene.display_text)
                if f" {normalize(beat.text)} " not in f" {quote} ":
                    beat = beat.model_copy(update={"text": ""})
            scenes.append(scene.model_copy(update={
                "index": len(scenes), "start_frame": scene.start_frame + cursor,
                "end_frame": scene.end_frame + cursor,
                "words": tuple(w.model_copy(update={"start": w.start + cursor / plan.fps,
                                                      "end": w.end + cursor / plan.fps})
                               for w in scene.words),
                "transition_after": TransitionTreatment(kind="cut")
                    if scene == fragment.scenes[-1] else scene.transition_after,
                "visual_beat": beat, "story_role": block.role, "story_block": number,
            }))
        cursor += fragment.total_frames
    seconds = cursor / plan.fps
    if not 3 <= seconds <= 60 + 1e-7:
        raise EditError("Build a story lasting between 3 and 60 seconds.")
    payload = plan.model_dump()
    payload.update(scenes=scenes, total_frames=cursor, audio_duration=seconds,
                   aspect=fragment.aspect)
    return ScenePlan.model_validate(payload)


def controls(plan: ScenePlan) -> dict:
    timed = tokens(plan)
    passages = []
    start = 0
    for index, token in enumerate(timed):
        if (END.search(token.value.text) or index == len(timed) - 1
                or timed[index + 1].group != token.group):
            low, high = bounds(plan, timed, start, index)
            passages.append({"first_word": start, "last_word": index,
                "text": " ".join(t.value.text for t in timed[start:index + 1]),
                "seconds": (high - low) / plan.fps,
                "start": low / plan.fps, **hook_details(timed, start, index)})
            start = index + 1
    # Existing saved blocks take precedence over a fresh, chronological starting point.
    grouped = {}
    for index, token in enumerate(timed):
        scene = plan.scenes[token.scene]
        if scene.story_block is not None:
            grouped.setdefault(scene.story_block, []).append((index, scene.story_role))
    proposal = []
    if grouped:
        proposal = [StoryBlock(role=items[0][1] or "keypoint", first_word=items[0][0],
                               last_word=items[-1][0]).model_dump()
                    for _, items in sorted(grouped.items())]
    else:
        seconds = 0
        for passage in passages:
            if len(proposal) == 6 or seconds + passage["seconds"] > 60:
                break
            proposal.append(StoryBlock(first_word=passage["first_word"],
                                       last_word=passage["last_word"]).model_dump())
            seconds += passage["seconds"]
        for index, block in enumerate(proposal):
            block["role"] = ("hook" if index == 0 else "ending" if index == len(proposal) - 1
                             else "payoff" if index == len(proposal) - 2 else "keypoint")
    return {"revision": revision(plan), "passages": passages, "proposal": proposal,
            "words": [{"index": i, "text": t.value.text, "start": t.value.start,
                       "end": t.value.end} for i, t in enumerate(timed)],
            "note": "The starting sequence follows transcript order. Roles are editable labels; "
                    "review context and meaning when rearranging passages."}
