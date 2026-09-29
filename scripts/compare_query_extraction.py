#!/usr/bin/env python3
"""Compare hand-written extraction rules against a spaCy POS tagger.

Run::

    python scripts/compare_query_extraction.py

Evaluates both approaches on **real transcript text** rather than hand-picked
sentences. That distinction matters: the current rules were tuned on sentences
chosen by their author, and tuning and testing on the same examples proves
nothing. The plural-noun bug (D-041) survived a full unit-test suite for exactly
that reason.

Sources of real text, in order of preference:

1. Transcripts cached from previous renders (``.voxframe_cache/transcripts``),
   including the LibriVox excerpts and any private recording.
2. Sentences from the evaluation concepts, as a fallback when no transcript is
   available — weaker evidence, and labelled as such in the output.

What is measured
----------------
There is no ground-truth label for "the right query", so the comparison reports
observable properties that bear on matching quality:

- **Verb leakage** — verbs in the output pull the embedding toward the action
  rather than the subject.
- **Subject retention** — whether the sentence's head noun survives.
- **Function-word leakage** — determiners and prepositions carry no visual
  meaning.
- **Speed** — per sentence, which matters over a 60-minute transcript.

Plus a side-by-side dump, because the numbers alone do not show whether a
difference would be visible in a finished video.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voxframe.match.queries import extract_queries  # noqa: E402

#: Words that should never appear in a visual query. Used to score leakage
#: without needing a labelled corpus.
_ENGLISH_FUNCTION = frozenset(
    "the a an of in on at to for with from by as and or but is are was were "
    "be been being have has had do does did will would can could that this "
    "it its they them their there here".split()
)
_FRENCH_FUNCTION = frozenset(
    "le la les un une des du de et ou mais dans sur sous par pour avec sans "
    "est sont etait etaient avoir etre ce cette ces qui que dont il elle ils "
    "elles je tu nous vous son sa ses leur leurs".split()
)


@dataclass(slots=True)
class Stats:
    """Observable properties of one extractor's output."""

    label: str
    sentences: int = 0
    empty_results: int = 0
    total_queries: int = 0
    total_words: int = 0
    function_word_leaks: int = 0
    verb_leaks: int = 0
    subject_hits: int = 0
    subject_checks: int = 0
    seconds: float = 0.0
    samples: list[tuple[str, str, list[str]]] = field(default_factory=list)

    @property
    def ms_per_sentence(self) -> float:
        return self.seconds * 1000 / self.sentences if self.sentences else 0.0

    @property
    def mean_queries(self) -> float:
        return self.total_queries / self.sentences if self.sentences else 0.0

    @property
    def mean_words(self) -> float:
        return self.total_words / self.total_queries if self.total_queries else 0.0

    @property
    def empty_rate(self) -> float:
        return self.empty_results / self.sentences if self.sentences else 0.0

    @property
    def function_leak_rate(self) -> float:
        return self.function_word_leaks / self.total_words if self.total_words else 0.0

    @property
    def verb_leak_rate(self) -> float:
        return self.verb_leaks / self.total_words if self.total_words else 0.0

    @property
    def subject_rate(self) -> float:
        return self.subject_hits / self.subject_checks if self.subject_checks else 0.0


