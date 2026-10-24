"""VectorIndex persistido — sqlite-vec (opt-in) ou bruteforce (lf-0039).

Backend escolhido por capability: ``sqlite_vec`` importável → vec0
(KNN nativo); senão → bruteforce stdlib sobre BLOB float32 da mesma
tabela ``vector_embeddings``. Ambos filtram por ``snapshot_id`` — o
isolamento por snapshot é estrutural, nunca filtro pós-query.
"""

from __future__ import annotations

import math
import sqlite3
import struct

from code_atlas.search.vector.contracts import VectorSearchResult

_UPSERT = (
    "INSERT OR REPLACE INTO vector_embeddings "
    "(snapshot_id, occurrence_id, symbol_key, model_id, model_version,"
    " dim, content_hash, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_DELETE_SNAP = "DELETE FROM vector_embeddings WHERE snapshot_id = ?"


def pack_vector(vec: list[float]) -> bytes:
    """float32 little-endian — 4 bytes/dim, round-trip estável."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / math.sqrt(na * nb)))


class BruteForceIndex:
    """Scan O(n·dim) com filtro de snapshot — honesto, sem deps."""

    def __init__(
        self, conn: sqlite3.Connection, model_id: str, model_version: str
    ) -> None:
        self._conn = conn
        self._model_id = model_id
        self._model_version = model_version

    def upsert(
        self, snapshot_id: int, occurrence_id: int,
        symbol_key: str, vector: list[float],
        content_hash: str = "",
    ) -> None:
        self._conn.execute(
            _UPSERT,
            (snapshot_id, occurrence_id, symbol_key, self._model_id,
             self._model_version, len(vector), content_hash,
             pack_vector(vector)),
        )

    def search(
        self, snapshot_id: int, query: list[float], limit: int
    ) -> list[VectorSearchResult]:
        rows = self._conn.execute(
            "SELECT occurrence_id, symbol_key, vector "
            "FROM vector_embeddings "
            "WHERE snapshot_id = ? AND model_id = ? AND model_version = ?",
            (snapshot_id, self._model_id, self._model_version),
        ).fetchall()
        scored = [
            VectorSearchResult(
                symbol_key=r["symbol_key"],
                occurrence_id=int(r["occurrence_id"]),
                score=_cosine(query, unpack_vector(r["vector"])),
                model_id=self._model_id,
            )
            for r in rows
        ]
        scored.sort(key=lambda r: (-r.score, r.occurrence_id))
        return scored[: max(1, limit)]

    def delete_snapshot(self, snapshot_id: int) -> None:
        self._conn.execute(_DELETE_SNAP, (snapshot_id,))


def sqlite_vec_available() -> bool:
    """sqlite-vec instalado? (extra opt-in ``[vector]``)"""
    try:
        import sqlite_vec  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


class SqliteVecIndex(BruteForceIndex):
    """Backend nativo via sqlite-vec quando o extra está instalado.

    Mesma tabela de persistência (interoperável com bruteforce); só a
    busca usa a virtual table vec0 — fallback ao scan se a extensão
    falhar em runtime.
    """

    def __init__(
        self, conn: sqlite3.Connection, model_id: str,
        model_version: str, dim: int
    ) -> None:
        super().__init__(conn, model_id, model_version)
        self._dim = dim
        import sqlite_vec

        sqlite_vec.load(conn)
        # SQL nomeado — auditoria AST exige literal/nome, nunca expr.
        create_vec_sql = (
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_symbols USING vec0("
            "snapshot_id INTEGER, occurrence_id INTEGER, "
            f"embedding FLOAT[{dim}])"
        )
        conn.execute(create_vec_sql)

    def upsert(
        self, snapshot_id: int, occurrence_id: int,
        symbol_key: str, vector: list[float],
        content_hash: str = "",
    ) -> None:
        super().upsert(
            snapshot_id, occurrence_id, symbol_key, vector, content_hash)
        self._conn.execute(
            "DELETE FROM vec_symbols WHERE occurrence_id = ?",
            (occurrence_id,))
        self._conn.execute(
            "INSERT INTO vec_symbols "
            "(snapshot_id, occurrence_id, embedding) VALUES (?, ?, ?)",
            (snapshot_id, occurrence_id, pack_vector(vector)),
        )

    def search(
        self, snapshot_id: int, query: list[float], limit: int
    ) -> list[VectorSearchResult]:
        try:
            rows = self._conn.execute(
                "SELECT occurrence_id, distance FROM vec_symbols "
                "WHERE embedding MATCH ? AND k = ? "
                "AND snapshot_id = ? ORDER BY distance",
                (pack_vector(query), max(1, limit), snapshot_id),
            ).fetchall()
        except sqlite3.Error:
            return super().search(snapshot_id, query, limit)
        return [
            VectorSearchResult(
                symbol_key="",  # preenchido na fusão via occurrence
                occurrence_id=int(r["occurrence_id"]),
                score=max(0.0, 1.0 - float(r["distance"])),
                model_id=self._model_id,
            )
            for r in rows
        ]

    def delete_snapshot(self, snapshot_id: int) -> None:
        super().delete_snapshot(snapshot_id)
        self._conn.execute(
            "DELETE FROM vec_symbols WHERE snapshot_id = ?",
            (snapshot_id,))
