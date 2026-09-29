"""Measure text-to-image retrieval for competing encoder pairings.

This exists because a text-to-text comparison cannot detect a text↔image space
mismatch. D-034 originally paired a text encoder distilled against **OpenAI**
CLIP with **LAION** image weights, and the EN/FR similarity numbers looked fine
because they never crossed the modality boundary. Only retrieval catches that.

Each pairing embeds the whole image pool and every scene text, then ranks
images per text. Reported per language:

- **top-1**: the correct image ranks first
- **top-3**: the correct image, or an explicitly acceptable alternative, is in
  the first three
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from tests.eval.dataset import EVAL_CASES, EvalCase

__all__ = ["EncoderPair", "PairingResult", "evaluate_pairing", "format_report"]


class EncoderPair(Protocol):
    """A text encoder and image encoder that claim to share a vector space."""

    name: str
    license_note: str
    download_mb: int

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        """Return one L2-normalised row per image."""
        ...

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Return one L2-normalised row per text."""
        ...


@dataclass(slots=True)
class PairingResult:
    """Measured accuracy and cost for one encoder pairing."""

    name: str
    license_note: str
    download_mb: int

    top1_en: float = 0.0
    top3_en: float = 0.0
    top1_fr: float = 0.0
    top3_fr: float = 0.0

    image_embed_seconds: float = 0.0
    text_embed_seconds: float = 0.0
    images_embedded: int = 0
    texts_embedded: int = 0

    #: Cases the pairing got wrong, for inspection rather than scoring.
    misses_en: list[tuple[str, str]] = field(default_factory=list)
    misses_fr: list[tuple[str, str]] = field(default_factory=list)

    @property
    def top1_mean(self) -> float:
        return (self.top1_en + self.top1_fr) / 2

    @property
    def french_gap(self) -> float:
        """How much worse French is than English, in top-1 points.

        The number that matters for this project: a pairing strong in English
        and weak in French fails one of the two required languages.
        """
        return self.top1_en - self.top1_fr

    @property
    def ms_per_image(self) -> float:
        if not self.images_embedded:
            return 0.0
        return self.image_embed_seconds * 1000 / self.images_embedded

    @property
    def ms_per_text(self) -> float:
        if not self.texts_embedded:
            return 0.0
        return self.text_embed_seconds * 1000 / self.texts_embedded


def _rank_hits(
    text_vectors: np.ndarray,
    image_vectors: np.ndarray,
    image_keys: list[str],
    cases: tuple[EvalCase, ...],
) -> tuple[float, float, list[tuple[str, str]]]:
    """Score one language.

    Returns:
        ``(top1, top3, misses)`` where misses are ``(case_key, predicted)``.
    """
    similarity = text_vectors @ image_vectors.T

    top1_hits = 0
    top3_hits = 0
    misses: list[tuple[str, str]] = []

    for index, case in enumerate(cases):
        order = np.argsort(-similarity[index])
        ranked = [image_keys[i] for i in order]

        if ranked[0] == case.correct:
            top1_hits += 1
        else:
            misses.append((case.key, ranked[0]))

        # An explicitly acceptable alternative counts for top-3: a human would
        # not call "snowy mountains" for a winter scene a failure.
        allowed = {case.correct, *case.acceptable}
        if allowed & set(ranked[:3]):
            top3_hits += 1

    total = len(cases)
    return top1_hits / total, top3_hits / total, misses


