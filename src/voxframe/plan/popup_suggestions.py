"""Pop-ups worth adding, found in the words (D-198). Nothing is added until a
person clicks: these are suggestions, shown with the word that prompted them.

Plain signals, as highlights use (no model):

- a word one of the stickers is for ("flood" for the wave, "money" for the
  bag, "idea" for the bulb);
- a number, said as digits or as a word ("three tips", "$1,200", "90
  percent"), for a counter.

At most one per scene, so a suggestion stays a suggestion and not clutter;
the first number in a scene wins over a sticker, since a number counting up
is what holds the eye.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voxframe.plan.scene_plan import ScenePlan

__all__ = ["Suggestion", "suggest_popups"]

_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "fifty": 50, "hundred": 100, "thousand": 1000, "million": 1_000_000,
}
_DIGITS = re.compile(r"^(?P<before>\$|€|£)?(?P<number>\d[\d,]*(?:\.\d+)?)(?P<after>%|k|m)?$", re.I)


@dataclass(frozen=True)
class Suggestion:
    scene: int
    word: int
    #: "sticker" or "counter".
    kind: str
    #: The sticker's name, or the counter's text.
    value: str
    #: The word that prompted it, as said.
    because: str


def _clean(token: str) -> str:
    return token.strip().strip(".,!?;:\"'()[]").lower()


def _counter(tokens: list[str], position: int) -> str | None:
    token = tokens[position].strip().strip(".,!?;:\"'()[]")
    following = _clean(tokens[position + 1]) if position + 1 < len(tokens) else ""
    match = _DIGITS.match(token)
    if match:
        text = f"{match['before'] or ''}{match['number']}{match['after'] or ''}"
        if not match["after"] and following in ("percent", "per", "%"):
            text += "%"
        if not match["before"] and following in ("dollars", "dollar"):
            text = "$" + text
        # A year said as a number is a date, not an amount to count.
        if re.fullmatch(r"(19|20)\d\d", match["number"]):
            return None
        return text
    value = _NUMBER_WORDS.get(_clean(token))
    if value is None:
        return None
    if following in ("percent",):
        return f"{value}%"
    return f"{value:,}"


def suggest_popups(plan: ScenePlan, limit: int = 12) -> list[Suggestion]:
    """Pop-ups worth adding, in the order they would appear."""
    from voxframe.render.compose.stickers import stickers

    by_word: dict[str, str] = {}
    for entry in stickers():
        for word in entry.get("words", []):  # type: ignore[union-attr]
            by_word.setdefault(str(word), str(entry["name"]))

    taken = {(overlay.scene, overlay.word) for overlay in plan.overlays}
    has_overlay = {overlay.scene for overlay in plan.overlays}
    found: list[Suggestion] = []
    for scene in plan.scenes:
        if scene.is_card or scene.index in has_overlay:
            continue
        tokens = [word.text for word in scene.caption_words()]
        counter = next(
            (
                Suggestion(scene.index, position, "counter", text, tokens[position].strip())
                for position in range(len(tokens))
                if (text := _counter(tokens, position)) is not None
                and (scene.index, position) not in taken
            ),
            None,
        )
        if counter is not None:
            found.append(counter)
        else:
            for position, token in enumerate(tokens):
                name = by_word.get(_clean(token))
                if name is not None and (scene.index, position) not in taken:
                    found.append(Suggestion(scene.index, position, "sticker", name, token.strip()))
                    break
        if len(found) >= limit:
            break
    return found
