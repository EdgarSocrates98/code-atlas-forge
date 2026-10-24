"""Contratos versionados do subsistema de busca (lf-0036 / Cycle 4 Ph1).

Toda busca responde ``SearchResult`` vinculada a repository + snapshot +
query + strategy + ranking_version + evidence + limit + cursor +
diagnostics. ``to_dict`` é o único serializer (envelope JSON v2).
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from dataclasses import dataclass

SEARCH_SCHEMA_VERSION = 1
RANKING_VERSION = "bm25-fts5-v1"

STRATEGIES = frozenset({"lexical", "structural", "graph", "hybrid", "vector"})


@dataclass(frozen=True)
class SearchRequest:
    """Pedido de busca — snapshot resolvido para o READY atual."""

    query: str
    strategy: str = "lexical"
    repository_root: str = ""
    limit: int = 20
    cursor: str | None = None
    language: str | None = None
    kind: str | None = None
    path: str | None = None  # substring em relative_path


@dataclass(frozen=True)
class SearchHit:
    """Um resultado: ocorrência + score + explicação."""

    occurrence_id: int
    qualified_name: str
    kind: str
    language: str
    path: str
    span: dict[str, int] | None
    score: float
    strategy: str
    matched_fields: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchDiagnostic:
    """Diagnóstico tipado de busca (cursor inválido, filtro, etc.)."""

    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class SearchResult:
    """Envelope versionado da resposta de busca."""

    schema_version: int
    repository: str
    snapshot_id: int
    query: str
    strategy: str
    ranking_version: str
    items: tuple[SearchHit, ...]
    limit: int
    truncated: bool
    next_cursor: str | None = None
    diagnostics: tuple[SearchDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class SearchCapability:
    """O que o backend de busca oferece nesta build."""

    strategies: tuple[str, ...]
    fts5: bool
    vector: bool = False
    detail: str = ""


def encode_cursor(offset: int, fingerprint: str) -> str:
    """Cursor opaco: offset + fingerprint da query (base64)."""
    raw = json.dumps({"o": offset, "f": fingerprint}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(
    cursor: str, fingerprint: str
) -> int | None:
    """Offset do cursor se o fingerprint bate; None = inválido."""
    try:
        data = json.loads(
            base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("f") != fingerprint or not isinstance(data.get("o"), int):
        return None
    return max(0, int(data["o"]))


def query_fingerprint(query: str, filters: dict[str, str | None]) -> str:
    """Hash estável de query+filtros — impede reusar cursor de outra busca."""
    raw = json.dumps([query, sorted(filters.items())], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
