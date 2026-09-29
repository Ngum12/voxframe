#!/usr/bin/env python3
"""Compare encoder pairings on the retrieval evaluation.

Run against **real photographs** to decide anything::

    python scripts/fetch_eval_images.py
    python scripts/run_large_eval.py --images <pool>

Run against the generated pool for a fast offline regression check::

    python scripts/run_large_eval.py

The generated pool (50 concepts x 8 variants = 400 images) is flat vector art
and cannot justify a model choice: a matcher keying on dominant colour would
score well on it (D-043). It is fast and deterministic, which makes it useful
for catching regressions, and misleading for anything else.

The earlier 12-case eval could not separate the models: one case was 8
percentage points, so a 92% vs 83% difference was a single image. This pool is
large enough for the differences to mean something, and its distractors are
chosen to be hard: near-duplicate variants of each concept, plus confusable
concept pairs (sun/moon, forest/farmland, lake/ocean).

Scoring
-------
- **top-1**: the highest-ranked image is a variant of the correct concept.
- **top-3**: a variant of the correct concept, or of a concept explicitly
  marked confusable with it, appears in the first three.

Any variant counts, because all eight depict the concept; asking for one
specific variant would measure near-duplicate discrimination rather than
retrieval.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.generate import CONCEPTS, generate_large_pool  # noqa: E402


@dataclass(slots=True)
class Result:
    """Measured accuracy for one pairing."""

    name: str
    license_note: str
    download_mb: int

    top1_en: float = 0.0
    top3_en: float = 0.0
    top1_fr: float = 0.0
    top3_fr: float = 0.0

    ms_per_image: float = 0.0
    ms_per_text: float = 0.0

    misses_en: list[tuple[str, str]] = field(default_factory=list)
    misses_fr: list[tuple[str, str]] = field(default_factory=list)

    @property
    def french_gap(self) -> float:
        return self.top1_en - self.top1_fr


def _score(
    text_vectors: np.ndarray,
    image_vectors: np.ndarray,
    image_keys: list[str],
) -> tuple[float, float, list[tuple[str, str]]]:
    """Score one language against the pool."""
    similarity = text_vectors @ image_vectors.T

    top1 = top3 = 0
    misses: list[tuple[str, str]] = []

    for index, concept in enumerate(CONCEPTS):
        order = np.argsort(-similarity[index])
        # An image key is "{concept}_v{n}"; strip the variant suffix.
        ranked = [image_keys[i].rsplit("_v", 1)[0] for i in order]

        if ranked[0] == concept.key:
            top1 += 1
        else:
            misses.append((concept.key, ranked[0]))

        allowed = {concept.key, *concept.confusable}
        if allowed & set(ranked[:3]):
            top3 += 1

    total = len(CONCEPTS)
    return top1 / total, top3 / total, misses


def evaluate(pair, image_paths: dict[str, Path]) -> Result:  # type: ignore[no-untyped-def]
    """Measure one pairing over the whole pool."""
    result = Result(pair.name, pair.license_note, pair.download_mb)

    keys = sorted(image_paths)
    paths = [image_paths[key] for key in keys]

    started = time.monotonic()
    image_vectors = pair.embed_images(paths)
    result.ms_per_image = (time.monotonic() - started) * 1000 / len(paths)

    texts_en = [c.text_en for c in CONCEPTS]
    texts_fr = [c.text_fr for c in CONCEPTS]

    started = time.monotonic()
    vectors_en = pair.embed_texts(texts_en)
    vectors_fr = pair.embed_texts(texts_fr)
    result.ms_per_text = (
        (time.monotonic() - started) * 1000 / (len(texts_en) + len(texts_fr))
    )

    result.top1_en, result.top3_en, result.misses_en = _score(
        vectors_en, image_vectors, keys
    )
    result.top1_fr, result.top3_fr, result.misses_fr = _score(
        vectors_fr, image_vectors, keys
    )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="Run matching pairings only.")
    parser.add_argument(
        "--images",
        type=Path,
        default=Path(os.environ.get("TEMP", "/tmp")) / "voxframe_large_pool",
    )
    args = parser.parse_args()

    from tests.eval.retrieval import make_distilled_pair, make_openclip_pair

    pool = generate_large_pool(args.images)
    print(f"Pool: {len(pool)} images, {len(CONCEPTS)} concepts")
    print(f"Queries: {len(CONCEPTS)} English, {len(CONCEPTS)} French\n")

    builders = [
        ("laion2b", lambda: make_openclip_pair(
            "ViT-B-32", "laion2b_s34b_b79k",
            "LAION laion2b", "MIT weights and code, no usage restrictions.", 605)),
        ("openai-multilingual", lambda: make_distilled_pair(
            "clip-ViT-B-32", "sentence-transformers/clip-ViT-B-32-multilingual-v1",
            "OpenAI B/32 + multilingual-v1",
            "Code MIT; model card places any deployed use out of scope.", 1144)),
        ("xlm-roberta", lambda: make_openclip_pair(
            "xlm-roberta-base-ViT-B-32", "laion5b_s13b_b90k",
            "LAION XLM-RoBERTa", "MIT. Natively multilingual.", 1465)),
    ]

    results: list[Result] = []
    for label, build in builders:
        if args.only and not any(t.lower() in label for t in args.only):
            continue

        print(f"--- {label}", flush=True)
        try:
            result = evaluate(build(), pool)
            results.append(result)
            print(
                f"    top-1  EN {result.top1_en:.0%}  FR {result.top1_fr:.0%}"
                f"    top-3  EN {result.top3_en:.0%}  FR {result.top3_fr:.0%}"
                f"    ({result.ms_per_image:.0f} ms/img)",
                flush=True,
            )
        except Exception as exc:
            print(f"    FAILED: {type(exc).__name__}: {exc}", flush=True)
        print(flush=True)

    if not results:
        return 1

    print(f"Retrieval over {len(CONCEPTS)} queries per language, "
          f"{len(pool)}-image pool.\n")
    print("| Pairing | top-1 EN | top-3 EN | top-1 FR | top-3 FR | FR gap | MB |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r.name} | {r.top1_en:.0%} | {r.top3_en:.0%} "
            f"| {r.top1_fr:.0%} | {r.top3_fr:.0%} | {r.french_gap:+.0%} "
            f"| {r.download_mb} |"
        )

    print("\nMisses (English):")
    for r in results:
        print(f"  {r.name}: {r.misses_en[:6]}")
    print("\nMisses (French):")
    for r in results:
        print(f"  {r.name}: {r.misses_fr[:6]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
