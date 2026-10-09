"""Reviewable pause and speech cuts, with an original recording clock.

A gap is never proof of silence; lexical cues are never proof of a mistake.
Only an explicitly selected speech cue removes words. Full plans in edit
history restore cuts along with captions, imagery and source positions.
"""
from __future__ import annotations

import math
import re
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
                         "before": left.text, "after": right.text, "kind": "pause",
                         "removed_text": "",
                         "reason": "Long transcript gap; listen before cutting."})
    return cuts


def suggest_speech_cuts(plan: ScenePlan) -> list[dict]:
    """Conservative lexical cues, not a judgement about what the speaker meant.

    Only standalone hesitation tokens and adjacent repeated phrases of two
    to four words qualify. Never guess timings across scenes or corrections.
    Frame rounding must not touch a retained word; otherwise omit the cue.
    """
    cuts = []
    for scene in plan.scenes:
        if scene.is_card or scene.is_corrected:
            continue
        words = scene.words
        normalized = [re.sub(r"[^\w'-]", "", w.text.casefold()) for w in words]
        proposed = []
        for index, token in enumerate(normalized):
            if token in {"um", "uh", "erm", "euh"}:
                proposed.append((index, index + 1, "filler"))
        index = 0
        while index < len(words):
            for size in range(4, 1, -1):
                end = index + size
                repeated = end + size
                if repeated > len(words):
                    continue
                phrase = normalized[index:end]
                if not all(phrase) or phrase != normalized[end:repeated]:
                    continue
                # A sentence boundary or long pause makes repetition especially
                # ambiguous. Offer only a closely adjacent restart cue.
                if any(re.search(r"[.!?;:]", w.text) for w in words[index:repeated - 1]):
                    continue
                if any(b.start - a.end > .65 for a, b in pairwise(words[index:repeated])):
                    continue
                proposed.append((index, end, "repeat"))
                index = end - 1
                break
            index += 1
        for first, last, kind in proposed:
            removed = words[first:last]
            retained = [w for i, w in enumerate(words) if not first <= i < last]
            if not retained or any(w.end <= w.start for w in removed):
                continue
            start = max(scene.start_frame, math.floor(removed[0].start * plan.fps))
            end = min(scene.end_frame, math.ceil(removed[-1].end * plan.fps))
            if end <= start or any(w.start < start / plan.fps or w.end > end / plan.fps
                                   for w in removed):
                continue
            if any((w.start < end / plan.fps and w.end > start / plan.fps)
                   or (w.start == w.end and start / plan.fps <= w.start < end / plan.fps)
                   for w in retained):
                continue
            quote = " ".join(w.text for w in removed)
            cuts.append({"id": f"{kind}:{scene.index}:{first}:{last}:{start}:{end}:{quote}",
                "kind": kind, "scene": scene.index, "start_frame": start, "end_frame": end,
                "start": start / plan.fps, "end": end / plan.fps,
                "seconds": (end - start) / plan.fps, "removed_text": quote,
                "before": " ".join(w.text for w in words[max(0, first - 4):first]),
                "after": " ".join(w.text for w in words[last:last + 4]),
                "reason": ("Possible hesitation; keep it if it adds personality."
                           if kind == "filler"
                           else "Repeated phrase; keep it if the emphasis is intentional.")})
    # Alternative interpretations can overlap. Prefer the longest phrase;
    # never offer conflicting word-removal choices in the same review batch.
    selected = []
    for cut in sorted(cuts, key=lambda c: (-c["seconds"], c["start_frame"])):
        if not any(cut["start_frame"] < other["end_frame"]
                   and cut["end_frame"] > other["start_frame"]
                   for other in selected):
            selected.append(cut)
    return sorted(selected, key=lambda c: c["start_frame"])


def review_cuts(plan: ScenePlan) -> list[dict]:
    return sorted([*suggest_cuts(plan), *suggest_speech_cuts(plan)], key=lambda c: c["start_frame"])


def apply_cuts(plan: ScenePlan, ids: tuple[str, ...]) -> ScenePlan:
    """Split retained intervals and move words, voice and footage together."""
    candidates = {cut["id"]: cut for cut in review_cuts(plan)}
    if not ids or len(set(ids)) != len(ids) or any(key not in candidates for key in ids):
        raise EditError("These cut suggestions changed. Reload pacing and choose again.")
    ranges = tuple((candidates[key]["start_frame"], candidates[key]["end_frame"]) for key in ids)
    return remove_ranges(plan, ranges)


def remove_ranges(plan: ScenePlan, ranges: tuple[tuple[int, int], ...]) -> ScenePlan:
    """Remove explicit frame intervals, preserving each retained source clock."""
    by_scene: dict[int, list[dict]] = {}
    for first, last in ranges:
        cut = {"start_frame": first, "end_frame": last}
        for scene in plan.scenes:
            first = max(cut["start_frame"], scene.start_frame)
            last = min(cut["end_frame"], scene.end_frame)
            if last > first:
                by_scene.setdefault(scene.index, []).append(
                    {"start_frame": first, "end_frame": last})
    result: list[PlannedScene] = []
    cursor = 0
    removed = 0
    previous_end: int | None = None
    for scene in plan.scenes:
        cuts = sorted(by_scene.get(scene.index, []), key=lambda cut: cut["start_frame"])
        intervals = []
        start = scene.start_frame
        for cut in cuts:
            if cut["start_frame"] < start:
                raise EditError("Cut suggestions overlap; choose one cut for each interval.")
            if cut["start_frame"] > start:
                intervals.append((start, cut["start_frame"]))
            start = cut["end_frame"]
            if not scene.is_card:
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
                       or (w.start == w.end and first / plan.fps <= w.start < last / plan.fps)]
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
                beat = scene.visual_beat
                if beat and beat.source == "director" and beat.text:
                    def normalize(text: str) -> str:
                        return " ".join(re.findall(r"\w+", text.casefold()))
                    if f" {normalize(beat.text)} " not in f" {normalize(changes['text'])} ":
                        changes["visual_beat"] = beat.model_copy(update={"text": ""})
                # A jump cut stays a cut even with a whole-video blend selected.
                if part < len(intervals) - 1 or last < scene.end_frame:
                    changes["transition_after"] = TransitionTreatment(kind="cut")
            if result and previous_end is not None and first > previous_end:
                result[-1] = result[-1].model_copy(
                    update={"transition_after": TransitionTreatment(kind="cut")})
            result.append(scene.model_copy(update=changes))
            previous_end = last
            cursor += last - first
    if not result or not any(not scene.is_card for scene in result):
        raise EditError("Keep at least one part of the recording.")
    if plan.short_export and not 3 <= cursor / plan.fps <= 60 + 1e-7:
        raise EditError("Keep a 3-60 second story for this export preset.")
    payload = plan.model_dump()
    payload.update(scenes=result, total_frames=cursor,
                   audio_duration=max(1 / plan.fps, plan.audio_duration - removed / plan.fps))
    return ScenePlan.model_validate(payload)
