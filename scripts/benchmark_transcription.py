#!/usr/bin/env python3
"""Measure transcription accuracy by word error rate against a reference.

Run::

    python scripts/benchmark_transcription.py
    python scripts/benchmark_transcription.py --models base small
    python scripts/benchmark_transcription.py --audio "samples/private/mine.m4a"

Why WER and not confidence
--------------------------
Confidence scores measure how sure the model is, which is not the same as being
right. On a real recording the ``small`` model reported *fewer* low-confidence
words than ``base`` while dropping half the audio — confident and wrong. WER
compares against a human-written reference, so it measures what actually
happened.

Reference transcripts
---------------------
A reference lives beside its audio with a ``.ref.txt`` suffix::

    samples/private/Recording (3).m4a
    samples/private/Recording (3).ref.txt

Write it exactly as spoken. Case and punctuation are normalised away before
comparison, so only the words matter.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Approximate download sizes, for the size/accuracy trade-off.
MODEL_SIZES_MB = {
    "tiny": 75,
    "base": 145,
    "small": 484,
    "medium": 1530,
    "large-v3": 3090,
    "large-v3-turbo": 1620,
}

#: Number words, written out or numeric, that a transcript may render either
#: way without being wrong.
_NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten",
}


@dataclass
class WerResult:
    """Word error rate and its components."""

    reference_words: int
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    pairs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        return self.errors / self.reference_words if self.reference_words else 0.0

    @property
    def summary(self) -> str:
        return (
            f"{self.wer:.1%} "
            f"(S{self.substitutions} D{self.deletions} I{self.insertions} "
            f"of {self.reference_words})"
        )


def normalise(text: str) -> list[str]:
    """Reduce text to comparable words.

    Case, punctuation and accents are removed: a transcript that writes
    "don't" where the reference has "dont" is not a transcription error, and
    counting it as one would hide the errors that matter.
    """
    lowered = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in lowered if unicodedata.category(c) != "Mn")
    words = re.findall(r"[a-z0-9']+", stripped)
    return [_NUMBER_WORDS.get(word, word) for word in words]


def word_error_rate(reference: str, hypothesis: str) -> WerResult:
    """Compute WER by Levenshtein alignment.

    Returns:
        Counts plus the aligned substitution pairs, which are what show
        *how* a model fails rather than only how often.
    """
    ref = normalise(reference)
    hyp = normalise(hypothesis)

    result = WerResult(reference_words=len(ref))
    if not ref:
        result.insertions = len(hyp)
        return result

    # Standard edit-distance table with a backtrace for the error breakdown.
    rows, cols = len(ref) + 1, len(hyp) + 1
    distance = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distance[i][0] = i
    for j in range(cols):
        distance[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                distance[i][j] = distance[i - 1][j - 1]
            else:
                distance[i][j] = 1 + min(
                    distance[i - 1][j - 1],  # substitution
                    distance[i - 1][j],  # deletion
                    distance[i][j - 1],  # insertion
                )

    i, j = len(ref), len(hyp)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
            result.substitutions += 1
            result.pairs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
            result.deletions += 1
            result.pairs.append((ref[i - 1], "—"))
            i -= 1
        else:
            result.insertions += 1
            result.pairs.append(("—", hyp[j - 1]))
            j -= 1

    result.pairs.reverse()
    return result


def aligned_from(reference: str, hypothesis: str, first_word: str) -> tuple[str, str]:
    """Trim both texts to start at ``first_word``.

    Some references contain a portion that is not independent of the models
    being compared. The English LibriVox clip is one: its spoken preamble
    ("read for LibriVox dot org by ...") names a reader and a website that
    appear in no published text, so the reference for that part was taken from
    a model's own output.

    That matters more than it sounds. ``medium`` skips the preamble entirely
    and starts at the poem, which cost it 24 deletions and made it look like
    the *worst* model at 28.9% WER when scoring the whole clip. Scoring from
    the poem's first word instead, where the reference is Helen Hunt Jackson's
    published text, it is the *best* at 3.0%. The ranking reverses.

    Matching on a normalised word rather than a literal string matters too: a
    first attempt matched the literal "O winter", which ``tiny`` and ``base``
    render differently, leaving their preambles in and charging them ~24
    phantom insertions.

    Returns:
        ``(reference, hypothesis)`` each trimmed to begin at the word before
        ``first_word``, or unchanged if it is absent from either.
    """
    target = normalise(first_word)
    if not target:
        return reference, hypothesis

    def trim(text: str) -> str:
        words = normalise(text)
        try:
            offset = words.index(target[0])
        except ValueError:
            return text
        return " ".join(words[max(0, offset - 1) :])

    return trim(reference), trim(hypothesis)


def find_reference(audio: Path) -> Path | None:
    """The reference transcript for an audio file, if one exists."""
    candidate = audio.with_suffix(".ref.txt")
    if candidate.is_file():
        return candidate

    # Also accept "name.m4a.ref.txt", which is what a naive rename produces.
    alternative = audio.parent / f"{audio.name}.ref.txt"
    return alternative if alternative.is_file() else None


def find_audio(explicit: Path | None) -> list[Path]:
    """Audio files to benchmark, preferring anything with a reference."""
    if explicit:
        return [explicit]

    found: list[Path] = []
    for directory in (REPO_ROOT / "samples" / "private", REPO_ROOT / "samples" / "public"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
                # Skip the trimmed duplicates the fetch script writes.
                if path.stem.endswith("_45s") and (
                    directory / f"{path.stem[:-4]}.mp3"
                ).exists():
                    continue
                found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, help="One file instead of all samples.")
    parser.add_argument(
        "--models",
        nargs="*",
        default=["tiny", "base", "small"],
        help="Whisper sizes to compare.",
    )
    parser.add_argument(
        "--language", help="Force a language code instead of detecting."
    )
    parser.add_argument(
        "--score-from",
        metavar="WORD",
        help=(
            "score from this word onward in both reference and output. Use it "
            "when a reference's opening is not independent of the models being "
            "compared; see aligned_from()."
        ),
    )
    args = parser.parse_args()

    from voxframe.transcribe import Transcriber

    files = find_audio(args.audio)
    if not files:
        print("No audio found. Run scripts/fetch_samples.py first.", file=sys.stderr)
        return 1

    print(f"Models:  {', '.join(args.models)}")
    print(f"Audio:   {len(files)} file(s)")
    if args.language:
        print(f"Language forced to {args.language}")
    if args.score_from:
        print(f"Scoring from {args.score_from!r} onward in both texts")
    print()

    for audio in files:
        reference_path = find_reference(audio)
        reference = reference_path.read_text(encoding="utf-8") if reference_path else None

        print("=" * 78)
        print(f"{audio.name}")
        if reference:
            print(f"  reference: {reference_path.name} "
                  f"({len(normalise(reference))} words)")
        else:
            print("  no reference transcript — timing and detection only")
            print(f"  write one as: {audio.with_suffix('.ref.txt').name}")
        print("=" * 78)

        header = f"  {'model':16} {'WER':>22} {'lang':>6} {'p':>6} {'time':>8} {'MB':>6}"
        print(header)
        print("  " + "-" * (len(header) - 2))

        for model in args.models:
            try:
                started = time.monotonic()
                transcriber = Transcriber(
                    model, cache_dir=REPO_ROOT / ".voxframe_cache",
                    language=args.language,
                )
                transcript = transcriber.transcribe(audio)
                elapsed = time.monotonic() - started
            except Exception as exc:
                print(f"  {model:16} FAILED: {type(exc).__name__}: {exc}")
                continue

            if reference:
                scored_reference, scored_hypothesis = (
                    aligned_from(reference, transcript.text, args.score_from)
                    if args.score_from
                    else (reference, transcript.text)
                )
                wer = word_error_rate(scored_reference, scored_hypothesis)
                accuracy = wer.summary
            else:
                accuracy = f"{transcript.word_count} words"

            print(
                f"  {model:16} {accuracy:>22} {transcript.language:>6} "
                f"{transcript.language_probability:>6.2f} {elapsed:>7.0f}s "
                f"{MODEL_SIZES_MB.get(model, 0):>6}"
            )

        print()

    print("Note: the time column includes the model DOWNLOAD on first use, which")
    print("dominates everything else — large-v3-turbo measured 943s on a first run")
    print("against 41.6s of actual transcription. Re-run once the weights are")
    print("cached for a meaningful number. Times are near-zero when a transcript")
    print("is cached; delete .voxframe_cache to force re-runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
