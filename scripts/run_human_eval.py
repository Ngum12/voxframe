#!/usr/bin/env python3
"""Evaluate retrieval against human-written queries.

Run::

    python scripts/run_human_eval.py

Reads ``tests/eval/human_queries.csv`` and reports accuracy **separately** from
the generated query set. The two are never averaged: they measure different
things, and combining them would hide exactly the discrepancy this exists to
reveal.

Why a separate query set
------------------------
The generated queries were written by the same author as the matcher and its
query extractor, so they share assumptions about how narration is phrased.
Independently written queries can expose blind spots that self-written ones
structurally cannot — if the two sets disagree, the generated set is the one to
distrust.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.generate import CONCEPTS, generate_large_pool  # noqa: E402

QUERIES_FILE = REPO_ROOT / "tests" / "eval" / "human_queries.csv"

#: Comment marker. A guillemet rather than '#' so a query may contain one.
COMMENT = "«"


@dataclass(frozen=True, slots=True)
class HumanQuery:
    """One human-written case."""

    text: str
    language: str
    expected: str
    line: int

    @property
    def is_unmatched_case(self) -> bool:
        """Whether the author recorded that no concept fits.

        Reported separately: a query with no right answer measures whether the
        threshold correctly declines, not whether retrieval ranks well.
        """
        return self.expected.lower() in {"none", "-", ""}


def load_queries(path: Path) -> tuple[list[HumanQuery], list[str]]:
    """Read the query file.

    Returns:
        ``(queries, problems)``. Problems are human-readable complaints about
        rows that could not be used, reported rather than silently skipped.
    """
    if not path.is_file():
        return [], [f"{path} not found"]

    valid_concepts = {c.key for c in CONCEPTS}
    queries: list[HumanQuery] = []
    problems: list[str] = []

    with path.open(encoding="utf-8", newline="") as handle:
        for number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith(COMMENT):
                continue

            try:
                row = next(csv.reader([stripped]))
            except csv.Error as exc:
                problems.append(f"line {number}: unparseable ({exc})")
                continue

            if len(row) < 3:
                problems.append(
                    f"line {number}: expected 3 fields, got {len(row)} — {stripped[:50]}"
                )
                continue

            text, language, expected = (field.strip() for field in row[:3])

            if not text:
                problems.append(f"line {number}: empty query")
                continue

            if language.lower() not in {"en", "fr"}:
                problems.append(
                    f"line {number}: language {language!r} is not en or fr"
                )
                continue

            if expected and expected.lower() not in {"none", "-"}:
                if expected not in valid_concepts:
                    problems.append(
                        f"line {number}: unknown concept {expected!r} "
                        f"(see the list at the bottom of the file)"
                    )
                    continue

            queries.append(
                HumanQuery(text, language.lower(), expected, number)
            )

    return queries, problems


@dataclass(slots=True)
class Outcome:
    """Result for one query."""

    query: HumanQuery
    predicted: str
    rank: int | None
    score: float


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=QUERIES_FILE)
    parser.add_argument(
        "--images",
        type=Path,
        default=Path(os.environ.get("TEMP", "/tmp")) / "voxframe_large_pool",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Run query extraction first, as the real pipeline does.",
    )
    args = parser.parse_args()

    queries, problems = load_queries(args.queries)

    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)

    if not queries:
        print(f"\nNo usable queries in {args.queries}.")
        print("Open the file and add rows below the 'YOUR ROWS BELOW' marker.")
        return 1

    english = sum(1 for q in queries if q.language == "en")
    french = len(queries) - english
    print(f"Human queries: {len(queries)} ({english} EN, {french} FR)")
    if problems:
        print(f"  {len(problems)} row(s) skipped, listed above")

    pool = generate_large_pool(args.images)
    print(f"Pool: {len(pool)} images\n")

    from voxframe.library.embeddings import Embedder

    embedder = Embedder(use_gpu=False)

    keys = sorted(pool)
    image_vectors = np.asarray(embedder.embed_images([pool[k] for k in keys]))

    texts = []
    for query in queries:
        if args.extract:
            # Match what the pipeline actually does: extract visual queries
            # from the sentence rather than embedding the whole thing.
            from voxframe.match.queries import extract_queries

            extracted = extract_queries(query.text, query.language)
            texts.append(" ".join(q.text for q in extracted) or query.text)
        else:
            texts.append(query.text)

    text_vectors = np.asarray(embedder.embed_texts(texts))
    similarity = text_vectors @ image_vectors.T

    outcomes: list[Outcome] = []
    for index, query in enumerate(queries):
        order = np.argsort(-similarity[index])
        ranked = [keys[i].rsplit("_v", 1)[0] for i in order]

        rank = None
        if not query.is_unmatched_case:
            for position, concept in enumerate(ranked):
                if concept == query.expected:
                    rank = position
                    break

        outcomes.append(
            Outcome(query, ranked[0], rank, float(similarity[index][order[0]]))
        )

    scored = [o for o in outcomes if not o.query.is_unmatched_case]
    no_answer = [o for o in outcomes if o.query.is_unmatched_case]

    def rate(subset: list[Outcome], within: int) -> float:
        if not subset:
            return 0.0
        hits = sum(1 for o in subset if o.rank is not None and o.rank < within)
        return hits / len(subset)

    print("=" * 66)
    print("HUMAN-WRITTEN QUERY SET  (reported separately by instruction)")
    print("=" * 66)

    for language, label in (("en", "English"), ("fr", "French")):
        subset = [o for o in scored if o.query.language == language]
        if not subset:
            continue
        print(
            f"  {label:8} n={len(subset):3}   "
            f"top-1 {rate(subset, 1):.0%}   top-3 {rate(subset, 3):.0%}   "
            f"top-5 {rate(subset, 5):.0%}"
        )

    if scored:
        print(
            f"  {'overall':8} n={len(scored):3}   "
            f"top-1 {rate(scored, 1):.0%}   top-3 {rate(scored, 3):.0%}   "
            f"top-5 {rate(scored, 5):.0%}"
        )

    misses = [o for o in scored if o.rank is None or o.rank > 0]
    if misses:
        print("\n  Misses:")
        for outcome in misses:
            rank_note = "not in pool top" if outcome.rank is None else f"rank {outcome.rank + 1}"
            print(f"    [{outcome.query.language}] {outcome.query.text[:52]}")
            print(
                f"         expected {outcome.query.expected}, "
                f"got {outcome.predicted} ({rank_note}, sim {outcome.score:.3f})"
            )

    if no_answer:
        print(f"\n  Cases marked 'none' ({len(no_answer)}):")
        for outcome in no_answer:
            print(
                f"    [{outcome.query.language}] {outcome.query.text[:52]} "
                f"-> {outcome.predicted} (sim {outcome.score:.3f})"
            )
        print("    A low similarity here is the correct outcome: the threshold")
        print("    should decline rather than show an irrelevant image.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
