"""Finding the pace: what to cut, the line that hooks, the words to punch in
on, and where the picture stands still too long (D-199).

Plain signals, measured, as highlights use (no model): the words' own
timestamps, and how loud each word is in the recording. Everything found is
offered, shown, and undoable; a cut can be switched off on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import structlog

from voxframe.plan.pace import Cut, CutKind, PunchIn
from voxframe.plan.scene_plan import PlanWord, ScenePlan

__all__ = [
    "HookLine",
    "Stillness",
    "find_cuts",
    "find_hooks",
    "find_stillness",
    "find_stressed_words",
]

log = structlog.get_logger(__name__)

FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"})

#: Words that carry little on their own, never punched in on.
_SMALL = frozenset(
    "a an the and or but so to of in on at for with from by is are was were be been it its "
    "this that these those i you he she we they me my your our their his her them us do does "
    "did have has had not no yes if then than as just very really about into out up down".split()
)

#: Words that make people stay: a stake, a turn, a secret, a warning.
_CURIOUS = frozenset(
    "secret mistake mistakes never nobody why how stop truth warning actually imagine biggest "
    "worst best only nothing everything wrong dangerous surprising shocking finally hidden "
    "until unless must should "
    "nobody's".split()
)

_NUMBER_WORDS = frozenset(
    "two three four five six seven eight nine ten twelve twenty thirty fifty hundred "
    "thousand million billion percent".split()
)


def _clean(token: str) -> str:
    return re.sub(r"[^\w']", "", token.lower())


@dataclass(frozen=True)
class _Spoken:
    word: PlanWord
    scene: int  # position in plan.scenes
    #: Seconds to subtract from the plan's clock for the recording's (cards).
    offset: float


def _spoken(plan: ScenePlan) -> list[_Spoken]:
    """Every word, in order, with where it is in the recording."""
    words: list[_Spoken] = []
    for position, scene in enumerate(plan.scenes):
        if scene.is_card:
            continue
        offset = plan.card_seconds_before(position)
        words.extend(_Spoken(word, position, offset) for word in scene.words)
    return words


def find_cuts(
    plan: ScenePlan,
    *,
    keep_pause: float = 0.25,
    fillers: bool = True,
    repeats: bool = True,
    edges: bool = True,
) -> tuple[Cut, ...]:
    """Cuts that tighten the pace without losing a word that matters.

    Args:
        keep_pause: The pause left between two words, in seconds, where a
            longer one is trimmed: breath, so speech never sounds clipped.
        fillers: Cut "um", "uh" and their kind.
        repeats: Cut the first of a word or a pair of words said twice.
        edges: Cut the silence before the first word and after the last.
    """
    spoken = _spoken(plan)
    if not spoken:
        return ()
    fps = plan.fps
    cuts: list[Cut] = []

    def scene_end(position: int) -> float:
        return plan.scenes[position].end_frame / fps

    def scene_start(position: int) -> float:
        return plan.scenes[position].start_frame / fps

    # Pauses, never across a card: a card is its own pause.
    for before, after in pairwise(spoken):
        if any(plan.scenes[p].is_card for p in range(before.scene, after.scene + 1)):
            continue
        gap = after.word.start - before.word.end
        if gap > keep_pause + 0.08:
            start = before.word.end + keep_pause / 2
            end = after.word.start - keep_pause / 2
            cuts.append(Cut(start=round(start, 3), end=round(end, 3), kind=CutKind.SILENCE,
                            label=f"{gap:.1f} s pause"))

    if fillers:
        for position, item in enumerate(spoken):
            if _clean(item.word.text) not in FILLERS:
                continue
            previous_end = spoken[position - 1].word.end if position else scene_start(item.scene)
            next_start = (
                spoken[position + 1].word.start
                if position + 1 < len(spoken) else scene_end(item.scene)
            )
            start = max(previous_end + 0.04, item.word.start - 0.05)
            end = min(next_start - 0.04, item.word.end + 0.05)
            if end > start:
                cuts.append(Cut(start=round(start, 3), end=round(end, 3), kind=CutKind.FILLER,
                                label=f"“{item.word.text.strip()}”"))

    if repeats:
        texts = [_clean(item.word.text) for item in spoken]
        position = 0
        while position + 1 < len(spoken):
            same_scene = spoken[position].scene == spoken[position + 1].scene
            if same_scene and texts[position] and texts[position] == texts[position + 1] \
                    and not texts[position].isdigit():
                cuts.append(Cut(
                    start=round(spoken[position].word.start, 3),
                    end=round(spoken[position + 1].word.start - 0.02, 3),
                    kind=CutKind.REPEAT,
                    label=f"“{spoken[position].word.text.strip()}” said twice",
                ))
                position += 1
            elif (
                position + 3 < len(spoken)
                and len({spoken[i].scene for i in range(position, position + 4)}) == 1
                and texts[position] and texts[position + 1]
                and texts[position : position + 2] == texts[position + 2 : position + 4]
            ):
                cuts.append(Cut(
                    start=round(spoken[position].word.start, 3),
                    end=round(spoken[position + 2].word.start - 0.02, 3),
                    kind=CutKind.REPEAT,
                    label=f"“{spoken[position].word.text.strip()} "
                          f"{spoken[position + 1].word.text.strip()}” said twice",
                ))
                position += 2
            position += 1

    if edges:
        first, last = spoken[0], spoken[-1]
        lead_start = scene_start(first.scene)
        if first.word.start - lead_start > 0.35:
            cuts.append(Cut(start=round(lead_start, 3), end=round(first.word.start - 0.12, 3),
                            kind=CutKind.EDGE, label="silence before the first word"))
        tail_end = scene_end(last.scene)
        if tail_end - last.word.end > 0.6:
            cuts.append(Cut(start=round(last.word.end + 0.35, 3), end=round(tail_end, 3),
                            kind=CutKind.EDGE, label="silence after the last word"))

    found = tuple(sorted((c for c in cuts if c.end - c.start >= 0.05), key=lambda c: c.start))
    log.info("pace.cuts.found", cuts=len(found),
             seconds=round(sum(c.end - c.start for c in found), 2))
    return found


def _loudness(plan: ScenePlan, audio: Path, spoken: list[_Spoken]) -> list[float] | None:
    """Each word's loudness in the recording, in dB, or ``None`` unreadable."""
    try:
        import numpy as np
        import soundfile as sf

        samples, rate = sf.read(str(audio), dtype="float32", always_2d=True)
    except Exception as exc:
        log.warning("pace.loudness.unreadable", error=str(exc))
        return None
    mono = samples.mean(axis=1)
    levels: list[float] = []
    for item in spoken:
        a = max(0, int((item.word.start - item.offset) * rate))
        b = max(a + 1, int((item.word.end - item.offset) * rate))
        chunk = mono[a:b]
        rms = float(np.sqrt(np.mean(chunk**2))) if chunk.size else 0.0
        levels.append(20 * float(np.log10(max(rms, 1e-6))))
    return levels


