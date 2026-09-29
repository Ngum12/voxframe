"""Check that a rendered video's word highlights match its own audio (D-144).

Transcribes the finished video's soundtrack with word timings, finds each
highlighted caption word in what is actually heard, and measures how far apart
they are. Independent of the plan's own timings, which is the point: D-110's
fix passed every check that trusted the plan, while captions after a chapter
card ran seconds late against the speech.

Reads the ``.ass`` file, whose karaoke lines time every word; ``.srt`` cues
span whole scenes and say nothing about when a word is spoken. Results are
medians per stretch between cards, since single words are noisy: a
transcriber's word times wander by a few tenths of a second.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

__all__ = ["Stretch", "card_boundaries", "heard", "highlights", "offsets", "stretches"]

_DIALOGUE = re.compile(r"^Dialogue: [^,]*,(\d+:\d+:\d+\.\d+),")
_TOKEN = re.compile(r"\{\\c(&H[0-9A-Fa-f]+)&?\}([^{]*)")


@dataclass(frozen=True)
class Stretch:
    """The words between two cards, and how far their highlights are off."""

    start: float
    end: float
    words: int
    median: float


def _seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _clean(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def highlights(ass: Path) -> list[tuple[float, str, str]]:
    """``(time, word, next word)`` for every word as it is highlighted.

    Each karaoke line colours one word differently from the rest; that word
    is the one being spoken from the line's start time.
    """
    found = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        match = _DIALOGUE.match(line)
        if not match:
            continue
        tokens = [(colour, _clean(text)) for colour, text in _TOKEN.findall(line)]
        tokens = [(colour, word) for colour, word in tokens if word]
        colours = [colour for colour, _ in tokens]
        lone = [i for i, colour in enumerate(colours) if colours.count(colour) == 1]
        if len(tokens) < 2 or len(lone) != 1:
            continue
        index = lone[0]
        following = tokens[index + 1][1] if index + 1 < len(tokens) else ""
        found.append((_seconds(match.group(1)), tokens[index][1], following))
    return found


def heard(video: Path, language: str = "en", model: str = "base") -> list[tuple[float, str]]:
    """Every word in a video's soundtrack, with when it starts."""
    from faster_whisper import WhisperModel

    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(str(video), language=language, word_timestamps=True)
    return [
        (word.start, _clean(word.word))
        for segment in segments
        for word in segment.words or []
    ]


def offsets(
    shown: list[tuple[float, str, str]], spoken: list[tuple[float, str]], window: float = 8.0
) -> list[tuple[float, float]]:
    """(highlight time, highlight minus speech) for words found in the audio.

    Located by the word and the one after it, nearest in time, within
    ``window`` seconds -- far enough to catch the multi-second drift this
    exists to find, near enough that a repeated phrase cannot mislead it.
    """
    results = []
    for time, word, following in shown:
        if not following or len(word) < 3:
            continue
        matches = [
            at
            for index, (at, candidate) in enumerate(spoken[:-1])
            if candidate == word and spoken[index + 1][1] == following
            and abs(at - time) <= window
        ]
        if matches:
            nearest = min(matches, key=lambda at: abs(at - time))
            results.append((time, time - nearest))
    return results


def card_boundaries(plan: Path | None) -> list[float]:
    """Where each card ends, in video seconds: the stretches start there."""
    if plan is None:
        return []
    data = json.loads(plan.read_text(encoding="utf-8"))
    return [
        scene["end_frame"] / data["fps"] for scene in data["scenes"] if scene.get("card_kind")
    ]


def stretches(
    found: list[tuple[float, float]], boundaries: list[float], minimum: int = 3
) -> list[Stretch]:
    """The median offset in each stretch between cards with enough words."""
    edges = [0.0, *boundaries, float("inf")]
    result = []
    for start, end in pairwise(edges):
        stretch = [offset for time, offset in found if start <= time < end]
        if len(stretch) >= minimum:
            result.append(Stretch(start, end, len(stretch), statistics.median(stretch)))
    return result
