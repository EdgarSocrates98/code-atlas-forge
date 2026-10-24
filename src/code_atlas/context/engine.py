"""Context pack engine — seleção determinística sob budget (lf-0040).

Pipeline: resolver snapshot → candidatos (símbolos diretos via
structural+lexical, vizinhos via graph, arquivos, dependências,
evidências) → dedup por identidade → prioridade → encaixe no budget
com reservas → indicadores explícitos de truncamento.

Nenhum item é gerado por LLM: todo corpo vem de fatos persistidos
(qualified_name, signature, docstring, path, arestas) ou da query.
"""

from __future__ import annotations

import json
import sqlite3

from code_atlas.context.contracts import (
    PACK_SCHEMA_VERSION,
    PRIORITY_COMPLEMENT,
    PRIORITY_CONTRACT,
    PRIORITY_DEP,
    PRIORITY_DIRECT,
    Budget,
    ContextItem,
    ContextPack,
    ContextRequest,
    ExpansionRef,
    PackDiagnostics,
    PackEvidence,
    PackManifest,
)
from code_atlas.context.tokenizer import HeuristicTokenizer, TokenCounter
from code_atlas.search.graph import GraphRetrieval, TraversalLimits
from code_atlas.search.structural import SearchFilters, StructuralSearch

# Reservas do envelope (fração do limite) — metadados+evidências+diags.
_RESERVE_META = 0.05
_RESERVE_EVID = 0.10
_RESERVE_DIAG = 0.05


