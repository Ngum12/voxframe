"""CLIP embeddings for images and text.

One model, LAION XLM-RoBERTa ViT-B-32, encodes both images and text into a
shared vector space. MIT-licensed weights and code, no usage restrictions
(D-008).

Why this model
--------------
Chosen by measurement over a 400-image pool with 50 queries per language
(D-040). Its French top-1 is 80% against 68% for the English-tower laion2b
checkpoint, and its French result is within 2 points of its English one rather
than 8 behind. The brief treats English and French as equals, so an 8-point
French deficit is a failure rather than a trade.

Using a single model whose towers were trained together also removes a whole
class of error. Pairing a text encoder with image weights it was not aligned to
produces near-random retrieval while every text-to-text sanity check still
looks healthy; that mistake was made and corrected during Phase 3 (D-034).

The model loads lazily and is cached per process: ~600 MB of weights would
otherwise make ``voxframe --help`` take half a minute.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import structlog

__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_OVERLAP_TOKENS",
    "CHUNK_TOKENS",
    "CHUNK_WORDS",
    "DEFAULT_MODEL_KEY",
    "EMBEDDING_DIM",
    "EMBEDDING_MODELS",
    "MAX_TEXT_TOKENS",
    "MODEL_NAME",
    "MODEL_PRETRAINED",
    "Embedder",
    "EmbeddingError",
    "cosine_similarity",
]

log = structlog.get_logger(__name__)

EMBEDDING_DIM = 512

#: Hard input limit of the text encoder, measured rather than assumed: the
#: tokenizer pads or truncates every input to exactly this many tokens, and
#: anything beyond is discarded **silently** (D-053).
#:
#: A scene at the default 8-second maximum holds roughly 20-28 spoken words,
#: which is well inside the limit. Longer scenes are possible through a
#: hand-edited plan or a slower pacing template, so the case is handled rather
#: than assumed away.
MAX_TEXT_TOKENS = 77

#: Tokens per chunk. Measured against the tokenizer rather than estimated from
#: word count (D-056): 40 French words tokenize to 169 tokens and 40 German
#: compounds to 212, both far past the limit, because subword splitting is far
#: more aggressive outside English.
#:
#: Sits below MAX_TEXT_TOKENS to leave room for the special tokens the
#: tokenizer adds.
CHUNK_TOKENS = 64

#: Tokens of overlap between chunks, so a subject spanning a boundary appears
#: whole in at least one chunk.
CHUNK_OVERLAP_TOKENS = 16

#: Fallback word counts, used only when no tokenizer is available — the
#: matcher never hits this path, but `_chunk_words` is importable on its own.
CHUNK_WORDS = 24
CHUNK_OVERLAP = 6

#: Available embedding models. Both are MIT-licensed LAION checkpoints whose
#: text and image towers were trained together, so each is internally
#: consistent by construction (D-040, D-044).
#:
#: Vectors from the two are NOT comparable. Switching is a detected, repairable
#: event rather than silent corruption -- see D-039 and `voxframe reembed`.
EMBEDDING_MODELS: dict[str, tuple[str, str, int, str]] = {
    # name: (open_clip model, pretrained tag, download MB, description)
    "default": (
        "xlm-roberta-base-ViT-B-32",
        "laion5b_s13b_b90k",
        1465,
        "Best accuracy, especially in French (80% vs 68% top-1).",
    ),
    "lite": (
        "ViT-B-32",
        "laion2b_s34b_b79k",
        605,
        "Less than half the download. English is comparable (76% vs 78%); "
        "French is materially weaker (68% vs 80% top-1).",
    ),
}

DEFAULT_MODEL_KEY = "default"


def _resolve(model_key: str) -> tuple[str, str]:
    """Look up a model by key, with the alternatives listed on error."""
    try:
        name, pretrained, _, _ = EMBEDDING_MODELS[model_key]
    except KeyError:
        available = ", ".join(sorted(EMBEDDING_MODELS))
        raise EmbeddingError(
            f"Unknown embedding model {model_key!r}. Available: {available}"
        ) from None
    return name, pretrained


#: Kept for callers that want the default without going through the table.
MODEL_NAME, MODEL_PRETRAINED = EMBEDDING_MODELS[DEFAULT_MODEL_KEY][:2]


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be computed."""


def _select_device(use_gpu: bool) -> str:
    """Pick a device, preferring CUDA when usable.

    CPU is the tested default (D-004); a missing or CPU-only torch is normal.
    """
    if not use_gpu:
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


