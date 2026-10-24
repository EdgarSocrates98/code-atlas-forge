"""Expansão progressiva de contexto (lf-0041 / Cycle 4 Ph8).

Autocontida: o caller passa o ``snapshot_id`` do pack original + os
``item_ids`` já entregues — nenhum estado global. Se o snapshot não é
mais READY, erro explícito; NUNCA troca de revisão silenciosamente.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from code_atlas.context.contracts import (
    PRIORITY_DEP,
    ContextItem,
    ExpansionRef,
    PackEvidence,
)
from code_atlas.context.tokenizer import HeuristicTokenizer, TokenCounter
from code_atlas.search.graph import GraphRetrieval, TraversalLimits


@dataclass(frozen=True)
class ExpansionResult:
    """Novos itens + dedup acumulada + refs restantes."""

    snapshot_id: int
    added: tuple[ContextItem, ...]
    already_delivered: tuple[str, ...]
    remaining_refs: tuple[ExpansionRef, ...]
    truncated: bool
    error: str = ""


class ContextExpander:
    """Expande símbolo/deps/refs dentro do MESMO snapshot do pack."""

    def __init__(
        self, conn: sqlite3.Connection, counter: TokenCounter | None = None
    ) -> None:
        self._conn = conn
        self._graph = GraphRetrieval(conn)
        self._counter = counter or HeuristicTokenizer()

    def expand(
        self,
        snapshot_id: int,
        symbol: str,
        *,
        delivered: frozenset[str] = frozenset(),
        budget_tokens: int = 4000,
        depth: int = 2,
    ) -> ExpansionResult:
        """Dependências+referências do símbolo no snapshot — dedup contra
        ``delivered``; respeita a mesma disciplina de budget."""
        if not self._snapshot_ready(snapshot_id):
            return ExpansionResult(
                snapshot_id=snapshot_id, added=(), already_delivered=(),
                remaining_refs=(), truncated=False,
                error=f"snapshot {snapshot_id} indisponível (não READY)")
        occ = self._occurrence_id(snapshot_id, symbol)
        if occ is None:
            return ExpansionResult(
                snapshot_id=snapshot_id, added=(), already_delivered=(),
                remaining_refs=(), truncated=False,
                error=f"símbolo sem ocorrência no snapshot: {symbol}")
        hits, _st = self._graph.traverse(
            snapshot_id, [occ], direction="both",
            limits=TraversalLimits(depth=depth, max_nodes=50,
                                   max_results=50))
        budget = max(64, budget_tokens)
        used = added_n = 0
        added: list[ContextItem] = []
        dup: list[str] = []
        remaining: list[ExpansionRef] = []
        for h in hits:
            iid = f"nb:{h.qualified_name}"
            item = ContextItem(
                item_id=iid, kind="symbol", priority=PRIORITY_DEP,
                title=h.qualified_name,
                body="\n".join(
                    f"{s.from_id}-[{s.relation}]->{s.to_id}"
                    for s in h.steps) or h.qualified_name,
                est_tokens=0, symbol_key=h.qualified_name, path=h.path,
                evidence=tuple(PackEvidence(
                    kind="relation", ref=s.relation) for s in h.steps),
                expandable=True)
            item = ContextItem(
                **{**item.__dict__,
                   "est_tokens": self._counter.count(item.body)})
            if iid in delivered:
                dup.append(iid)
                continue
            if used + item.est_tokens <= budget:
                added.append(item)
                used += item.est_tokens
                added_n += 1
            else:
                remaining.append(ExpansionRef(
                    ref_id=iid, kind="symbol",
                    reason="fora do budget da expansão"))
        return ExpansionResult(
            snapshot_id=snapshot_id, added=tuple(added),
            already_delivered=tuple(dup),
            remaining_refs=tuple(remaining),
            truncated=bool(remaining))

    def _snapshot_ready(self, snapshot_id: int) -> bool:
        row = self._conn.execute(
            "SELECT status FROM snapshots WHERE id = ?",
            (snapshot_id,)).fetchone()
        return bool(row) and str(row["status"]).endswith("READY")

    def _occurrence_id(self, snapshot_id: int, symbol: str) -> int | None:
        row = self._conn.execute(
            "SELECT o.id FROM symbol_occurrences o"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " WHERE o.snapshot_id = ? AND (s.qualified_name = ?"
            " OR s.qualified_name LIKE ?) ORDER BY o.id LIMIT 1",
            (snapshot_id, symbol, f"%.{symbol}"),
        ).fetchone()
        return int(row["id"]) if row else None


def expansion_to_json(res: ExpansionResult) -> str:
    """Envelope parseável do expand — schema v1."""
    import dataclasses

    return json.dumps({
        "schema_version": 1, "renderer_version": "expand-json-v1",
        **{k: v for k, v in dataclasses.asdict(res).items()},
    }, sort_keys=True)
