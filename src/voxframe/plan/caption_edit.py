"""Apply user corrections to caption text while keeping word timings.

Transcription gets words wrong — "live" heard as "leave", a name spelled
phonetically — and a user must be able to fix them. The difficulty is that
captions highlight word by word, so every displayed word needs a start and end
time, and the transcript's timings belong to the words the model *heard*.

Three cases, each handled differently:

**One-to-one.** "leave" becomes "live": the corrected word inherits the
original's timing exactly. The commonest case and the easy one.

**Split.** "cant" becomes "can not": one spoken word becomes two displayed
words. The original span is divided between them in proportion to their
lengths, which tracks how long each takes to say more closely than an even
split would.

**Merge.** "may be" becomes "maybe": two spoken words become one. The new word
spans from the first word's start to the last word's end, so no time is lost.

Alignment is by edit distance over the word sequences rather than by position,
because an insertion near the start would otherwise shift every later word onto
the wrong timing.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from voxframe.models.transcript import Word

__all__ = ["CorrectionResult", "EditOperation", "apply_caption_correction"]

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EditOperation:
    """One alignment step between original and corrected words.

    Attributes:
        kind: ``keep``, ``replace``, ``split``, ``merge``, ``insert`` or
            ``delete``.
        original: Indices into the original words this step consumed.
        corrected: The replacement text, empty for a deletion.
    """

    kind: str
    original: tuple[int, ...]
    corrected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    """Corrected words with timings, and how they were derived."""

    words: tuple[Word, ...]
    operations: tuple[EditOperation, ...]

    @property
    def changed(self) -> bool:
        return any(op.kind != "keep" for op in self.operations)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for op in self.operations:
            if op.kind != "keep":
                counts[op.kind] = counts.get(op.kind, 0) + 1
        if not counts:
            return "no changes"
        return ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))


def _normalise(text: str) -> str:
    """Compare words ignoring case and surrounding punctuation.

    A correction that only adds a comma is not a word change, and treating it
    as one would needlessly redistribute timings.
    """
    return text.strip().strip(".,!?;:\"'()[]{}—–-…").lower()  # noqa: RUF001


def _align(
    original: list[str], corrected: list[str]
) -> list[tuple[str, list[int], list[int]]]:
    """Align two word sequences by edit distance.

    Returns:
        Steps of ``(kind, original_indices, corrected_indices)`` in order.
        ``kind`` is ``equal``, ``replace``, ``insert`` or ``delete``.
    """
    rows, cols = len(original) + 1, len(corrected) + 1
    distance = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distance[i][0] = i
    for j in range(cols):
        distance[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            if _normalise(original[i - 1]) == _normalise(corrected[j - 1]):
                distance[i][j] = distance[i - 1][j - 1]
            else:
                distance[i][j] = 1 + min(
                    distance[i - 1][j - 1], distance[i - 1][j], distance[i][j - 1]
                )

    steps: list[tuple[str, list[int], list[int]]] = []
    i, j = len(original), len(corrected)

    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and _normalise(original[i - 1]) == _normalise(corrected[j - 1])
        ):
            steps.append(("equal", [i - 1], [j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
            steps.append(("replace", [i - 1], [j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
            steps.append(("delete", [i - 1], []))
            i -= 1
        else:
            steps.append(("insert", [], [j - 1]))
            j -= 1

    steps.reverse()
    return steps


def _merge_adjacent(
    steps: list[tuple[str, list[int], list[int]]],
) -> list[tuple[str, list[int], list[int]]]:
    """Combine neighbouring edits into split and merge operations.

    Edit distance reports "cant" -> "can not" as an insert plus a replace, and
    "may be" -> "maybe" as a delete plus a replace. Recognising those pairs is
    what lets timings be divided or joined rather than invented.

    The stray step may fall on either side of the replace, because which word
    the aligner treats as the anchor depends on the words themselves: "cant" ->
    "can not" anchors on "can" and puts the insert first, while a correction
    anchoring on the second word would put it last. Both directions are absorbed
    for that reason.
    """
    merged: list[tuple[str, list[int], list[int]]] = []
    index = 0

    while index < len(steps):
        kind, originals, correcteds = steps[index]

        if kind in {"insert", "delete"}:
            # A run of inserts or deletes immediately followed by a replace
            # belongs to that replace: one original word becoming several, or
            # several becoming one.
            run_end = index
            while run_end < len(steps) and steps[run_end][0] == kind:
                run_end += 1

            if run_end < len(steps) and steps[run_end][0] == "replace":
                gathered_originals: list[int] = []
                gathered_correcteds: list[int] = []
                for step in steps[index:run_end]:
                    gathered_originals.extend(step[1])
                    gathered_correcteds.extend(step[2])

                _, replace_originals, replace_correcteds = steps[run_end]
                merged.append(
                    (
                        "replace",
                        sorted(gathered_originals + replace_originals),
                        sorted(gathered_correcteds + replace_correcteds),
                    )
                )
                index = run_end + 1
                continue

        if kind == "replace":
            # The same shape with the stray step trailing the replace.
            while index + 1 < len(steps) and steps[index + 1][0] == "insert":
                correcteds = correcteds + steps[index + 1][2]
                index += 1
            while index + 1 < len(steps) and steps[index + 1][0] == "delete":
                originals = originals + steps[index + 1][1]
                index += 1

        merged.append((kind, originals, correcteds))
        index += 1

    return merged


def apply_caption_correction(
    words: tuple[Word, ...], corrected_text: str
) -> CorrectionResult:
    """Rebuild a scene's words from corrected text, preserving timings.

    Args:
        words: The transcript's words, with their timings.
        corrected_text: What the user wants displayed.

    Returns:
        Words carrying the corrected text with timings derived from the
        originals, plus the operations that produced them.

    Note:
        If the correction is empty, the original words are returned unchanged.
        An empty caption is almost always an accident, and silently blanking a
        scene would be worse than ignoring the edit.
    """
    corrected_words = corrected_text.split()

    if not corrected_words:
        log.warning("caption.correction.empty", original=" ".join(w.text for w in words))
        return CorrectionResult(words, ())

    if not words:
        # Nothing to take timings from. Without a transcript the caller has no
        # basis for word-level timing, so this is left to the scene level.
        return CorrectionResult((), ())

    originals = [word.text for word in words]
    steps = _merge_adjacent(_align(originals, corrected_words))

    rebuilt: list[Word] = []
    operations: list[EditOperation] = []

    for kind, original_indices, corrected_indices in steps:
        texts = tuple(corrected_words[j] for j in corrected_indices)
        sources = tuple(original_indices)

        if kind == "equal":
            original = words[original_indices[0]]
            # Adopt the corrected spelling. The words compare equal only after
            # normalising, so this is where an added comma or changed case
            # reaches the caption without disturbing the timing.
            rebuilt.append(
                original
                if original.text == texts[0]
                else Word(
                    text=texts[0],
                    start=original.start,
                    end=original.end,
                    probability=original.probability,
                )
            )
            operations.append(EditOperation("keep", sources, texts))

        elif kind == "delete":
            # The user removed a word. Its time is absorbed by its neighbours
            # rather than becoming a gap: the following word simply starts
            # where it always did.
            operations.append(EditOperation("delete", sources, ()))

        elif kind == "insert":
            # A word with no original to take timing from. Give it a slice of
            # the preceding word, or of the following one at the start.
            rebuilt.extend(_time_inserted(rebuilt, words, texts))
            operations.append(EditOperation("insert", (), texts))

        elif len(original_indices) == 1 and len(texts) == 1:
            original = words[original_indices[0]]
            rebuilt.append(
                Word(
                    text=texts[0],
                    start=original.start,
                    end=original.end,
                    probability=original.probability,
                )
            )
            operations.append(EditOperation("replace", sources, texts))

        elif len(original_indices) == 1 and len(texts) > 1:
            rebuilt.extend(_split(words[original_indices[0]], texts))
            operations.append(EditOperation("split", sources, texts))

        else:
            rebuilt.append(_merge([words[i] for i in original_indices], texts))
            operations.append(EditOperation("merge", sources, texts))

    result = CorrectionResult(tuple(rebuilt), tuple(operations))

    if result.changed:
        log.info("caption.corrected", changes=result.summary())

    return result


def _split(original: Word, texts: tuple[str, ...]) -> list[Word]:
    """Divide one word's span among several replacements.

    Split in proportion to length rather than evenly: "can not" from "cant"
    spends more time on "not" than a three-way character split would suggest,
    and proportional division tracks speech better than equal division.
    """
    total_characters = sum(len(text) for text in texts) or 1
    span = original.end - original.start

    parts: list[Word] = []
    cursor = original.start

    for position, text in enumerate(texts):
        share = span * (len(text) / total_characters)
        # The last part ends exactly where the original did, so rounding
        # cannot leave a gap before the next word.
        end = original.end if position == len(texts) - 1 else cursor + share
        parts.append(
            Word(
                text=text,
                start=cursor,
                end=max(end, cursor + 0.001),
                probability=original.probability,
            )
        )
        cursor = end

    return parts


def _merge(originals: list[Word], texts: tuple[str, ...]) -> Word:
    """Join several words' spans into one.

    Spans from the first start to the last end, so no time is lost between
    them and the caption stays aligned with the audio.
    """
    return Word(
        text=" ".join(texts),
        start=originals[0].start,
        end=originals[-1].end,
        probability=min(word.probability for word in originals),
    )


def _time_inserted(
    rebuilt: list[Word], words: tuple[Word, ...], texts: tuple[str, ...]
) -> list[Word]:
    """Give an inserted word a plausible span.

    A word the speaker did not say has no true timing. Taking a slice from the
    preceding word keeps the sequence ordered and non-overlapping, which is
    what caption rendering requires; the alternative of a zero-length span
    would make the word flash by unreadably.
    """
    if rebuilt:
        previous = rebuilt[-1]
        span = max(0.05, (previous.end - previous.start) * 0.4)
        start = previous.end
    elif words:
        span = max(0.05, (words[0].end - words[0].start) * 0.4)
        start = max(0.0, words[0].start - span)
    else:
        return []

    inserted: list[Word] = []
    cursor = start
    each = span / len(texts)

    for text in texts:
        inserted.append(
            Word(text=text, start=cursor, end=cursor + each, probability=0.5)
        )
        cursor += each

    return inserted
