#!/usr/bin/env python3
"""Compare ways of turning scene text into a search vector.

Run::

    python scripts/compare_query_strategies.py

Subject retention for the hand-written extractor measured **46%** (D-046),
meaning most scenes are searched without their main subject in the query. That
is the largest current quality problem, and this measures whether combining the
extracted queries with the full sentence recovers it.

Strategies compared
-------------------
- **extracted** — the current default: embed each extracted phrase, combine
  weighted by confidence.
- **full** — embed the whole scene sentence, ignoring extraction entirely.
- **hybrid-mean** — weighted average of the two vectors. One search.
- **hybrid-max** — search with both vectors independently and keep whichever
  candidate scores higher. Two searches, more robust to one vector being poor.

Why the last two might differ
-----------------------------
Averaging two vectors in a normalised space gives a point between them, which
can sit in a region neither describes. Taking the better of two searches never
does that, at the cost of a second query. Whether the difference matters is an
empirical question, which is what this script answers.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.generate import CONCEPTS, generate_large_pool  # noqa: E402

from voxframe.match.queries import extract_queries  # noqa: E402

HUMAN_QUERIES = REPO_ROOT / "tests" / "eval" / "human_queries.csv"

#: Sentences where the subject arrives late or indirectly. Narration does this
#: constantly and the extractor handles it poorly, so these are included
#: deliberately rather than only the phrasings extraction was tuned on.
INDIRECT_CASES: tuple[tuple[str, str, str], ...] = (
    ("and that is when the whole valley finally came into view below us",
     "en", "hills_green"),
    ("what struck me first was how much snow there still was up there",
     "en", "mountains_snow"),
    ("you could hear it long before you could see it, that constant crashing",
     "en", "ocean_waves"),
    ("by then it was getting dark, and the only light left was overhead",
     "en", "star_field"),
    ("everything around us was green, and taller than anything at home",
     "en", "forest_trees"),
    ("il faisait nuit, et la lune eclairait tout le chemin",
     "fr", "moon_night"),
    ("ce qui frappait, c'etait le silence, et toute cette neige",
     "fr", "snow_winter"),
    ("on entendait les vagues bien avant de les voir",
     "fr", "ocean_waves"),
    ("we had been climbing for hours and it was still ahead of us",
     "en", "mountains_snow"),
    ("the smell of it reached us before anything else did",
     "en", "fire_flames"),
    ("nobody had walked here in a long time, you could tell",
     "en", "path_walking"),
    ("it was the kind of place where you lower your voice without meaning to",
     "en", "forest_trees"),
    ("she turned the handle and it gave way more easily than expected",
     "en", "door_entrance"),
    ("everything below was covered, and it had not stopped since morning",
     "en", "snow_winter"),
    ("the water went out much further than it had the day before",
     "en", "beach_sand"),
    ("from up there you could see all the way to the far side",
     "en", "mountain_lake"),
    ("there was nothing growing for as far as you could look",
     "en", "desert_dunes"),
    ("it had been ticking in that hallway for eighty years",
     "en", "clock_time"),
    ("the pages had gone soft at the corners from being turned so often",
     "en", "book_reading"),
    ("they went over in a long line, one after another, heading south",
     "en", "bird_flying"),
    ("on ne voyait plus que le sommet au-dessus des nuages",
     "fr", "mountains_snow"),
    ("le vent s'etait leve et tout le champ bougeait ensemble",
     "fr", "grass_meadow"),
    ("elle a pousse la porte et la lumiere est entree d'un coup",
     "fr", "window_light"),
    ("il n'y avait plus un bruit, juste l'eau qui bougeait a peine",
     "fr", "lake_calm"),
    ("la route continuait tout droit jusqu'a l'horizon",
     "fr", "road_highway"),
    ("les arbres etaient si hauts qu'on ne voyait pas le ciel",
     "fr", "forest_trees"),
    ("tout etait orange, et ca ne durerait que quelques minutes",
     "fr", "sunset_glow"),
    ("on l'entendait bruler bien avant d'arriver",
     "fr", "fire_flames"),
)


@dataclass(slots=True)
class StrategyResult:
    """Accuracy for one strategy on one corpus."""

    name: str
    top1: int = 0
    top3: int = 0
    total: int = 0
    misses: list[tuple[str, str]] = field(default_factory=list)

    @property
    def top1_rate(self) -> float:
        return self.top1 / self.total if self.total else 0.0

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.total if self.total else 0.0


def _normalise(vector: np.ndarray) -> np.ndarray:
    magnitude = float(np.linalg.norm(vector))
    return vector / magnitude if magnitude > 0 else vector


def _extracted_vector(
    text: str, language: str, embedder, cache: dict[str, np.ndarray]
) -> np.ndarray | None:  # type: ignore[no-untyped-def]
    """Weighted combination of the extracted phrases' vectors."""
    queries = extract_queries(text, language)
    if not queries:
        return None

    missing = [q.text for q in queries if q.text not in cache]
    if missing:
        for phrase, vector in zip(
            missing, embedder.embed_texts(missing), strict=True
        ):
            cache[phrase] = np.asarray(vector, dtype=np.float32)

    total_weight = sum(q.weight for q in queries) or 1.0
    combined = np.zeros(512, dtype=np.float32)
    for query in queries:
        combined += cache[query.text] * (query.weight / total_weight)

    return _normalise(combined)


