"""Blast radius — BFS reverso de dependências (Phase 5).

Aresta resolvida ``from → to`` significa "from depende de to"; para
impacto, consumidores do símbolo alterado = arestas ``from`` cujo
``to_id`` aponta para a ocorrência alterada. Uma aresta
unresolved/ambiguous no caminho → ``fully_verified=False`` → impacto
rebaixado para ``possible`` (nunca promovido).
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass

from code_atlas.impact.contracts import (
    EvidenceStrength,
    ImpactCandidate,
    ImpactKind,
    ImpactLimits,
    ImpactPath,
)

_IN_SQL = (
    "SELECT id, from_id, to_id, type, resolution_status"
    " FROM relations WHERE snapshot_id = ? AND to_id = ?"
    " ORDER BY id LIMIT ?"
)
_SYM_SQL = (
    "SELECT s.qualified_name, s.language, s.kind, o.id AS occ_id,"
    " f.relative_path FROM symbol_occurrences o"
    " JOIN symbols s ON s.id = o.logical_symbol_id"
    " JOIN source_files f ON f.id = o.source_file_id"
    " WHERE o.id = ?"
)


@dataclass(frozen=True)
class _Hit:
    key: str
    qname: str
    language: str
    kind: str
    occ_id: int
    path: str


def _sym(conn: sqlite3.Connection, occ_id: int) -> _Hit | None:
    row = conn.execute(_SYM_SQL, (occ_id,)).fetchone()
    if row is None:
        return None
    key = f"{row['language']}:{row['kind']}:{row['qualified_name']}"
    return _Hit(key, str(row["qualified_name"]), str(row["language"]),
                str(row["kind"]), int(row["occ_id"]),
                str(row["relative_path"]))


def blast(
    conn: sqlite3.Connection, snapshot_id: int,
    origins: dict[str, int],  # symbol_key → occurrence_id alterado
    limits: ImpactLimits,
    *,
    reason_suffix: str = "",
) -> tuple[tuple[ImpactCandidate, ...], bool]:
    """BFS de consumidores; retorna (candidatos, truncated)."""
    conn.row_factory = sqlite3.Row
    out: list[ImpactCandidate] = []
    visited: set[int] = set()
    queue: deque[tuple[int, str, list[int], list[str], list[str], int]] = (
        deque())
    for key, occ in origins.items():
        queue.append((occ, key, [], [], [], 0))
    truncated = False

    while queue:
        occ, origin_key, rel_ids, mids, statuses, depth = queue.popleft()
        if occ in visited or depth > limits.max_depth:
            continue
        visited.add(occ)
        rows = conn.execute(
            _IN_SQL, (snapshot_id, occ, limits.max_nodes)).fetchall()
        for r in rows:
            consumer_occ = int(r["from_id"])
            if consumer_occ in visited:
                continue
            hit = _sym(conn, consumer_occ)
            if hit is None or hit.key in origins:
                continue
            new_rels = [*rel_ids, int(r["id"])]
            new_mid = [*mids, hit.key]
            new_st = [*statuses, str(r["resolution_status"])]
            verified = all(s == "resolved" for s in new_st)
            hops = len(new_rels)
            kind = (ImpactKind.DIRECT if hops == 1 and verified
                    else ImpactKind.TRANSITIVE if verified
                    else ImpactKind.POSSIBLE)
            strength = (EvidenceStrength.VERIFIED if verified
                        else EvidenceStrength.POSSIBLE)
            path = ImpactPath(
                origin_symbol_key=origin_key,
                affected_symbol_key=hit.key, hops=hops,
                relation_ids=tuple(new_rels),
                intermediate_keys=tuple(new_mid),
                resolution_statuses=tuple(new_st),
                fully_verified=verified,
                reason=(f"{r['type']} via relation {r['id']}"
                        + reason_suffix))
            out.append(ImpactCandidate(
                symbol_key=hit.key, qualified_name=hit.qname,
                language=hit.language, kind=hit.kind, impact=kind,
                strength=strength, path=path,
                source_path=hit.path, occurrence_id=hit.occ_id))
            if len(out) >= limits.max_paths:
                return tuple(out), True
            if hops < limits.max_depth:
                queue.append((consumer_occ, origin_key,
                              new_rels, new_mid, new_st, depth + 1))
    return tuple(out), truncated