def find_stressed_words(
    plan: ScenePlan, audio: Path, *, per_minute: float = 8.0, min_gap: float = 3.0
) -> tuple[PunchIn, ...]:
    """The words said with most weight, for punch-ins: louder than the words
    round them, and words that carry meaning; spaced out, and only where the
    speaker is on screen to be zoomed in on."""
    import numpy as np

    spoken = _spoken(plan)
    levels = _loudness(plan, audio, spoken)
    if not spoken or levels is None:
        return ()
    scored: list[tuple[float, _Spoken]] = []
    for index, item in enumerate(spoken):
        scene = plan.scenes[item.scene]
        if not (plan.shows_speaker(scene) or plan.shares_frame(scene)):
            continue
        text = _clean(item.word.text)
        if len(text) < 3 or text in _SMALL or text in FILLERS:
            continue
        around = levels[max(0, index - 6) : index] + levels[index + 1 : index + 7]
        if not around:
            continue
        lift = levels[index] - float(np.median(around))
        length = item.word.end - item.word.start
        score = lift + min(1.5, length * 3) + (1.0 if text in _CURIOUS else 0.0)
        if lift >= 2.0:
            scored.append((score, item))
    minutes = max(plan.total_frames / plan.fps / 60, 0.25)
    budget = max(1, round(minutes * per_minute))
    chosen: list[_Spoken] = []
    for _, item in sorted(scored, key=lambda pair: -pair[0]):
        if all(abs(item.word.start - other.word.start) >= min_gap for other in chosen):
            chosen.append(item)
        if len(chosen) >= budget:
            break
    return tuple(
        PunchIn(start=round(item.word.start, 3), end=round(item.word.end, 3), zoom=1.15,
                word=item.word.text.strip()[:40])
        for item in sorted(chosen, key=lambda i: i.word.start)
    )


