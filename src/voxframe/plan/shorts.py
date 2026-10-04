"""Inspectable passage suggestions and word-boundary short cuts.

Ranking uses visible transcript signals, not a claim to understand a story or
predict views. The user reviews the full passage and ending before saving.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from voxframe.config.settings import AspectRatio
from voxframe.plan.editing import EditError
from voxframe.plan.highlights import _rebase_words
from voxframe.plan.scene_plan import PlanWord, ScenePlan

END = re.compile(r'''[.!?…]["'”\u2019»)\]]*$''')
DEPENDENT = {"and", "but", "because", "also", "so", "then", "et", "mais", "donc", "alors", "aussi"}
CUES = {"mistake", "secret", "avoid", "instead", "why", "how", "erreur", "éviter",
        "pourquoi", "comment", "rather", "actually", "myth", "mythe"}


@dataclass(frozen=True)
class TimedToken:
    scene: int
    word: int
    value: PlanWord
    group: int


def revision(plan: ScenePlan) -> str:
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()[:24]


def tokens(plan: ScenePlan) -> tuple[TimedToken, ...]:
    """Displayed words with actual clocks; cards separate suggestion groups."""
    result = []
    group = 0
    for scene in plan.scenes:
        displayed = scene.caption_words()
        if scene.is_card or (scene.display_text and not displayed):
            group += 1
            continue
        for i, word in enumerate(displayed):
            start = max(scene.start_frame / plan.fps, word.start)
            end = min(scene.end_frame / plan.fps, word.end)
            if end >= start:
                result.append(TimedToken(scene.index, i, PlanWord(
                    text=word.text, start=start, end=end), group))
    return tuple(result)


def bounds(plan: ScenePlan, timed: tuple[TimedToken, ...],
           first: int, last: int) -> tuple[int, int]:
    if first < 0 or last < first or last >= len(timed):
        raise EditError("Choose the first and last words from this transcript.")
    a, b = timed[first].value, timed[last].value
    lead = max(0.0, a.start - .12)
    tail = min(plan.total_frames / plan.fps, b.end + .18)
    if first:
        lead = max(lead, min(a.start, timed[first - 1].value.end))
    if last + 1 < len(timed):
        tail = min(tail, max(b.end, timed[last + 1].value.start))
    first_scene, last_scene = timed[first].scene, timed[last].scene
    if first_scene and plan.scenes[first_scene - 1].is_card:
        lead = max(lead, plan.scenes[first_scene].start_frame / plan.fps)
    if last_scene + 1 < len(plan.scenes) and plan.scenes[last_scene + 1].is_card:
        tail = min(tail, plan.scenes[last_scene].end_frame / plan.fps)
    return max(0, math.floor(lead * plan.fps)), min(plan.total_frames, math.ceil(tail * plan.fps))


def build_short(plan: ScenePlan, first: int, last: int, *, vertical: bool = True) -> ScenePlan:
    timed = tokens(plan)
    start, end = bounds(plan, timed, first, last)
    chosen = timed[first:last + 1]
    rebuilt = []
    cursor = 0
    for scene in plan.scenes:
        low, high = max(start, scene.start_frame), min(end, scene.end_frame)
        if scene.is_card or high <= low:
            continue
        selected = [t for t in chosen if t.scene == scene.index]
        displayed = tuple(t.value for t in selected)
        words = _rebase_words(displayed, source_start=low / plan.fps,
                              source_end=high / plan.fps, destination_start=cursor / plan.fps)
        original = tuple(w for w in scene.words
                         if w.start < high / plan.fps and w.end > low / plan.fps)
        text = " ".join(w.text for w in (original if scene.is_corrected else displayed))
        caption = " ".join(w.text for w in displayed) if scene.is_corrected else ""
        if not scene.words and scene.display_text:
            text, caption = scene.text, scene.caption_text
        source = scene.audio_start
        if source is None:
            source = max(0, scene.start_frame / plan.fps - plan.card_seconds_before(scene.index))
        offset = (low - scene.start_frame) / plan.fps
        changes = {"index": len(rebuilt), "start_frame": cursor,
                   "end_frame": cursor + high - low, "words": words,
                   "text": text, "caption_text": caption, "audio_start": source + offset,
                   "caption_emphasis": tuple(i for i, t in enumerate(selected)
                                              if t.word in scene.caption_emphasis)}
        if scene.footage_start is not None:
            changes["footage_start"] = scene.footage_start + offset
        rebuilt.append(scene.model_copy(update=changes))
        cursor += high - low
    seconds = cursor / plan.fps
    if seconds < 3 or seconds > 60 + 1e-7:
        raise EditError("Choose a passage lasting between 3 and 60 seconds. Cards are omitted.")
    payload = plan.model_dump()
    payload.update(scenes=rebuilt, total_frames=cursor, audio_duration=seconds,
                   aspect=AspectRatio.VERTICAL if vertical else plan.aspect)
    return ScenePlan.model_validate(payload)


def source_ranges(plan: ScenePlan) -> list[dict]:
    return [{"audio_start": s.audio_start, "footage_start": s.footage_start,
             "seconds": s.duration_frames / plan.fps} for s in plan.scenes]


def suggestions(plan: ScenePlan) -> list[dict]:
    """Choose up to three distinct, contiguous sentence runs, never reorder them."""
    timed = tokens(plan)
    if not timed:
        return []
    sentences = []
    start = 0
    for i, token in enumerate(timed):
        boundary = i == len(timed) - 1 or timed[i + 1].group != token.group
        if END.search(token.value.text) or boundary:
            sentences.append((start, i, bool(END.search(token.value.text))))
            start = i + 1
    ranked = []
    for position, (first, _, _) in enumerate(sentences):
        # A minute of speech rarely needs more than 40 sentences. Bound work
        # independently of source length, including transcripts with tiny units.
        for _, last, complete in sentences[position:position + 40]:
            if timed[first].group != timed[last].group:
                break
            a, b = bounds(plan, timed, first, last)
            seconds = (b - a) / plan.fps
            if seconds > 60:
                break
            if seconds < 3:
                continue
            spoken = " ".join(t.value.text for t in timed[first:last + 1])
            opening_end = next((j for j in range(first, last + 1)
                                if END.search(timed[j].value.text)), min(last, first + 15))
            opening = " ".join(t.value.text for t in timed[first:opening_end + 1])
            opening_tokens = set(re.findall(r"[\wÀ-ÿ]+", opening.casefold()))
            score = 2 - abs(seconds - 30) / 30
            reasons = []
            if "?" in opening:
                score += 1.5
                reasons.append("Opens with a question")
            if any(c.isdigit() for c in opening):
                score += 1
                reasons.append("A number in the opening")
            if opening_tokens & CUES:
                score += .8
                reasons.append("An explanation or contrast cue in the opening")
            opening_words = re.findall(r"\w+", opening.casefold())
            if opening_words and opening_words[0] in DEPENDENT:
                score -= 1.5
                reasons.append("Opening may depend on earlier context")
            if not complete:
                score -= .6
                reasons.append("Review the ending: no sentence punctuation")
            if timed[last].value.text.rstrip('"”»').endswith("?"):
                score -= .8
                reasons.append("Ends with a question; check the payoff")
            if not reasons:
                reasons.append("A sentence-bounded passage near 30 seconds")
            ranked.append((score, first, last, a, b, spoken, opening, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    picked = []
    for item in ranked:
        _, first, last, a, b, spoken, opening, reasons = item
        if any(max(0, min(last, p[2]) - max(first, p[1]) + 1)
               / min(last - first + 1, p[2] - p[1] + 1) > .35 for p in picked):
            continue
        picked.append(item)
        if len(picked) == 3:
            break
    result = []
    for _, first, last, a, b, spoken, opening, reasons in picked:
        result.append({"id": f"{first}:{last}", "first_word": first, "last_word": last,
                       "start": a / plan.fps, "end": b / plan.fps,
                       "seconds": (b - a) / plan.fps, "opening": opening, "text": spoken,
                       "ending": " ".join(t.value.text
                                          for t in timed[max(first, last - 14):last + 1]),
                       "reasons": reasons})
    return result


def controls(plan: ScenePlan) -> dict:
    return {"revision": revision(plan), "suggestions": suggestions(plan),
            "words": [{"index": i, "text": t.value.text, "start": t.value.start,
                       "end": t.value.end} for i, t in enumerate(tokens(plan))]}
