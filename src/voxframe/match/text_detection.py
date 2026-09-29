"""Does an image show printed text? Asked of the embedding model already loaded.

Captions are burned over the image, so a photograph whose subject is printed
words -- "100%", "ACHIEVE", a page of scripture -- puts two blocks of text on
screen at once (D-082). The original detector read an image's tags, and in a
real sourced library every tag was just the source's name, so it caught nothing
(D-133).

This one looks at the image: its embedding is compared with prompts describing
printed text and prompts describing ordinary photographs, and the share of the
softmax on the text side is its score. Zero-shot, on the model that already
matches scenes -- no new dependency and no download.

**Measured before it was used** (``scripts/eval_text_images.py``, 121 labelled
images including the three that prompted it). On the standard model, at 0.7:
12 of 16 text images flagged, 0 of 88 ordinary photographs; "EARTH" 0.999,
"ACHIEVE" 0.997, "100%" 0.940. The four it misses are images where text is not
the dominant subject: a meme's caption strip, a rotated slogan on a graphic, a
disc of tiny type, a slide behind a speaker.

The threshold is per embedding model, because the models score differently --
the same lesson as the matching threshold (D-089). A model without a measured
threshold flags nothing rather than guessing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

__all__ = [
    "LOGIT_SCALE",
    "PHOTO_PROMPTS",
    "TEXT_PROMPTS",
    "TEXT_THRESHOLDS",
    "TextDetector",
    "text_score",
]

#: The owner's suggested prompts, plus close variants. Several per side are
#: steadier than any single phrasing.
TEXT_PROMPTS: tuple[str, ...] = (
    "a photo of text",
    "a sign with words",
    "a document or screenshot",
    "large printed letters",
    "a word spelled out",
)

#: What the rest of a stock library looks like. Without a varied negative side,
#: "a photo of text" wins by default on anything that is not obviously a scene.
PHOTO_PROMPTS: tuple[str, ...] = (
    "a photo",
    "a photo of a landscape",
    "a photo of people",
    "a photo of an animal",
    "a photo of an object",
    "a photo of food",
)

#: CLIP's learned temperature. A softmax over raw cosine similarities is nearly
#: flat; the model was trained with its logits scaled by about this.
LOGIT_SCALE = 100.0

#: Score at or above which an image counts as showing printed text, by the
#: embedding model's key (``Settings.embed_model``). Only measured models are
#: listed; see the module docstring and D-133 for the numbers behind each.
TEXT_THRESHOLDS: dict[str, float] = {
    # 12/16 text images, 0/88 photographs. AUC 0.967.
    "default": 0.7,
    # 9/16 text images, 0/88 photographs -- the lowest threshold with no false
    # alarms. Weaker recall than the standard model (AUC 0.932), but it still
    # catches "100%" (0.953), "EARTH" (0.970) and "ACHIEVE" (0.996).
    "lite": 0.5,
}


class _Embeds(Protocol):
    """The part of the embedder this needs."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalise(vector: Sequence[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def text_score(image: Sequence[float], prompts: Sequence[Sequence[float]]) -> float:
    """Share of probability on the text prompts, from 0 to 1.

    Args:
        image: The image's embedding.
        prompts: Normalised embeddings of :data:`TEXT_PROMPTS` followed by
            :data:`PHOTO_PROMPTS`, in that order.
    """
    unit = _normalise(image)
    logits = [
        LOGIT_SCALE * sum(a * b for a, b in zip(unit, prompt, strict=True))
        for prompt in prompts
    ]
    peak = max(logits)
    weights = [math.exp(value - peak) for value in logits]
    return sum(weights[: len(TEXT_PROMPTS)]) / sum(weights)


class TextDetector:
    """Scores image embeddings for printed text, for one embedding model."""

    def __init__(self, embedder: _Embeds, model_key: str) -> None:
        """
        Args:
            embedder: Embeds the prompts, once, on first use.
            model_key: Which embedding model produced the image vectors. Picks
                the threshold; an unmeasured model flags nothing.
        """
        self._embedder = embedder
        self.threshold = TEXT_THRESHOLDS.get(model_key)
        self._prompts: list[list[float]] | None = None

    @property
    def available(self) -> bool:
        """Whether this model has a measured threshold."""
        return self.threshold is not None

    def _prompt_vectors(self) -> list[list[float]]:
        if self._prompts is None:
            raw = self._embedder.embed_texts([*TEXT_PROMPTS, *PHOTO_PROMPTS])
            self._prompts = [_normalise(vector) for vector in raw]
        return self._prompts

    def score(self, image: Sequence[float]) -> float:
        return text_score(image, self._prompt_vectors())

    def shows_text(self, image: Sequence[float]) -> bool:
        """Whether an image's subject is printed text."""
        if self.threshold is None:
            return False
        return self.score(image) >= self.threshold
