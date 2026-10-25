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
    """Pedido de busca — snapshot resolvido para o READY atual.

    ``domain`` (lf-0119): filtro exato da faceta de entidades de
    domínio (``de.domain = ?``) — não toca hits de símbolo, que não
    têm domínio.
    """

    query: str
    strategy: str = "lexical"
    repository_root: str = ""
    snapshot_id: int | None = None
    limit: int = 20
    cursor: str | None = None
    language: str | None = None
    kind: str | None = None
    path: str | None = None  # substring em relative_path
    domain: str | None = None  # filtro da faceta domain_entities


@dataclass(frozen=True)
class SearchHit:
    """Um resultado: ocorrência + score + explicação.

    ``repository_id``/``snapshot_id`` (lf-0119): escopo que produziu o
    hit — preenchido sempre que o engine conhece o escopo (busca
    federada depende dele para nunca colapsar homônimos cross-repo).
    ``item_type`` discrimina o tipo do item ("symbol" aqui; a faceta
    de domínio usa ``DomainSearchHit`` com "domain_entity").
    """

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
    repository_id: int | None = None
    snapshot_id: int | None = None
    item_type: str = "symbol"


@dataclass(frozen=True)
class DomainSearchHit:
    """Hit da faceta de domínio — entidade lógica observada no snapshot.

    Nunca se funde com ``SearchHit`` de símbolos: sai em
    ``SearchResult.domain_items`` como faceta separada. ``occurrences``
    é a contagem de evidência (ocorrências da entidade no snapshot);
    ``occurrence_id`` aponta para a primeira ocorrência (row âncora,
    determinística). ``matched_fields`` declara COMO casou:
    ``name``/``qualified_name``/``path`` (match direto LIKE) ou
    ``via_relation`` (endpoint entity↔entity resolvido de
    ``domain_relations`` adjacente a um match direto — a aresta real é
    citada em ``evidence`` como ``via=TYPE:rel:<id>``).
    """

    entity_id: int
    occurrence_id: int
    domain: str
    kind: str
    qualified_name: str
    name: str
    identity_key: str
    catalog: str
    namespace: str
    environment: str
    path: str
    line: int | None
    occurrences: int
    extraction_status: str
    repository_id: int | None
    snapshot_id: int
    score: float
    item_type: str = "domain_entity"
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
    # lf-0119: faceta de domínio — separada de ``items`` por desenho
    # (nunca fundida com símbolos). ``domain_total`` conta matches
    # diretos + adjacentes via_relation encontrados; é top-``limit``
    # deterministicamente ordenada, sem cursor próprio.
    domain_items: tuple[DomainSearchHit, ...] = ()
    domain_total: int = 0

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
