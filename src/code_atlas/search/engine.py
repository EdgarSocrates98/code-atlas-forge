"""Fachada de busca — resolve snapshot READY e despacha estratégias
(lf-0036). ``structural`` delega ao ``StructuralSearch`` existente;
``graph``/``hybrid``/``vector`` ainda não implementados → diagnóstico
tipado honesto (nunca resultado vazio silencioso)."""

from __future__ import annotations

import dataclasses
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
from code_atlas.search.graph import GraphRetrieval
from code_atlas.search.hybrid import RANKING_VERSION_HYBRID, fuse, to_search_hit
from code_atlas.search.lexical import LexicalSearch
from code_atlas.search.structural import StructuralSearch


class SearchEngine:
    """Ponto único de entrada de busca sobre a conexão."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._structural = StructuralSearch(conn)
        self._lexical = LexicalSearch(conn)
        self._graph = GraphRetrieval(conn)

    def capabilities(self) -> SearchCapability:
        fts5 = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts_occurrences'"
        ).fetchone() is not None
        return SearchCapability(
            strategies=(("lexical", "structural", "graph", "hybrid")
                        if fts5 else ("structural", "graph", "hybrid")),
            fts5=fts5,
            vector=False,
            detail="vector: planned (lf-0039)",
        )

    def search(self, req: SearchRequest) -> SearchResult:
        diags: list[SearchDiagnostic] = []
        repo_id = self._structural.repo_for_root(req.repository_root)
        if repo_id is None:
            diags.append(SearchDiagnostic(
                code="repository-not-found", severity="error",
                message=f"repositório não indexado: {req.repository_root}"))
            return self._empty(req, diags)
        snap, snap_err = self._structural.resolve_snapshot(
            repo_id, req.snapshot_id)
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

        if req.strategy == "hybrid":
            hits, h_diags = self._hybrid_hits(req, sid)
            diags.extend(h_diags)
            return SearchResult(
                schema_version=SEARCH_SCHEMA_VERSION,
                repository=req.repository_root,
                snapshot_id=sid, query=req.query, strategy=req.strategy,
                ranking_version=RANKING_VERSION_HYBRID, items=hits,
                limit=req.limit,
                truncated=False,
                diagnostics=tuple(diags),
            )
        if req.strategy == "graph":
            hits, _total, g_diags = self._graph_hits(req, sid)
            diags.extend(g_diags)
            return SearchResult(
                schema_version=SEARCH_SCHEMA_VERSION,
                repository=req.repository_root,
                snapshot_id=sid, query=req.query, strategy=req.strategy,
                ranking_version="graph-structural-v1", items=hits,
                limit=req.limit,
                truncated=len(hits) >= max(1, req.limit or 20),
                diagnostics=tuple(diags),
            )
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

    def _hybrid_hits(
        self, req: SearchRequest, snapshot_id: int
    ) -> tuple[tuple[SearchHit, ...], tuple[SearchDiagnostic, ...]]:
        """Pipeline híbrido: lexical+structural+graph → RRF → página."""
        diags: list[SearchDiagnostic] = []
        fetch = max(20, min((req.limit or 20) * 3, 200))
        rankings: dict[str, list[SearchHit]] = {}
        lex_req = dataclasses.replace(req, limit=fetch)
        lex_hits, _total, lex_diags = self._lexical.search(lex_req, snapshot_id)
        diags.extend(lex_diags)
        rankings["lexical"] = list(lex_hits)
        repo_id = self._structural.repo_for_root(req.repository_root)
        st_hits, _ = self._structural_hits(
            dataclasses.replace(req, limit=fetch),
            repo_id or 0, snapshot_id, 0)
        rankings["structural"] = list(st_hits)
        g_hits, _, g_diags = self._graph_hits(lex_req, snapshot_id)
        diags.extend(d for d in g_diags if d.code != "seed-not-found")
        rankings["graph"] = list(g_hits)
        fused = fuse(rankings)
        return tuple(to_search_hit(c) for c in fused), tuple(diags)

    def _graph_hits(
        self, req: SearchRequest, snapshot_id: int
    ) -> tuple[tuple[SearchHit, ...], int, tuple[SearchDiagnostic, ...]]:
        """Seeds por nome → travessia BFS limitada → SearchHit com
        explicação (caminho de relações) por hit."""
        from code_atlas.search.graph import TraversalLimits

        seeds = self._graph.seed_occurrences(snapshot_id, req.query)
        if not seeds:
            return (), 0, (SearchDiagnostic(
                code="seed-not-found", severity="warning",
                message=f"nenhuma ocorrência casa '{req.query}' no "
                        f"snapshot"),)
        lim = TraversalLimits(
            depth=3, max_nodes=500, max_edges=2000, max_ms=500,
            max_results=max(1, min(req.limit or 20, 200)),
        )
        hits, stats = self._graph.traverse(
            snapshot_id, seeds, direction="both", limits=lim)
        diags: list[SearchDiagnostic] = []
        if stats.truncated_by:
            diags.append(SearchDiagnostic(
                code="traversal-truncated", severity="info",
                message=f"travessia truncada por limite: "
                        f"{stats.truncated_by}"))
        out = tuple(
            SearchHit(
                occurrence_id=h.occurrence_id,
                qualified_name=h.qualified_name, kind=h.kind,
                language=h.language, path=h.path, span=None,
                score=h.score, strategy="graph",
                matched_fields=("graph",),
                evidence=tuple(
                    f"{s.relation}:{s.from_id}->{s.to_id}"
                    f"({s.resolution_status})"
                    for s in h.steps
                ),
            )
            for h in hits
        )
        return out, len(out), tuple(diags)

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