def _score_ranked(
    ranked: list[str], expected: str, result: StrategyResult, label: str
) -> None:
    """Record one case against a ranked concept list."""
    result.total += 1
    if ranked and ranked[0] == expected:
        result.top1 += 1
    else:
        result.misses.append((label, ranked[0] if ranked else "(none)"))
    if expected in ranked[:3]:
        result.top3 += 1


def evaluate_corpus(
    cases: list[tuple[str, str, str]],
    image_vectors: np.ndarray,
    image_concepts: list[str],
    embedder,  # type: ignore[no-untyped-def]
    hybrid_weight: float = 0.5,
) -> dict[str, StrategyResult]:
    """Run every strategy over one set of cases.

    Args:
        cases: ``(text, language, expected_concept)``.
        image_vectors: Pool embeddings, one row per image.
        image_concepts: Concept key per image row.
        embedder: Provides ``embed_texts``.
        hybrid_weight: Weight on the extracted vector in hybrid-mean. 0.5 is
            an equal blend.

    Returns:
        One result per strategy.
    """
    results = {
        name: StrategyResult(name)
        for name in ("extracted", "full", "hybrid-mean", "hybrid-max")
    }

    phrase_cache: dict[str, np.ndarray] = {}

    texts = [text for text, _, _ in cases]
    full_vectors = np.asarray(embedder.embed_texts(texts), dtype=np.float32)

    for index, (text, language, expected) in enumerate(cases):
        label = text[:40]

        full = _normalise(full_vectors[index])
        extracted = _extracted_vector(text, language, embedder, phrase_cache)

        def ranked_for(vector: np.ndarray) -> list[str]:
            similarity = vector @ image_vectors.T
            order = np.argsort(-similarity)
            return [image_concepts[i] for i in order]

        # extracted: falls back to the full sentence when extraction is empty,
        # which is what the matcher does today.
        _score_ranked(
            ranked_for(extracted if extracted is not None else full),
            expected, results["extracted"], label,
        )

        _score_ranked(ranked_for(full), expected, results["full"], label)

        if extracted is None:
            blended = full
        else:
            blended = _normalise(
                extracted * hybrid_weight + full * (1.0 - hybrid_weight)
            )
        _score_ranked(ranked_for(blended), expected, results["hybrid-mean"], label)

        # hybrid-max: take whichever vector produces the stronger top match.
        if extracted is None:
            best_ranked = ranked_for(full)
        else:
            extracted_similarity = extracted @ image_vectors.T
            full_similarity = full @ image_vectors.T
            best = np.maximum(extracted_similarity, full_similarity)
            order = np.argsort(-best)
            best_ranked = [image_concepts[i] for i in order]
        _score_ranked(best_ranked, expected, results["hybrid-max"], label)

    return results


def load_human_cases() -> list[tuple[str, str, str]]:
    """Read the owner's queries, if present."""
    if not HUMAN_QUERIES.is_file():
        return []

    valid = {c.key for c in CONCEPTS}
    cases: list[tuple[str, str, str]] = []

    for raw in HUMAN_QUERIES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("«"):
            continue
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            continue
        if len(row) < 3:
            continue
        text, language, expected = (f.strip() for f in row[:3])
        if expected in valid and language.lower() in {"en", "fr"}:
            cases.append((text, language.lower(), expected))

    return cases


def report(title: str, results: dict[str, StrategyResult]) -> None:
    """Print one corpus's results."""
    any_result = next(iter(results.values()))
    print(f"\n{title}  (n={any_result.total})")
    print(f"  {'strategy':14} {'top-1':>8} {'top-3':>8}")
    print("  " + "-" * 32)
    for result in results.values():
        print(
            f"  {result.name:14} {result.top1_rate:7.0%} {result.top3_rate:8.0%}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--images",
        type=Path,
        default=Path(os.environ.get("TEMP", "/tmp")) / "voxframe_large_pool",
    )
    parser.add_argument(
        "--weight", type=float, default=0.5,
        help="Weight on the extracted vector in hybrid-mean (default 0.5).",
    )
    args = parser.parse_args()

    from voxframe.library.embeddings import Embedder

    pool = generate_large_pool(args.images)
    keys = sorted(pool)
    concepts = [key.rsplit("_v", 1)[0] for key in keys]

    embedder = Embedder(use_gpu=False)
    print(f"Pool: {len(pool)} images   Model: {embedder.model_id}")

    image_vectors = np.asarray(
        embedder.embed_images([pool[k] for k in keys]), dtype=np.float32
    )

    # 1. Direct phrasing: the eval concepts as written.
    direct = [(c.text_en, "en", c.key) for c in CONCEPTS]
    direct += [(c.text_fr, "fr", c.key) for c in CONCEPTS]
    report(
        "DIRECT phrasing (eval concepts, EN+FR)",
        evaluate_corpus(direct, image_vectors, concepts, embedder, args.weight),
    )

    # 2. Indirect phrasing: where extraction is expected to struggle.
    report(
        "INDIRECT phrasing (subject late or implied)",
        evaluate_corpus(
            list(INDIRECT_CASES), image_vectors, concepts, embedder, args.weight
        ),
    )

    # 3. The owner's own queries, when supplied.
    human = load_human_cases()
    if human:
        report(
            "HUMAN-WRITTEN queries",
            evaluate_corpus(human, image_vectors, concepts, embedder, args.weight),
        )
    else:
        print(
            f"\nHUMAN-WRITTEN queries: none yet — add rows to "
            f"{HUMAN_QUERIES.relative_to(REPO_ROOT)}"
        )

    print(
        "\nNote: this pool is generated vector art, so absolute numbers are\n"
        "optimistic. The comparison between strategies is the meaningful part."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
