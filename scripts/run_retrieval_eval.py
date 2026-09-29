#!/usr/bin/env python3
"""Compare encoder pairings on text-to-image retrieval.

Run::

    python scripts/run_retrieval_eval.py

Downloads several CLIP variants on first run (a few GB in total), then reports
top-1 and top-3 accuracy in English and French, download size, and embedding
speed, so the encoder choice is made by measurement rather than by reading
model cards.
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tests.eval.dataset import EVAL_CASES  # noqa: E402
from tests.eval.images import generate_pool  # noqa: E402
from tests.eval.retrieval import (  # noqa: E402
    evaluate_pairing,
    format_report,
    make_distilled_pair,
    make_openclip_pair,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", nargs="*", help="Run only pairings whose label contains this text."
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=Path(os.environ.get("TEMP", "/tmp")) / "voxframe_eval_pool",
    )
    args = parser.parse_args()

    image_paths = generate_pool(args.images)
    print(f"Image pool: {len(image_paths)} images in {args.images}")
    print(f"Cases: {len(EVAL_CASES)} (English and French)\n")

    builders = [
        (
            "LAION laion2b + LAION text (D-008 as built)",
            lambda: make_openclip_pair(
                "ViT-B-32",
                "laion2b_s34b_b79k",
                "LAION laion2b (English text tower)",
                "MIT weights, MIT code. English-only text tower.",
                605,
            ),
        ),
        (
            "OpenAI ViT-B/32 + multilingual distilled (D-034 corrected)",
            lambda: make_distilled_pair(
                "clip-ViT-B-32",
                "sentence-transformers/clip-ViT-B-32-multilingual-v1",
                "OpenAI ViT-B/32 + multilingual-v1",
                "Code MIT; model card places ANY deployed use out of scope.",
                605 + 539,
            ),
        ),
        (
            "LAION XLM-RoBERTa (natively multilingual)",
            lambda: make_openclip_pair(
                "xlm-roberta-base-ViT-B-32",
                "laion5b_s13b_b90k",
                "LAION XLM-RoBERTa ViT-B-32",
                "MIT. Text and image towers trained together.",
                1465,
            ),
        ),
    ]

    results = []
    for label, build in builders:
        if args.only and not any(token.lower() in label.lower() for token in args.only):
            continue

        print(f"--- {label}")
        try:
            pair = build()
            result = evaluate_pairing(pair, image_paths)
            results.append(result)
            print(
                f"    top-1 EN {result.top1_en:.0%}  FR {result.top1_fr:.0%}   "
                f"top-3 EN {result.top3_en:.0%}  FR {result.top3_fr:.0%}   "
                f"({result.ms_per_image:.0f} ms/img)"
            )
            if result.misses_en:
                print(f"    EN misses: {result.misses_en}")
            if result.misses_fr:
                print(f"    FR misses: {result.misses_fr}")
        except Exception as exc:
            print(f"    FAILED: {type(exc).__name__}: {exc}")
        print()

    if results:
        print(format_report(results, len(EVAL_CASES)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