@dataclass(frozen=True)
class HookLine:
    """A line that could open the video, and why."""

    start: float
    end: float
    text: str
    score: float
    reasons: tuple[str, ...]


def find_hooks(plan: ScenePlan, audio: Path | None = None, limit: int = 3) -> list[HookLine]:
    """The lines most likely to make someone stop scrolling, best first.

    A sentence, 1.5 to 9 seconds long, scored for what makes people stay: a
    question, a number, talking to "you", a turn or a stake ("the biggest
    mistake", "nobody tells you"), being short, and being said with energy.
    The opening line is left out: it already opens the video.
    """
    spoken = _spoken(plan)
    if not spoken:
        return []
    levels = _loudness(plan, audio, spoken) if audio is not None else None
    median = sorted(levels)[len(levels) // 2] if levels else 0.0

    sentences: list[list[int]] = []
    current: list[int] = []
    for index, item in enumerate(spoken):
        if current and spoken[current[-1]].scene != item.scene and \
                any(plan.scenes[p].is_card for p in range(spoken[current[-1]].scene, item.scene)):
            sentences.append(current)
            current = []
        current.append(index)
        if re.search(r"[.?!]$", item.word.text.strip()):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)

    hooks: list[HookLine] = []
    for number, sentence in enumerate(sentences):
        if number == 0:
            continue
        first, last = spoken[sentence[0]].word, spoken[sentence[-1]].word
        seconds = last.end - first.start
        if not 1.5 <= seconds <= 9.0:
            continue
        tokens = [spoken[i].word.text.strip() for i in sentence]
        clean = [_clean(t) for t in tokens]
        score, reasons = 0.0, []
        if tokens[-1].endswith("?"):
            score += 3
            reasons.append("a question")
        if any(t.isdigit() or t in _NUMBER_WORDS or re.search(r"\d", t) for t in clean):
            score += 2
            reasons.append("a number")
        if any(t in ("you", "your", "you're", "yourself") for t in clean):
            score += 1.5
            reasons.append("talks to you")
        curious = [t for t in clean if t in _CURIOUS]
        if curious:
            score += 1.5 * min(2, len(curious))
            reasons.append(f"“{curious[0]}”")
        if len(tokens) <= 12:
            score += 1
            reasons.append("short")
        if levels:
            energy = sum(levels[i] for i in sentence) / len(sentence) - median
            if energy > 1.5:
                score += min(2.0, energy / 2)
                reasons.append("said with energy")
        if score <= 0:
            continue
        hooks.append(HookLine(round(first.start - 0.08, 3), round(last.end + 0.12, 3),
                              " ".join(tokens), round(score, 2), tuple(reasons)))
    return sorted(hooks, key=lambda h: -h.score)[:limit]


@dataclass(frozen=True)
class Stillness:
    """A stretch of the video where nothing on screen changes."""

    start: float
    end: float


def find_stillness(video: ScenePlan, longest: float = 4.0) -> list[Stillness]:
    """Stretches longer than ``longest`` seconds with no new picture, cut,
    punch-in or pop-up, in the video's own time (a projection). Captions
    change all the time and do not count: they are not something new to see.
    """
    fps = video.fps
    changes: set[float] = {0.0, video.total_frames / fps}
    for scene in video.scenes:
        start = scene.start_frame / fps
        changes.add(start)
        cursor = start
        for span in scene.footage_spans:
            changes.add(round(cursor, 3))
            cursor += span.seconds
        for zoom in scene.zooms:
            changes.add(round(start + zoom.start, 3))
    for overlay in video.overlays:
        changes.add(round(video.overlay_times(overlay)[0], 3))
    ordered = sorted(changes)
    return [
        Stillness(round(a, 3), round(b, 3))
        for a, b in pairwise(ordered)
        if b - a > longest
    ]
