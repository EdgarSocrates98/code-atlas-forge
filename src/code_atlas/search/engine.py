"""Fachada de busca — resolve snapshot READY e despacha estratégias
(lf-0036). ``structural`` delega ao ``StructuralSearch`` existente;
``graph``/``hybrid``/``vector`` ainda não implementados → diagnóstico
tipado honesto (nunca resultado vazio silencioso)."""

from __future__ import annotations

import dataclasses
import json
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
from code_atlas.search.vector.contracts import (
    EmbeddingProvider,
)
from code_atlas.search.vector.embeddings import SqliteEmbeddingCache
from code_atlas.search.vector.index import (
    BruteForceIndex,
    sqlite_vec_available,
)


class SearchEngine:
    """Ponto único de entrada de busca sobre a conexão."""

    def __init__(
        self, conn: sqlite3.Connection, *, enable_vector: bool = True,
        enable_rerank: bool = True,
    ) -> None:
        self._conn = conn
        self._structural = StructuralSearch(conn)
        self._lexical = LexicalSearch(conn)
        self._graph = GraphRetrieval(conn)
        self._vec = enable_vector
        self._rerank = enable_rerank

    def capabilities(self) -> SearchCapability:
        fts5 = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts_occurrences'"
        ).fetchone() is not None
        base: tuple[str, ...] = ("lexical", "structural", "graph", "hybrid")
        if self._vec:
            base += ("vector",)
        backend = (
            "sqlite-vec" if sqlite_vec_available() else "bruteforce")
        return SearchCapability(
            strategies=base if fts5 else tuple(
                s for s in base if s != "lexical"),
            fts5=fts5,
            vector=self._vec,
            detail=(
                f"vector: provider=naive-hash backend={backend}"
                if self._vec else "vector: disabled"),
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

        # lf-0058: snapshot no fingerprint — cursor de outra revisão é
        # rejeitado (proteção cross-revision exigida pela Phase 0.2).
        filters = {"language": req.language, "kind": req.kind,
                   "path": req.path, "snapshot": str(sid)}
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
            fused, h_diags = self._hybrid_hits(req, sid)
            diags.extend(h_diags)
            # lf-0058 Phase 0.2: limit/offset APLICADOS após fusão —
            # antes retornava todos os candidatos (P1).
            lim = max(1, min(req.limit or 20, 200))
            page = fused[offset:offset + lim]
            truncated = offset + len(page) < len(fused)
            return SearchResult(
                schema_version=SEARCH_SCHEMA_VERSION,
                repository=req.repository_root,
                snapshot_id=sid, query=req.query, strategy=req.strategy,
                ranking_version=RANKING_VERSION_HYBRID, items=page,
                limit=lim,
                truncated=truncated,
                next_cursor=(encode_cursor(offset + len(page), fp)
                             if truncated else None),
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
        elif req.strategy == "vector":
            hits, total, v_diags = self._vector_hits(req, sid, offset)
            diags.extend(v_diags)
            ranking = "vector-cosine-v1"
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
                matched_fields=("qualified_name",),
                evidence=(
                    (f"occ:{int(it.get('occurrence_id', 0) or 0)}",)
                    if int(it.get("occurrence_id", 0) or 0) else ()),
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
        # lf-0058: cursor NÃO pode vazar para os sub-canais — a lista
        # fundida deve ser idêntica entre páginas (paginação estável).
        lex_req = dataclasses.replace(req, limit=fetch, cursor=None)
        lex_hits, _total, lex_diags = self._lexical.search(lex_req, snapshot_id)
        diags.extend(lex_diags)
        rankings["lexical"] = list(lex_hits)
        repo_id = self._structural.repo_for_root(req.repository_root)
        st_hits, _ = self._structural_hits(
            lex_req, repo_id or 0, snapshot_id, 0)
        rankings["structural"] = list(st_hits)
        g_hits, _, g_diags = self._graph_hits(lex_req, snapshot_id)
        diags.extend(d for d in g_diags if d.code != "seed-not-found")
        rankings["graph"] = list(g_hits)
        if self._vec:
            v_hits, _vt, v_diags = self._vector_hits(
                lex_req, snapshot_id, 0)
            diags.extend(
                d for d in v_diags if d.code != "vector-empty")
            rankings["vector"] = list(v_hits)
        from code_atlas.search.intent import classify

        fused = fuse(
            rankings,
            intent=classify(req.query) if self._rerank else None,
            query=req.query if self._rerank else "",
        )
        return tuple(to_search_hit(c) for c in fused), tuple(diags)

    def _vector_hits(
        self, req: SearchRequest, snapshot_id: int, offset: int
    ) -> tuple[tuple[SearchHit, ...], int, tuple[SearchDiagnostic, ...]]:
        """Busca vetorial: ensure embeddings → embed query → KNN → hits.

        Backend: sqlite-vec se o extra estiver instalado, senão scan
        bruteforce stdlib — capability declarada, nunca silenciosa.
        """
        from code_atlas.search.vector import (
            BruteForceIndex,
            NaiveEmbeddingProvider,
            SqliteEmbeddingCache,
            embed_texts_cached,
            sqlite_vec_available,
        )

        provider = NaiveEmbeddingProvider()
        model = provider.info()
        if sqlite_vec_available():
            from code_atlas.search.vector.index import SqliteVecIndex

            index: BruteForceIndex = SqliteVecIndex(
                self._conn, model.model_id, model.version, model.dim)
        else:
            index = BruteForceIndex(self._conn, model.model_id, model.version)
        cache = SqliteEmbeddingCache(self._conn)
        built = self._ensure_embeddings(
            snapshot_id, provider, cache, index)
        qvec, _qh, _qm = embed_texts_cached(provider, cache, [req.query])
        results = index.search(
            snapshot_id, qvec[0], offset + max(1, req.limit or 20))
        results = results[offset:]
        occ_ids = [r.occurrence_id for r in results]
        info: dict[int, dict[str, object]] = {}
        if occ_ids:
            rows = self._conn.execute(
                "SELECT o.id, s.qualified_name, s.kind, s.language,"
                " sf.relative_path, o.span_start, o.span_end,"
                " o.line, o.col"
                " FROM symbol_occurrences o"
                " JOIN source_files sf ON sf.id = o.source_file_id"
                " JOIN symbols s ON s.id = o.logical_symbol_id"
                " WHERE o.id IN (SELECT value FROM json_each(?))",
                (json.dumps(occ_ids),),
            ).fetchall()
            for r in rows:
                info[int(r["id"])] = dict(r)
        hits = tuple(
            SearchHit(
                occurrence_id=r.occurrence_id,
                qualified_name=str(
                    info.get(r.occurrence_id, {}).get(
                        "qualified_name", r.symbol_key)),
                kind=str(info.get(r.occurrence_id, {}).get("kind", "")),
                language=str(
                    info.get(r.occurrence_id, {}).get("language", "")),
                path=str(
                    info.get(r.occurrence_id, {}).get(
                        "relative_path", "")),
                span=None,
                score=round(r.score, 6),
                strategy="vector",
                matched_fields=("embedding",),
                evidence=(
                    f"cosine={r.score:.4f}",
                    f"model={r.model_id} dim={model.dim}",
                    f"embeddings_built={built}",
                ),
            )
            for r in results
            if r.occurrence_id in info and r.score > 0.0
        )
        diags: tuple[SearchDiagnostic, ...] = ()
        if not hits:
            diags = (SearchDiagnostic(
                code="vector-empty", severity="info",
                message="sem hits vetoriais para esta query"),)
        return hits, len(hits) + offset, diags

    def _ensure_embeddings(
        self,
        snapshot_id: int,
        provider: EmbeddingProvider,
        cache: SqliteEmbeddingCache,
        index: BruteForceIndex,
    ) -> int:
        """Materializa embeddings do snapshot — só os que faltam na
        versão atual do modelo (rebuild por mudança de conteúdo/modelo).
        Retorna quantos embeddings novos foram gerados."""
        from code_atlas.search.vector.embeddings import (
            content_hash,
            embed_texts_cached,
            index_text_for,
        )

        model = provider.info()
        rows = self._conn.execute(
            "SELECT f.rowid AS occ_id, s.qualified_name, f.signature,"
            " f.docstring FROM fts_occurrences f"
            " JOIN symbol_occurrences o ON o.id = f.rowid"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " WHERE f.snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        pending: list[tuple[int, str, str, str]] = []
        for r in rows:
            text = index_text_for(
                str(r["qualified_name"]),
                str(r["signature"] or ""), str(r["docstring"] or ""))
            pending.append(
                (int(r["occ_id"]), str(r["qualified_name"]),
                 text, content_hash(text)))
        existing = {
            (int(x["occurrence_id"]), str(x["content_hash"]))
            for x in self._conn.execute(
                "SELECT occurrence_id, content_hash "
                "FROM vector_embeddings "
                "WHERE snapshot_id = ? AND model_id = ?"
                " AND model_version = ?",
                (snapshot_id, model.model_id, model.version),
            ).fetchall()
        }
        todo = [p for p in pending if (p[0], p[3]) not in existing]
        if not todo:
            return 0
        vecs, _h, _m = embed_texts_cached(
            provider, cache, [t[2] for t in todo])
        for (occ_id, sym, _text, chash), vec in zip(
                todo, vecs, strict=True):
            index.upsert(
                snapshot_id, occ_id, sym, vec, content_hash=chash)
        return len(todo)

    def _graph_hits(
        self, req: SearchRequest, snapshot_id: int
    ) -> tuple[tuple[SearchHit, ...], int, tuple[SearchDiagnostic, ...]]:
        """Multi-seed → BFS limitado com direção por intent (lf-0049).

        Seeds: qualified_name exato + top lexical + top structural —
        dedup por occurrence_id. Seeds entram como hits rotulados
        ``graph-seed`` (casaram com a query por nome — honesto).
        Direção vem de ``classify(query)`` — nunca inventa aresta."""
        from code_atlas.search.graph import TraversalLimits
        from code_atlas.search.intent import classify

        intent = classify(req.query)
        seed_ids = set(self._graph.seed_occurrences(
            snapshot_id, req.query))
        seed_hits: list[SearchHit] = []
        for strat in ("lexical", "structural"):
            sub = self.search(dataclasses.replace(
                req, strategy=strat, limit=3))
            for h in sub.items:
                if h.occurrence_id not in seed_ids:
                    seed_ids.add(h.occurrence_id)
                    seed_hits.append(h)
        if not seed_ids:
            return (), 0, (SearchDiagnostic(
                code="seed-not-found", severity="warning",
                message=f"nenhuma ocorrência casa '{req.query}' no "
                        f"snapshot"),)
        lim = TraversalLimits(
            depth=3, max_nodes=500, max_edges=2000, max_ms=500,
            max_results=max(1, min(req.limit or 20, 200)),
        )
        hits, stats = self._graph.traverse(
            snapshot_id, list(seed_ids), direction=intent.direction,
            limits=lim)
        diags: list[SearchDiagnostic] = [
            SearchDiagnostic(
                code="graph-intent", severity="info",
                message=f"intent={intent.task_type} "
                        f"direction={intent.direction} "
                        f"seeds={len(seed_ids)}"),
        ]
        if stats.truncated_by:
            diags.append(SearchDiagnostic(
                code="traversal-truncated", severity="info",
                message=f"travessia truncada por limite: "
                        f"{stats.truncated_by}"))
        seed_out = [
            SearchHit(
                occurrence_id=h.occurrence_id,
                qualified_name=h.qualified_name, kind=h.kind,
                language=h.language, path=h.path, span=h.span,
                score=h.score, strategy="graph",
                matched_fields=("graph-seed", *h.matched_fields),
                evidence=h.evidence,
            )
            for h in seed_hits
        ]
        out = tuple(seed_out) + tuple(
            SearchHit(
                occurrence_id=h.occurrence_id,
                qualified_name=h.qualified_name, kind=h.kind,
                language=h.language, path=h.path, span=None,
                score=h.score, strategy="graph",
                matched_fields=("graph",),
                evidence=tuple(
                    f"dir={intent.direction}:"
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