class ContextEngine:
    """Constrói ``ContextPack`` determinístico sobre a conexão."""

    def __init__(
        self, conn: sqlite3.Connection, counter: TokenCounter | None = None
    ) -> None:
        self._conn = conn
        self._structural = StructuralSearch(conn)
        self._graph = GraphRetrieval(conn)
        self._counter = counter or HeuristicTokenizer()

    # -------------------------------------------------------- público
    def build(self, req: ContextRequest) -> ContextPack:
        diags: list[str] = []
        repo_id = self._structural.repo_for_root(req.repository_root)
        if repo_id is None:
            return self._empty(req, f"repositório não indexado: "
                                  f"{req.repository_root}")
        snap, err = self._structural.resolve_snapshot(
            repo_id, req.snapshot_id)
        if snap is None or snap.id is None:
            return self._empty(req, err or "nenhum snapshot READY")
        sid = int(snap.id or 0)

        budget = self._budget(req)
        candidates = self._candidates(req, sid)
        items = self._dedup(self._estimate(candidates))
        items.sort(key=lambda i: (i.priority, i.item_id))
        selected, omitted = self._fit(items, budget.available_items)
        essentials_cost = sum(
            i.est_tokens for i in items if i.priority <= PRIORITY_DIRECT)
        insufficient = len(selected) < len(
            [i for i in items if i.priority <= PRIORITY_DIRECT])
        return self._pack(
            req, sid, budget, selected, omitted, essentials_cost,
            insufficient, diags, str(snap.status))

    # -------------------------------------------------------- internals
    def _budget(self, req: ContextRequest) -> Budget:
        limit = max(64, req.budget_tokens)
        meta = int(limit * _RESERVE_META)
        evid = int(limit * _RESERVE_EVID)
        diag = int(limit * _RESERVE_DIAG)
        return Budget(
            limit=limit, reserved_meta=meta, reserved_evidence=evid,
            reserved_diagnostics=diag,
            available_items=max(32, limit - meta - evid - diag),
            estimator=self._counter.counter_id)

    def _candidates(self, req: ContextRequest, sid: int) -> list[ContextItem]:
        items: list[ContextItem] = []
        repo_id = self._structural.repo_for_root(req.repository_root)
        filters = SearchFilters()
        # símbolos diretos — structural sobre a task (substring qname)
        page = self._structural.search_symbols(
            repo_id or 0, sid, req.task, filters,
            req.max_symbols, 0)
        occ = self._occ_rows(sid, [int(s["symbol_id"]) for s in page.items])
        for sym in page.items:
            qn = str(sym["qualified_name"])
            row = occ.get(int(sym["symbol_id"]), {})
            sig = str(row.get("signature") or "")
            doc = str(row.get("docstring") or "")
            body = "\n".join(p for p in (qn, sig, doc) if p)
            has_contract = bool(sig or doc)
            items.append(ContextItem(
                item_id=f"sym:{qn}", kind="symbol",
                priority=(PRIORITY_CONTRACT if has_contract
                          else PRIORITY_DIRECT),
                title=qn, body=body, est_tokens=0,
                symbol_key=qn, path=str(row.get("relative_path", "")),
                evidence=(PackEvidence(
                    kind="occurrence", ref=str(row.get("id", ""))),),
                expandable=True))
        # vizinhos via grafo (focus: 1 hop; deep: transitivo limitado)
        depth = {"orientation": 0, "focus": 1, "deep": 3}[req.level]
        seed_ids = [int(str(r["id"])) for r in occ.values()
                    if r.get("id")]
        if depth:
            items.extend(self._neighbor_items(sid, seed_ids, depth))
        # arquivos relevantes — dedup por path
        for p in sorted({i.path for i in items if i.path}):
            items.append(ContextItem(
                item_id=f"file:{p}", kind="file",
                priority=PRIORITY_COMPLEMENT, title=p, body=p,
                est_tokens=0, path=p))
        # dependências comprovadas entre selecionados
        items.extend(self._dependency_items(sid, seed_ids))
        return items

    def _occ_rows(
        self, sid: int, symbol_ids: list[int]
    ) -> dict[int, dict[str, object]]:
        """Primeira ocorrência por símbolo + signature/docstring do FTS
        — batched via json_each, sem N+1."""
        if not symbol_ids:
            return {}
        rows = self._conn.execute(
            "SELECT o.id, o.logical_symbol_id, s.qualified_name,"
            " sf.relative_path, f.signature, f.docstring"
            " FROM symbol_occurrences o"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " JOIN source_files sf ON sf.id = o.source_file_id"
            " LEFT JOIN fts_occurrences f ON f.rowid = o.id"
            " WHERE o.snapshot_id = ? AND o.logical_symbol_id IN"
            " (SELECT value FROM json_each(?))"
            " ORDER BY o.id",
            (sid, json.dumps(symbol_ids)),
        ).fetchall()
        out: dict[int, dict[str, object]] = {}
        for r in rows:
            sid_key = int(r["logical_symbol_id"])
            if sid_key not in out:
                out[sid_key] = dict(r)
        return out

    def _neighbor_items(
        self, sid: int, seed_ids: list[int], depth: int
    ) -> list[ContextItem]:
        out: list[ContextItem] = []
        seed_ids = seed_ids[:20]
        if not seed_ids:
            return out
        hits, _stats = self._graph.traverse(
            sid, seed_ids, direction="both",
            limits=TraversalLimits(
                depth=depth, max_nodes=40, max_results=40))
        for h in hits:
            out.append(ContextItem(
                item_id=f"nb:{h.qualified_name}",
                kind="symbol", priority=PRIORITY_COMPLEMENT,
                title=h.qualified_name, body=h.qualified_name,
                est_tokens=0, symbol_key=h.qualified_name,
                path=h.path, expandable=True))
        return out

    def _dependency_items(
        self, sid: int, seed_ids: list[int]
    ) -> list[ContextItem]:
        if not seed_ids:
            return []
        rows = self._conn.execute(
            "SELECT sa.qualified_name AS src, sb.qualified_name AS dst,"
            " r.type AS edge_kind FROM relations r"
            " JOIN symbol_occurrences a ON a.id = r.from_id"
            " LEFT JOIN symbol_occurrences b ON b.id = r.to_id"
            " JOIN symbols sa ON sa.id = a.logical_symbol_id"
            " LEFT JOIN symbols sb ON sb.id = b.logical_symbol_id"
            " WHERE r.snapshot_id = ? AND (r.from_id IN "
            "(SELECT value FROM json_each(?)) OR r.to_id IN "
            "(SELECT value FROM json_each(?))) LIMIT 100",
            (sid, json.dumps(seed_ids), json.dumps(seed_ids)),
        ).fetchall()
        return [ContextItem(
            item_id=f"dep:{r['src']}->{r['edge_kind']}->{r['dst']}",
            kind="dependency", priority=PRIORITY_DEP,
            title=f"{r['src']} -[{r['edge_kind']}]-> {r['dst']}",
            body=f"{r['src']} {r['edge_kind']} {r['dst']}",
            est_tokens=0,
            evidence=(PackEvidence(kind="relation",
                                   ref=str(r["edge_kind"])),))
            for r in rows if r["dst"]]

    def _estimate(self, items: list[ContextItem]) -> list[ContextItem]:
        return [i if i.est_tokens else ContextItem(
            item_id=i.item_id, kind=i.kind, priority=i.priority,
            title=i.title, body=i.body,
            est_tokens=self._counter.count(i.body),
            symbol_key=i.symbol_key, path=i.path,
            evidence=i.evidence, expandable=i.expandable)
            for i in items]

    def _dedup(self, items: list[ContextItem]) -> list[ContextItem]:
        seen: set[str] = set()
        out: list[ContextItem] = []
        for i in items:
            key = f"{i.kind}:{i.item_id}"
            if key in seen:
                continue
            seen.add(key)
            out.append(i)
        return out

    def _fit(
        self, items: list[ContextItem], available: int
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        sel: list[ContextItem] = []
        omit: list[ContextItem] = []
        used = 0
        for i in items:
            if used + i.est_tokens <= available:
                sel.append(i)
                used += i.est_tokens
            else:
                omit.append(i)
        return sel, omit

    def _pack(
        self, req: ContextRequest, sid: int, budget: Budget,
        selected: list[ContextItem], omitted: list[ContextItem],
        essentials_cost: int, insufficient: bool,
        diags: list[str], snap_status: str,
    ) -> ContextPack:
        codes: list[str] = list(diags)
        if omitted:
            codes.append("pack-truncated")
        if insufficient:
            codes.extend(("insufficient-budget",
                          "mandatory-context-omitted"))
        manifest = PackManifest(
            schema_version=PACK_SCHEMA_VERSION,
            repository=req.repository_root, snapshot_id=sid,
            level=req.level, estimator=budget.estimator,
            tokens_estimated=True,
            selected=len(selected), omitted=len(omitted),
            truncated=bool(omitted),
            freshness=f"snapshot:{sid} status:{snap_status}",
            ranking_explanation=(
                "prioridade: evidências > diretos > contratos > deps > "
                "snippets > complementar > transitivos; dentro da "
                "classe, item_id estável"))
        diagnostics = PackDiagnostics(
            codes=tuple(codes), messages=tuple(diags),
            insufficient_budget=insufficient,
            mandatory_context_omitted=insufficient,
            suggested_minimum_budget=(
                essentials_cost + budget.limit - budget.available_items
                if insufficient else 0))
        return ContextPack(
            manifest=manifest, budget=budget,
            items=tuple(selected),
            selected_symbols=tuple(
                i.symbol_key for i in selected
                if i.kind == "symbol" and i.symbol_key),
            relevant_files=tuple(sorted(
                {i.path for i in selected if i.path})),
            dependencies=tuple(
                ev for i in selected if i.kind == "dependency"
                for ev in i.evidence),
            unresolved_refs=(),
            expansion_refs=tuple(
                ExpansionRef(ref_id=i.item_id, kind=i.kind,
                             reason="omitido por budget ou nível")
                for i in omitted if i.expandable),
            diagnostics=diagnostics)

    def _empty(self, req: ContextRequest, msg: str) -> ContextPack:
        budget = self._budget(req)
        manifest = PackManifest(
            schema_version=PACK_SCHEMA_VERSION,
            repository=req.repository_root, snapshot_id=0,
            level=req.level, estimator=budget.estimator,
            tokens_estimated=True, selected=0, omitted=0,
            truncated=False, freshness="none",
            ranking_explanation="vazio — resolução falhou")
        return ContextPack(
            manifest=manifest, budget=budget, items=(),
            selected_symbols=(), relevant_files=(), dependencies=(),
            unresolved_refs=(), expansion_refs=(),
            diagnostics=PackDiagnostics(
                codes=("resolve-failed",), messages=(msg,),
                insufficient_budget=False,
                mandatory_context_omitted=False,
                suggested_minimum_budget=0))
