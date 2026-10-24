"""Subsistema vetorial opt-in (lf-0039 / Cycle 4 Ph4)."""

from code_atlas.search.vector.contracts import (
    EmbeddingModelInfo,
    EmbeddingProvider,
    VectorCapability,
    VectorIndex,
    VectorSearchResult,
)
from code_atlas.search.vector.embeddings import (
    SqliteEmbeddingCache,
    content_hash,
    embed_texts_cached,
    index_text_for,
)
from code_atlas.search.vector.index import (
    BruteForceIndex,
    sqlite_vec_available,
)
from code_atlas.search.vector.provider import NaiveEmbeddingProvider

__all__ = [
    "BruteForceIndex",
    "EmbeddingModelInfo",
    "EmbeddingProvider",
    "NaiveEmbeddingProvider",
    "SqliteEmbeddingCache",
    "VectorCapability",
    "VectorIndex",
    "VectorSearchResult",
    "content_hash",
    "embed_texts_cached",
    "index_text_for",
    "sqlite_vec_available",
]