class Embedder:
    """Encodes images and text into one shared vector space.

    Args:
        use_gpu: Use CUDA when available. Never required.
        batch_size: Images per forward pass. Small by default because a 4 GB
            card is easy to exhaust, and ingest is I/O-bound anyway.
    """

    def __init__(
        self,
        *,
        use_gpu: bool = True,
        batch_size: int = 8,
        model_key: str = DEFAULT_MODEL_KEY,
    ) -> None:
        self.device = _select_device(use_gpu)
        self.batch_size = batch_size
        self.model_key = model_key
        self.model_name, self.model_pretrained = _resolve(model_key)
        self._model: Any = None
        self._preprocess: Any = None
        self._tokenizer: Any = None

    @property
    def model_id(self) -> str:
        """Identifier for cache keys and the scene plan.

        An embedding is only comparable to others made by the same model, so
        this is recorded wherever embeddings are stored.
        """
        return f"{self.model_name}/{self.model_pretrained}"

    def _load(self) -> tuple[Any, Any, Any]:
        """Load the model, downloading weights on first use."""
        if self._model is not None:
            return self._model, self._preprocess, self._tokenizer

        try:
            import open_clip
        except ImportError as exc:
            raise EmbeddingError(
                "open_clip_torch is not installed.\n"
                "  pip install 'voxframe[library]'"
            ) from exc

        log.info(
            "embed.model.loading",
            model=self.model_name,
            variant=self.model_key,
            device=self.device,
        )

        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.model_pretrained, device=self.device
        )
        model.eval()

        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(self.model_name)

        return self._model, self._preprocess, self._tokenizer

    def embed_image(self, image_path: Path) -> list[float]:
        """Embed one image file."""
        return self.embed_images([image_path])[0]

    def embed_images(self, image_paths: Sequence[Path]) -> list[list[float]]:
        """Embed several images, batched.

        Args:
            image_paths: Files to embed.

        Returns:
            One L2-normalised vector per input, in the same order.

        Raises:
            EmbeddingError: If a file cannot be read as an image.
        """
        if not image_paths:
            return []

        import torch
        from PIL import Image

        model, preprocess, _ = self._load()
        vectors: list[list[float]] = []

        for start in range(0, len(image_paths), self.batch_size):
            batch_paths = image_paths[start : start + self.batch_size]
            tensors = []

            for path in batch_paths:
                try:
                    with Image.open(path) as image:
                        # CLIP expects RGB; PNGs with alpha and greyscale JPEGs
                        # both appear in real libraries.
                        tensors.append(preprocess(image.convert("RGB")))
                except (OSError, ValueError) as exc:
                    raise EmbeddingError(f"Could not read image {path}: {exc}") from exc

            batch = torch.stack(tensors).to(self.device)

            with torch.no_grad():
                features = model.encode_image(batch)
                # Normalising makes the dot product equal cosine similarity,
                # which the library's distance conversion assumes.
                features /= features.norm(dim=-1, keepdim=True)

            vectors.extend(features.cpu().numpy().astype(np.float32).tolist())

        return vectors

    def token_count(self, text: str) -> int:
        """How many tokens the encoder will see for this text.

        Counts the tokenizer's real output rather than the padded tensor, so
        the result reflects whether truncation would occur.
        """
        _, _, tokenizer = self._load()
        try:
            return len(tokenizer.tokenizer(text, add_special_tokens=True)["input_ids"])
        except (AttributeError, TypeError):
            # A tokenizer without the HuggingFace interface: approximate from
            # word count rather than fail. Only used for the truncation
            # warning, so an estimate is acceptable.
            return int(len(text.split()) * 1.4) + 2

    def embed_text(self, text: str) -> list[float]:
        """Embed one text query, chunking if it exceeds the encoder's limit.

        The encoder truncates at :data:`MAX_TEXT_TOKENS` **without warning**, so
        a long scene would silently search on its opening words alone. Long
        text is therefore split into overlapping chunks, each embedded, and the
        results averaged.

        Averaging rather than taking the best chunk: without a query to score
        against there is no basis for choosing one, and the mean of a scene's
        parts is a reasonable summary of the scene. The alternative — searching
        with each chunk and keeping the best hit — is available to callers via
        :meth:`embed_text_chunks`, which is what the matcher uses.
        """
        if self.token_count(text) <= MAX_TEXT_TOKENS:
            return self.embed_texts([text])[0]

        chunks = _chunk_by_tokens(text, self.token_count)
        log.info(
            "embed.text.chunked",
            tokens=self.token_count(text),
            limit=MAX_TEXT_TOKENS,
            chunks=len(chunks),
            preview=text[:60],
        )

        vectors = np.asarray(self.embed_texts(chunks), dtype=np.float32)
        mean = vectors.mean(axis=0)
        magnitude = float(np.linalg.norm(mean))
        if magnitude > 0:
            mean = mean / magnitude

        result: list[float] = mean.tolist()
        return result

    def embed_text_chunks(self, text: str) -> list[list[float]]:
        """Embed text as one vector per chunk.

        Returns a single vector for text within the limit. Callers that can
        score each vector — the matcher, which searches with each and keeps the
        best hit — should prefer this over :meth:`embed_text`, because
        averaging blurs a scene that changes subject partway through.
        """
        if self.token_count(text) <= MAX_TEXT_TOKENS:
            return [self.embed_texts([text])[0]]

        chunks = _chunk_by_tokens(text, self.token_count)
        log.info(
            "embed.text.chunked",
            tokens=self.token_count(text),
            limit=MAX_TEXT_TOKENS,
            chunks=len(chunks),
            preview=text[:60],
        )
        return self.embed_texts(chunks)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed several text queries.

        English and French are the tested languages (D-040). The XLM-RoBERTa
        tokenizer covers roughly 100 languages; others should work but are
        not measured.

        Returns:
            One L2-normalised vector per input, comparable against image
            embeddings.
        """
        if not texts:
            return []

        import torch

        model, _, tokenizer = self._load()

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size * 4):
            batch = list(texts[start : start + self.batch_size * 4])
            tokens = tokenizer(batch).to(self.device)

            with torch.no_grad():
                features = model.encode_text(tokens)
                features /= features.norm(dim=-1, keepdim=True)

            array = features.cpu().numpy().astype(np.float32)
            if array.shape[1] != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"Text encoder produced {array.shape[1]} dimensions, "
                    f"expected {EMBEDDING_DIM}."
                )
            vectors.extend(array.tolist())

        return vectors

    def cache_key(self, content_hash: str) -> str:
        """Cache key for an embedding of this content under this model."""
        combined = f"{content_hash}/{self.model_id}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _chunk_by_tokens(
    text: str,
    count_tokens: Callable[[str], int],
    size: int = CHUNK_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Split text into overlapping chunks bounded by *token* count.

    Word count is a poor proxy for token count and fails unevenly across
    languages: 40 English words measured at 48 tokens, the same number of
    French words at 169 and German compounds at 212 (D-056). Splitting on
    words therefore guarantees nothing.

    Chunks are still built from whole words — a chunk ending mid-word would
    embed a fragment — but the *limit* is measured in tokens.

    Args:
        text: The text to split.
        count_tokens: Returns the token count for a string. Injected so this
            stays testable without loading a model.
        size: Maximum tokens per chunk.
        overlap: Tokens of context to repeat between chunks.

    Returns:
        Chunks in order, each within ``size`` tokens. Always at least one.
    """
    if count_tokens(text) <= size:
        return [text]

    words = text.split()
    if len(words) <= 1:
        # A single word over the limit cannot be split further without
        # cutting mid-word, which would embed a fragment. Returned whole and
        # left for the encoder to truncate: one over-long word is a
        # pathological input, not a case worth degrading.
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(words):
        # Grow the chunk word by word until one more would exceed the limit.
        end = start
        while end < len(words):
            candidate = " ".join(words[start : end + 1])
            if count_tokens(candidate) > size:
                break
            end += 1

        # Always take at least one word, or a single over-long word loops.
        end = max(end, start + 1)
        chunks.append(" ".join(words[start:end]))

        if end >= len(words):
            break

        # Step back by however many words fit within the overlap budget,
        # measured in tokens rather than assumed from a word count.
        back = 0
        while back < end - start - 1:
            tail = " ".join(words[end - back - 1 : end])
            if count_tokens(tail) > overlap:
                break
            back += 1

        start = max(end - back, start + 1)

    return chunks


def _chunk_words(
    text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping word chunks.

    A conservative fallback for callers without a tokenizer. The production
    path uses :func:`_chunk_by_tokens`, which bounds chunks by the measure
    that actually matters.
    """
    words = text.split()
    if len(words) <= size:
        return [text]

    step = max(1, size - overlap)
    chunks = [
        " ".join(words[start : start + size]) for start in range(0, len(words), step)
    ]

    if len(chunks) > 1 and len(words) - (len(chunks) - 1) * step < overlap:
        chunks.pop()

    return chunks


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two normalised vectors.

    Equivalent to a dot product for normalised inputs, but tolerates unnormalised
    ones so callers need not remember which they hold.
    """
    first = np.asarray(a, dtype=np.float32)
    second = np.asarray(b, dtype=np.float32)

    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator == 0.0:
        return 0.0

    return float(np.dot(first, second) / denominator)
