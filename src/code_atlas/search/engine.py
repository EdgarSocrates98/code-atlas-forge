"""Fachada de busca — resolve snapshot READY e despacha estratégias
(lf-0036). ``structural`` delega ao ``StructuralSearch`` existente;
``graph``/``hybrid``/``vector`` ainda não implementados → diagnóstico
tipado honesto (nunca resultado vazio silencioso)."""

from __future__ import annotations

import sqlite3

from code_atlas.search.contracts import (
    RANKING_VERSION,
    SEARCH_SCHEMA_VERSION,
    SearchCapability,
    SearchDiagnostic,
    SearchHit,
    SearchRequest,
    SearchResult,
    decode_cursor,
    encode_cursor,
    query_fingerprint,
)
from code_atlas.search.lexical import LexicalSearch
from code_atlas.search.structural import StructuralSearch


class SearchEngine:
    """Ponto único de entrada de busca sobre a conexão."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._structural = StructuralSearch(conn)
        self._lexical = LexicalSearch(conn)

    def capabilities(self) -> SearchCapability:
        fts5 = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts_occurrences'"
        ).fetchone() is not None
        return SearchCapability(
            strategies=("lexical", "structural") if fts5 else ("structural",),
            fts5=fts5,
            vector=False,
            detail="graph/hybrid/vector: planned (lf-0037..lf-0039)",
        )

    def search(self, req: SearchRequest) -> SearchResult:
        diags: list[SearchDiagnostic] = []
        repo_id = self._structural.repo_for_root(req.repository_root)
        if repo_id is None:
            diags.append(SearchDiagnostic(
                code="repository-not-found", severity="error",
                message=f"repositório não indexado: {req.repository_root}"))
            return self._empty(req, diags)
        snap, snap_err = self._structural.resolve_snapshot(repo_id, None)
        if snap is None:
            diags.append(SearchDiagnostic(
                code="no-ready-snapshot", severity="error",
                message=snap_err or "nenhum snapshot READY"))
            return self._empty(req, diags)
        sid = int(snap.id or 0)

        filters = {"language": req.language, "kind": req.kind,
                   "path": req.path}
        fp = query_fingerprint(req.query, filters)
        offset = 0
        if req.cursor:
            decoded = decode_cursor(req.cursor, fp)
            if decoded is None:
                diags.append(SearchDiagnostic(
                    code="invalid-cursor", severity="error",
                    message="cursor não corresponde a esta query/filtros"))
                return self._empty(req, diags, snapshot_id=sid)
            offset = decoded

        if req.strategy == "structural":
            hits, total = self._structural_hits(req, repo_id, sid, offset)
            ranking = "structural-sql-v1"
        elif req.strategy == "lexical":
            hits, total, lex_diags = self._lexical.search(req, sid, offset)
            diags.extend(lex_diags)
            ranking = RANKING_VERSION
        else:
            diags.append(SearchDiagnostic(
                code="strategy-unavailable", severity="error",
                message=f"estrategia '{req.strategy}' não implementada "
                        f"(capabilities: lexical, structural)"))
            return self._empty(req, diags, snapshot_id=sid)

        truncated = offset + len(hits) < total
        return SearchResult(
            schema_version=SEARCH_SCHEMA_VERSION,
            repository=req.repository_root,
            snapshot_id=sid,
            query=req.query,
            strategy=req.strategy,
            ranking_version=ranking,
            items=hits,
            limit=req.limit,
            truncated=truncated,
            next_cursor=(
                encode_cursor(offset + len(hits), fp) if truncated else None
            ),
            diagnostics=tuple(diags),
        )

    def _structural_hits(
        self, req: SearchRequest, repo_id: int, snapshot_id: int,
        offset: int,
    ) -> tuple[tuple[SearchHit, ...], int]:
        """Adapta ``search_symbols`` do StructuralSearch para SearchHit."""
        from code_atlas.search.structural import SearchFilters

        page = self._structural.search_symbols(
            repo_id, snapshot_id, req.query,
            SearchFilters(
                language=req.language, kind=req.kind, path=req.path,
            ),
            max(1, min(req.limit or 20, 200)) + offset,
            0,
        )
        items = page.items[offset:]
        hits = tuple(
            SearchHit(
                occurrence_id=int(it.get("occurrence_id", 0) or 0),
                qualified_name=str(it.get("qualified_name", "")),
                kind=str(it.get("kind", "")),
                language=str(it.get("language", "")),
                path=str(it.get("path", "")),
                span=it.get("span"),
                score=0.0,
                strategy="structural",
            )
            for it in items
        )
        return hits, int(page.total)

    def _empty(
        self,
        req: SearchRequest,
        diags: list[SearchDiagnostic],
        snapshot_id: int = 0,
    ) -> SearchResult:
        return SearchResult(
            schema_version=SEARCH_SCHEMA_VERSION,
            repository=req.repository_root,
            snapshot_id=snapshot_id,
            query=req.query,
            strategy=req.strategy,
            ranking_version=RANKING_VERSION,
            items=(),
            limit=req.limit,
            truncated=False,
            diagnostics=tuple(diags),
        )
