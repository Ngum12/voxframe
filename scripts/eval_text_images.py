"""Can the embedding model already loaded tell images of printed text apart? (D-133)

Zero-shot classification: each image's embedding is compared with a handful of
text prompts describing printed text and a handful describing ordinary photos.
The share of probability on the text prompts is the image's text score.

No new dependency and no download: it uses the same model that matches scenes.
Measured on a labelled set (``tests/eval/text_images.csv``) that includes the
images that prompted this -- "100%", "ACHIEVE", "EARTH" -- alongside ordinary
photographs, and a separate "incidental" group (book pages, shelves, signage)
reported apart so it can neither flatter nor sink the result.

    python scripts/eval_text_images.py [--model default|lite]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

LABELS = REPO_ROOT / "tests" / "eval" / "text_images.csv"
LIBRARIES = REPO_ROOT / "demo_output" / "p4b"

# The prompts and scale live with the detector the app uses, so what is
# measured here is exactly what runs.
from voxframe.match.text_detection import (  # noqa: E402
    PHOTO_PROMPTS,
    TEXT_PROMPTS,
    text_score,
)


def load_labels() -> list[tuple[str, str, str, str]]:
    with LABELS.open(encoding="utf-8") as handle:
        rows = [line for line in handle if not line.startswith("#")]
    return [
        (row["asset_id"], row["library"], row["file"], row["label"])
        for row in csv.DictReader(rows)
    ]


def text_scores(model: str) -> dict[str, float]:
    from voxframe.library.db import AssetLibrary
    from voxframe.library.embeddings import Embedder

    labels = load_labels()
    paths: dict[str, Path] = {}
    for library in {row[1] for row in labels}:
        for asset in AssetLibrary(LIBRARIES / library).all_assets():
            paths[asset.id] = asset.path

    missing = [row for row in labels if row[0] not in paths]
    if missing:
        print(f"{len(missing)} labelled images are not on this machine; skipped")

    embedder = Embedder(use_gpu=False, model_key=model)
    ids = [row[0] for row in labels if row[0] in paths]

    started = time.monotonic()
    images = embedder.embed_images([paths[i] for i in ids])
    prompts = embedder.embed_texts([*TEXT_PROMPTS, *PHOTO_PROMPTS])
    print(
        f"embedded {len(ids)} images with {embedder.model_id} "
        f"in {time.monotonic() - started:.0f}s"
    )

    def normalise(vector: list[float]) -> list[float]:
        length = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / length for v in vector]

    prompts = [normalise(p) for p in prompts]
    return {
        asset_id: text_score(image, prompts)
        for asset_id, image in zip(ids, images, strict=True)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="default")
    arguments = parser.parse_args()

    labels = load_labels()
    scores = text_scores(arguments.model)
    by_label: dict[str, list[tuple[float, str]]] = {"text": [], "incidental": [], "none": []}
    for asset_id, _, file, label in labels:
        if asset_id in scores:
            by_label[label].append((scores[asset_id], file))

    for label, items in by_label.items():
        values = sorted(score for score, _ in items)
        if values:
            print(
                f"{label:10} n={len(values):3}  min {values[0]:.3f}  "
                f"median {values[len(values) // 2]:.3f}  max {values[-1]:.3f}"
            )

    text = [score for score, _ in by_label["text"]]
    none = [score for score, _ in by_label["none"]]
    incidental = [score for score, _ in by_label["incidental"]]

    # Probability a random text image outscores a random ordinary photo.
    pairs = [(t > n) + 0.5 * (t == n) for t in text for n in none]
    print(f"\nseparation (AUC, text vs none): {sum(pairs) / len(pairs):.3f}")

    print("\nthreshold | text flagged | none flagged | incidental flagged")
    print("---|---|---|---")
    for threshold in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        flagged_text = sum(score >= threshold for score in text)
        flagged_none = sum(score >= threshold for score in none)
        flagged_inc = sum(score >= threshold for score in incidental)
        print(
            f"{threshold:.1f} | {flagged_text}/{len(text)} "
            f"({flagged_text / len(text):.0%}) | {flagged_none}/{len(none)} "
            f"({flagged_none / len(none):.0%}) | {flagged_inc}/{len(incidental)}"
        )

    print("\nnamed cases:")
    for score, file in sorted(by_label["text"], reverse=True):
        print(f"  text  {score:.3f}  {file}")
    print("  highest-scoring ordinary photos:")
    for score, file in sorted(by_label["none"], reverse=True)[:6]:
        print(f"  none  {score:.3f}  {file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
