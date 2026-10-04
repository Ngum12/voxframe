"""Reviewable transcript-gap cuts, with an explicit original recording clock.

A gap in transcription is a suggestion, never proof of silence. No timed word
is removed; the person listens and chooses before anything changes. Full plans
in edit history restore cuts along with captions, imagery and source positions.
"""
from __future__ import annotations

import math
from itertools import pairwise

from voxframe.config.transitions import TransitionTreatment
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import PlannedScene, ScenePlan


def suggest_cuts(plan: ScenePlan) -> list[dict]:
    """Offer interior gaps of at least a second, leaving breathing room."""
    cuts = []
    groups: list[list[PlannedScene]] = [[]]
    for scene in plan.scenes:
        # Never cut across cards or captions whose corrected alignment is unknown.
        if scene.is_card or scene.is_corrected:
            groups.append([])
        else:
            groups[-1].append(scene)
    for group in groups:
        timed = [(scene, word) for scene in group for word in scene.words]
        for (left_scene, left), (right_scene, right) in pairwise(timed):
            if right.start - left.end < 1.0:
                continue
            start = max(left_scene.start_frame + 1, math.ceil((left.end + .18) * plan.fps))
            end = min(right_scene.end_frame - 1, math.floor((right.start - .18) * plan.fps))
            if end <= start or any(w.start < end / plan.fps and w.end > start / plan.fps
                                   for _, w in timed):
                continue
            cuts.append({"id": f"{left_scene.index}:{start}:{end}", "scene": left_scene.index,
                         "start_frame": start, "end_frame": end,
                         "start": start / plan.fps, "end": end / plan.fps,
                         "seconds": (end - start) / plan.fps,
                         "before": left.text, "after": right.text})
    return cuts


def apply_cuts(plan: ScenePlan, ids: tuple[str, ...]) -> ScenePlan:
    """Split retained intervals and move words, voice and footage together."""
    candidates = {cut["id"]: cut for cut in suggest_cuts(plan)}
    if not ids or len(set(ids)) != len(ids) or any(key not in candidates for key in ids):
        raise EditError("These pause suggestions changed. Reload pacing and choose again.")
    by_scene: dict[int, list[dict]] = {}
    for key in ids:
        cut = candidates[key]
        for scene in plan.scenes:
            first = max(cut["start_frame"], scene.start_frame)
            last = min(cut["end_frame"], scene.end_frame)
            if last > first:
                by_scene.setdefault(scene.index, []).append(
                    {"start_frame": first, "end_frame": last})
    result: list[PlannedScene] = []
    cursor = 0
    removed = 0
    for scene in plan.scenes:
        cuts = sorted(by_scene.get(scene.index, []), key=lambda cut: cut["start_frame"])
        intervals = []
        start = scene.start_frame
        for cut in cuts:
            if cut["start_frame"] < start:
                raise EditError("Pause suggestions overlap; reload pacing.")
            if cut["start_frame"] > start:
                intervals.append((start, cut["start_frame"]))
            start = cut["end_frame"]
            removed += cut["end_frame"] - cut["start_frame"]
        if start < scene.end_frame:
            intervals.append((start, scene.end_frame))
        source = scene.audio_start
        if source is None:
            source = max(0.0, scene.start_frame / plan.fps
                         - plan.card_seconds_before(scene.index))
        for part, (first, last) in enumerate(intervals):
            shift = (cursor - first) / plan.fps
            indexed = [(i, w) for i, w in enumerate(scene.words)
                       if not cuts or (w.start < last / plan.fps and w.end > first / plan.fps)
                       or (w.start == w.end and first / plan.fps <= w.start <= last / plan.fps)]
            words = tuple(w.model_copy(update={
                "start": min((cursor + last - first) / plan.fps,
                             max(cursor / plan.fps, w.start + shift)),
                "end": min((cursor + last - first) / plan.fps,
                           max(cursor / plan.fps, w.end + shift)),
            }) for _, w in indexed)
            offset = (first - scene.start_frame) / plan.fps
            changes = {"index": len(result), "start_frame": cursor,
                       "end_frame": cursor + last - first, "words": words,
                       "audio_start": None if scene.is_card else source + offset}
            if scene.footage_start is not None:
                changes["footage_start"] = scene.footage_start + offset
            if cuts:
                changes.update(text=" ".join(w.text for w in words), caption_text="",
                               caption_emphasis=tuple(n for n, (old, _) in enumerate(indexed)
                                                      if old in scene.caption_emphasis))
                # A jump cut stays a cut even with a whole-video blend selected.
                if part < len(intervals) - 1 or last < scene.end_frame:
                    changes["transition_after"] = TransitionTreatment(kind="cut")
            result.append(scene.model_copy(update=changes))
            cursor += last - first
    payload = plan.model_dump()
    payload.update(scenes=result, total_frames=cursor,
                   audio_duration=max(1 / plan.fps, plan.audio_duration - removed / plan.fps))
    return ScenePlan.model_validate(payload)
