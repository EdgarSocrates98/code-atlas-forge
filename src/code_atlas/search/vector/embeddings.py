"""EmbeddingCache + build de embeddings por snapshot (lf-0039).

Cache por ``content_hash`` (sha256 do texto de indexação): rebuild só
quando conteúdo OU modelo mudam — PK ``(model_id, model_version,
content_hash)`` torna o versionamento estrutural, não convenção.
"""

from __future__ import annotations

import hashlib
import sqlite3

from code_atlas.search.vector.contracts import (
    EmbeddingModelInfo,
    EmbeddingProvider,
)
from code_atlas.search.vector.index import pack_vector, unpack_vector


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SqliteEmbeddingCache:
    """Cache persistente por conteúdo+modelo (tabela embedding_cache)."""

    _GET = (
        "SELECT vector FROM embedding_cache "
        "WHERE model_id = ? AND model_version = ? AND content_hash = ?"
    )
    _PUT = (
        "INSERT OR REPLACE INTO embedding_cache "
        "(model_id, model_version, content_hash, dim, vector) "
        "VALUES (?, ?, ?, ?, ?)"
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(
        self, model: EmbeddingModelInfo, chash: str
    ) -> list[float] | None:
        row = self._conn.execute(
            self._GET, (model.model_id, model.version, chash),
        ).fetchone()
        return unpack_vector(row["vector"]) if row else None

    def put(
        self, model: EmbeddingModelInfo, chash: str, vector: list[float]
    ) -> None:
        self._conn.execute(
            self._PUT,
            (model.model_id, model.version, chash, len(vector),
             pack_vector(vector)),
        )

    def commit(self) -> None:
        """Persiste as writes pendentes do cache (lote, não por linha)."""
        self._conn.commit()


def embed_texts_cached(
    provider: EmbeddingProvider,
    cache: SqliteEmbeddingCache,
    texts: list[str],
) -> tuple[list[list[float]], int, int]:
    """Embed com cache: retorna (vetores, hits, misses) — misses sobem
    em lote único ao provider."""
    model = provider.info()
    out: list[list[float] | None] = [None] * len(texts)
    miss_idx: list[int] = []
    hits = 0
    for i, text in enumerate(texts):
        vec = cache.get(model, content_hash(text))
        if vec is None:
            miss_idx.append(i)
        else:
            out[i] = vec
            hits += 1
    if miss_idx:
        fresh = provider.embed([texts[i] for i in miss_idx])
        for i, vec in zip(miss_idx, fresh, strict=True):
            cache.put(model, content_hash(texts[i]), vec)
            out[i] = vec
        # Cache de embeddings é append-only e semanticamente
        # independente do estado transacional do snapshot — commita o
        # lote para que CLI/processos one-shot não percam o cache no
        # rollback do close (lf-0142 finding).
        cache.commit()
    return [v if v is not None else [] for v in out], hits, len(miss_idx)


def index_text_for(symbol_key: str, signature: str, docstring: str) -> str:
    """Texto de indexação: fronteiras sintáticas (nome+signature+doc),
    nunca chunk fixo."""
    return "\n".join(p for p in (symbol_key, signature, docstring) if p)
