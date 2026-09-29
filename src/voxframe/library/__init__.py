"""Asset library: storage, embeddings, and ingest."""

from voxframe.library.db import (
    EMBEDDING_DIM,
    AssetLibrary,
    EmbeddingModelMismatch,
    LibraryError,
)
from voxframe.library.embeddings import Embedder, EmbeddingError, cosine_similarity
from voxframe.library.ingest import (
    IMAGE_SUFFIXES,
    IngestError,
    IngestResult,
    ingest_directory,
    iter_images,
    reembed_library,
)

__all__ = [
    "EMBEDDING_DIM",
    "IMAGE_SUFFIXES",
    "AssetLibrary",
    "Embedder",
    "EmbeddingError",
    "EmbeddingModelMismatch",
    "IngestError",
    "IngestResult",
    "LibraryError",
    "cosine_similarity",
    "ingest_directory",
    "iter_images",
    "reembed_library",
]