def evaluate_pairing(
    pair: EncoderPair,
    image_paths: dict[str, Path],
    cases: tuple[EvalCase, ...] = EVAL_CASES,
) -> PairingResult:
    """Run the eval for one pairing.

    Args:
        pair: The encoders to test.
        image_paths: Image key to file path, covering the whole pool.
        cases: Retrieval cases.

    Returns:
        Accuracy per language, plus embedding timings.
    """
    result = PairingResult(
        name=pair.name,
        license_note=pair.license_note,
        download_mb=pair.download_mb,
    )

    image_keys = sorted(image_paths)
    ordered_paths = [image_paths[key] for key in image_keys]

    started = time.monotonic()
    image_vectors = pair.embed_images(ordered_paths)
    result.image_embed_seconds = time.monotonic() - started
    result.images_embedded = len(ordered_paths)

    texts_en = [case.text_en for case in cases]
    texts_fr = [case.text_fr for case in cases]

    started = time.monotonic()
    vectors_en = pair.embed_texts(texts_en)
    vectors_fr = pair.embed_texts(texts_fr)
    result.text_embed_seconds = time.monotonic() - started
    result.texts_embedded = len(texts_en) + len(texts_fr)

    result.top1_en, result.top3_en, result.misses_en = _rank_hits(
        vectors_en, image_vectors, image_keys, cases
    )
    result.top1_fr, result.top3_fr, result.misses_fr = _rank_hits(
        vectors_fr, image_vectors, image_keys, cases
    )

    return result


def format_report(results: list[PairingResult], case_count: int) -> str:
    """Render results as a markdown table for the phase report."""
    lines = [
        f"Retrieval over {case_count} cases against a {case_count}-image pool.",
        "",
        "| Pairing | top-1 EN | top-3 EN | top-1 FR | top-3 FR | FR gap | MB | ms/img | ms/text |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for result in results:
        lines.append(
            f"| {result.name} "
            f"| {result.top1_en:.0%} | {result.top3_en:.0%} "
            f"| {result.top1_fr:.0%} | {result.top3_fr:.0%} "
            f"| {result.french_gap:+.0%} "
            f"| {result.download_mb} "
            f"| {result.ms_per_image:.0f} | {result.ms_per_text:.0f} |"
        )

    lines.append("")
    for result in results:
        lines.append(f"- **{result.name}** — {result.license_note}")

    return "\n".join(lines)


def make_openclip_pair(
    model_name: str,
    pretrained: str,
    label: str,
    license_note: str,
    download_mb: int,
    device: str = "cpu",
) -> Any:
    """Build a pairing that uses open_clip for both modalities.

    Used for natively-trained models, where the text and image towers were
    trained together and are known to share a space.
    """
    import open_clip
    import torch
    from PIL import Image

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained, device=device
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(model_name)

    class _Pair:
        name = label
        license_note_ = license_note

        def __init__(self) -> None:
            self.name = label
            self.license_note = license_note
            self.download_mb = download_mb

        def embed_images(self, paths: list[Path]) -> np.ndarray:
            tensors = [preprocess(Image.open(p).convert("RGB")) for p in paths]
            batch = torch.stack(tensors).to(device)
            with torch.no_grad():
                features = model.encode_image(batch)
                features /= features.norm(dim=-1, keepdim=True)
            return features.cpu().numpy().astype(np.float32)

        def embed_texts(self, texts: list[str]) -> np.ndarray:
            tokens = tokenizer(texts).to(device)
            with torch.no_grad():
                features = model.encode_text(tokens)
                features /= features.norm(dim=-1, keepdim=True)
            return features.cpu().numpy().astype(np.float32)

    return _Pair()


def make_distilled_pair(
    image_model: str,
    text_model: str,
    label: str,
    license_note: str,
    download_mb: int,
    device: str = "cpu",
) -> Any:
    """Build a pairing using sentence-transformers for both sides.

    Used for the distilled multilingual encoder, whose image counterpart must
    be the model it was distilled against.
    """
    from PIL import Image
    from sentence_transformers import SentenceTransformer

    image_encoder = SentenceTransformer(image_model, device=device)
    text_encoder = SentenceTransformer(text_model, device=device)

    class _Pair:
        def __init__(self) -> None:
            self.name = label
            self.license_note = license_note
            self.download_mb = download_mb

        def embed_images(self, paths: list[Path]) -> np.ndarray:
            images = [Image.open(p).convert("RGB") for p in paths]
            vectors = image_encoder.encode(
                images, normalize_embeddings=True, show_progress_bar=False
            )
            return np.asarray(vectors, dtype=np.float32)

        def embed_texts(self, texts: list[str]) -> np.ndarray:
            vectors = text_encoder.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            return np.asarray(vectors, dtype=np.float32)

    return _Pair()