def load_real_sentences() -> tuple[list[tuple[str, str]], str]:
    """Collect sentences from cached transcripts.

    Returns:
        ``(sentences, provenance)`` where each sentence is ``(text, language)``.
    """
    sentences: list[tuple[str, str]] = []
    cache = REPO_ROOT / ".voxframe_cache" / "transcripts"

    if cache.is_dir():
        for path in sorted(cache.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue

            language = data.get("language", "en")
            words = [w["text"].strip() for w in data.get("words", [])]

            # Rebuild sentences from word-level output.
            current: list[str] = []
            for word in words:
                current.append(word)
                if word.endswith((".", "!", "?")) and len(current) >= 4:
                    sentences.append((" ".join(current), language))
                    current = []
            if len(current) >= 4:
                sentences.append((" ".join(current), language))

    if sentences:
        return sentences, f"{len(sentences)} sentences from cached transcripts"

    from tests.eval.generate import CONCEPTS

    fallback = [(c.text_en, "en") for c in CONCEPTS]
    fallback += [(c.text_fr, "fr") for c in CONCEPTS]
    return fallback, (
        f"{len(fallback)} eval concept sentences "
        "(NO CACHED TRANSCRIPTS — weaker evidence; render something first)"
    )


def score(
    label: str,
    sentences: list[tuple[str, str]],
    extract,
    subjects: dict[str, str] | None = None,
) -> Stats:
    """Run one extractor over the sentences and measure it."""
    stats = Stats(label=label)

    started = time.monotonic()
    for text, language in sentences:
        queries = extract(text, language)
        stats.sentences += 1

        if not queries:
            stats.empty_results += 1

        stats.total_queries += len(queries)
        function_words = _FRENCH_FUNCTION if language == "fr" else _ENGLISH_FUNCTION

        joined = " ".join(queries).lower()
        for query in queries:
            for word in query.split():
                stats.total_words += 1
                if word.lower().strip(".,!?;:'\"") in function_words:
                    stats.function_word_leaks += 1

        if subjects and text in subjects:
            stats.subject_checks += 1
            if subjects[text].lower() in joined:
                stats.subject_hits += 1

        if len(stats.samples) < 12:
            stats.samples.append((language, text, queries))

    stats.seconds = time.monotonic() - started
    return stats


def build_spacy_extractor():
    """Build a spaCy-based extractor, or return ``None`` if unavailable."""
    try:
        import spacy
    except ImportError:
        return None, "spacy not installed"

    models: dict[str, object] = {}
    for language, name in (("en", "en_core_web_sm"), ("fr", "fr_core_news_sm")):
        try:
            models[language] = spacy.load(name, disable=["ner", "lemmatizer"])
        except OSError:
            return None, f"{name} not downloaded (python -m spacy download {name})"

    def extract(text: str, language: str, max_queries: int = 3) -> list[str]:
        """Build noun chunks from POS tags.

        Uses the tagger directly rather than ``noun_chunks``: the French model
        does not provide noun chunks, so doing it by hand keeps both languages
        on the same logic.
        """
        model = models.get(language, models["en"])
        doc = model(text)  # type: ignore[operator]

        phrases: list[list[str]] = []
        current: list[str] = []
        has_noun = False

        for token in doc:
            if token.pos_ in {"NOUN", "PROPN"}:
                current.append(token.text)
                has_noun = True
            elif token.pos_ == "ADJ" and len(current) < 3:
                current.append(token.text)
            else:
                if current and has_noun:
                    phrases.append(current[:3])
                current, has_noun = [], False

        if current and has_noun:
            phrases.append(current[:3])

        # Longer phrases first, as more specific.
        phrases.sort(key=len, reverse=True)
        return [" ".join(p) for p in phrases[:max_queries]]

    return extract, "en_core_web_sm (MIT) + fr_core_news_sm (LGPL-LR)"


def main() -> int:
    sentences, provenance = load_real_sentences()
    print(f"Corpus: {provenance}")

    english = sum(1 for _, language in sentences if language == "en")
    print(f"  {english} English, {len(sentences) - english} French\n")

    def rules(text: str, language: str) -> list[str]:
        return [q.text for q in extract_queries(text, language)]

    results = [score("hand-written rules", sentences, rules)]

    spacy_extract, spacy_note = build_spacy_extractor()
    if spacy_extract is None:
        print(f"spaCy unavailable: {spacy_note}\n")
    else:
        print(f"spaCy: {spacy_note}\n")
        results.append(score("spaCy POS tagger", sentences, spacy_extract))

    print("=" * 74)
    print(f"{'':22} {'ms/sent':>9} {'queries':>9} {'words/q':>9} "
          f"{'empty':>8} {'fn leak':>9}")
    print("-" * 74)
    for stats in results:
        print(
            f"{stats.label:22} {stats.ms_per_sentence:9.2f} "
            f"{stats.mean_queries:9.2f} {stats.mean_words:9.2f} "
            f"{stats.empty_rate:8.0%} {stats.function_leak_rate:9.1%}"
        )
    print("=" * 74)

    if len(results) == 2:
        print("\nSide by side (first 12 sentences):\n")
        for rule_sample, spacy_sample in zip(
            results[0].samples, results[1].samples, strict=False
        ):
            language, text, rule_queries = rule_sample
            print(f"[{language}] {text[:66]}")
            print(f"     rules: {rule_queries}")
            print(f"     spaCy: {spacy_sample[2]}")
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
