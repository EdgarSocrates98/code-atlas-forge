"""Retrieval estrutural sobre o grafo persistido (lf-0037 / Cycle 4 Ph3).

Travessia BFS limitada sobre ``relations`` do snapshot READY:
depth/max_nodes/max_edges/max_ms/max_results — todos honrados, ciclos
fechados por conjunto de visitados explícito. Uma query por nível de
fronteira via ``json_each`` (SQL constante — sem N+1, sem grafo em
memória).

Ranking estrutural documentado (nunca "grau alto por grau alto"):
  1. distância da semente (menor primeiro)
  2. status da evidência do caminho (resolved > ambiguous > unresolved)
  3. peso da relação usada (CALLS/IMPORTS > EXTENDS/IMPLEMENTS >
     REFERENCES/CONTAINS/…)
  4. confidence do último salto (resolved > syntactic > heuristic >
     inferred)
  5. id da ocorrência (desempate determinístico)
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

# Pesos por tipo de relação (menor = mais relevante para contexto).
_REL_WEIGHT = {
    "CALLS": 0, "IMPORTS": 1, "EXTENDS": 2, "IMPLEMENTS": 2,
    "REFERENCES": 3, "CONTAINS": 4, "DEFINES": 4, "TESTS": 4,
}
_STATUS_RANK = {"resolved": 0, "ambiguous": 1, "unresolved": 2,
                "external": 2}
_CONF_RANK = {"resolved": 0, "syntactic": 1, "heuristic": 2,
              "inferred": 3}


@dataclass(frozen=True)
class TraversalLimits:
    """Limites obrigatórios da travessia — sempre aplicados."""

    depth: int = 3
    max_nodes: int = 500
    max_edges: int = 2000
    max_ms: int = 500
    max_results: int = 100


@dataclass(frozen=True)
class TraversalStep:
    """Um salto do caminho — explicação por hit."""

    from_id: int
    to_id: int
    relation: str
    resolution_status: str
    confidence_class: str


@dataclass(frozen=True)
class TraversalHit:
    """Ocorrência alcançada + caminho + ranking estrutural."""

    occurrence_id: int
    qualified_name: str
    kind: str
    language: str
    path: str
    depth: int
    steps: tuple[TraversalStep, ...]
    score: float


@dataclass
class TraversalStats:
    nodes_visited: int = 0
    edges_examined: int = 0
    truncated_by: str = ""  # qual limite cortou ("depth|nodes|edges|time|results")


_EDGES_SQL = (
    "SELECT from_id, to_id, type, resolution_status, confidence_class"
    " FROM relations WHERE snapshot_id = ? AND ("
    " from_id IN (SELECT value FROM json_each(?))"
    " OR to_id IN (SELECT value FROM json_each(?)))"
)

_OCC_INFO_SQL = (
    "SELECT o.id, s.qualified_name, s.kind, s.language,"
    " sf.relative_path, o.span_start, o.span_end, o.line, o.col"
    " FROM symbol_occurrences o"
    " JOIN symbols s ON s.id = o.logical_symbol_id"
    " JOIN source_files sf ON sf.id = o.source_file_id"
    " WHERE o.id IN (SELECT value FROM json_each(?))"
)


class GraphRetrieval:
    """Travessia limitada e explicável sobre relations do snapshot."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def seed_occurrences(
        self, snapshot_id: int, qualified_name: str
    ) -> list[int]:
        """Seeds por qualified_name exato (case-insensitive)."""
        rows = self._conn.execute(
            "SELECT o.id FROM symbol_occurrences o"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " WHERE o.snapshot_id = ? AND ("
            " s.qualified_name = ? OR s.qualified_name LIKE ?)",
            (snapshot_id, qualified_name, f"%.{qualified_name}"),
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def traverse(
        self,
        snapshot_id: int,
        seed_ids: list[int],
        *,
        direction: str = "outgoing",  # outgoing|incoming|both
        relation_types: frozenset[str] | None = None,
        limits: TraversalLimits | None = None,
    ) -> tuple[list[TraversalHit], TraversalStats]:
        """BFS limitado — devolve hits rankeados + stats honestas."""
        lim = limits or TraversalLimits()
        stats = TraversalStats()
        t0 = time.monotonic()
        visited: set[int] = set(seed_ids)
        paths: dict[int, list[TraversalStep]] = {s: [] for s in seed_ids}
        frontier = list(seed_ids)
        hit_ids: list[int] = []
        for _depth in range(1, lim.depth + 1):
            if not frontier:
                break
            if (time.monotonic() - t0) * 1000 > lim.max_ms:
                stats.truncated_by = "time"
                break
            next_frontier = self._walk_level(
                snapshot_id, frontier, visited, paths, direction,
                relation_types, lim, stats, hit_ids,
            )
            if stats.truncated_by:
                break
            if stats.edges_examined >= lim.max_edges:
                stats.truncated_by = "edges"
                break
            frontier = next_frontier
        else:
            pass
        if not hit_ids:
            return [], stats
        info = {
            int(r["id"]): r
            for r in self._conn.execute(
                _OCC_INFO_SQL, (json.dumps(hit_ids),)
            )
        }
        hits = [
            TraversalHit(
                occurrence_id=oid,
                qualified_name=str(info[oid]["qualified_name"]),
                kind=str(info[oid]["kind"]),
                language=str(info[oid]["language"]),
                path=str(info[oid]["relative_path"]),
                depth=len(paths[oid]),
                steps=tuple(paths[oid]),
                score=_structural_score(paths[oid]),
            )
            for oid in hit_ids
            if oid in info
        ]
        if len(hit_ids) >= lim.max_results and not stats.truncated_by:
            stats.truncated_by = "results"
        hits.sort(
            key=lambda h: (
                h.depth,
                min((_STATUS_RANK.get(s.resolution_status, 3)
                     for s in h.steps), default=3),
                min((_REL_WEIGHT.get(s.relation, 5)
                     for s in h.steps), default=5),
                min((_CONF_RANK.get(s.confidence_class, 4)
                     for s in h.steps), default=4),
                h.occurrence_id,
            )
        )
        return hits, stats


    def _walk_level(
        self,
        snapshot_id: int,
        frontier: list[int],
        visited: set[int],
        paths: dict[int, list[TraversalStep]],
        direction: str,
        relation_types: frozenset[str] | None,
        lim: TraversalLimits,
        stats: TraversalStats,
        hit_ids: list[int],
    ) -> list[int]:
        """Um nível de BFS: edges da fronteira → próximos visitados."""
        rows = self._conn.execute(
            _EDGES_SQL,
            (snapshot_id, json.dumps(frontier), json.dumps(frontier)),
        ).fetchall()
        stats.edges_examined += len(rows)
        next_frontier: list[int] = []
        for r in rows:
            frm, to = int(r["from_id"]), (
                int(r["to_id"]) if r["to_id"] is not None else None)
            if to is None:
                continue
            rel = str(r["type"])
            if relation_types and rel not in relation_types:
                continue
            # direção: outgoing = seed→out, incoming = out→seed
            for a, b, direc in (
                (frm, to, "outgoing"), (to, frm, "incoming"),
            ):
                if direc != direction and direction != "both":
                    continue
                if a not in visited or b in visited:
                    continue
                visited.add(b)
                stats.nodes_visited += 1
                step = TraversalStep(
                    from_id=frm, to_id=to, relation=rel,
                    resolution_status=str(r["resolution_status"]),
                    confidence_class=str(r["confidence_class"]),
                )
                paths[b] = [*paths[a], step]
                next_frontier.append(b)
                if len(hit_ids) < lim.max_results:
                    hit_ids.append(b)
                if stats.nodes_visited >= lim.max_nodes:
                    stats.truncated_by = "nodes"
                    return next_frontier
        return next_frontier

    # -- atalhos de capacidade -------------------------------------------

    def callers(
        self, snapshot_id: int, seed: int, **kw: object
    ) -> tuple[list[TraversalHit], TraversalStats]:
        """Consumidores: arestas incoming (quem chama/importa a seed)."""
        return self.traverse(
            snapshot_id, [seed], direction="incoming", **kw)  # type: ignore[arg-type]

    def callees(
        self, snapshot_id: int, seed: int, **kw: object
    ) -> tuple[list[TraversalHit], TraversalStats]:
        return self.traverse(
            snapshot_id, [seed], direction="outgoing", **kw)  # type: ignore[arg-type]

    def dependencies(
        self, snapshot_id: int, seed: int, **kw: object
    ) -> tuple[list[TraversalHit], TraversalStats]:
        kw.setdefault(
            "relation_types",
            frozenset({"IMPORTS", "EXTENDS", "IMPLEMENTS", "REFERENCES"}),
        )
        return self.traverse(
            snapshot_id, [seed], direction="outgoing", **kw)  # type: ignore[arg-type]

    def components(
        self, snapshot_id: int, seed: int, **kw: object
    ) -> tuple[list[TraversalHit], TraversalStats]:
        """Componente conectado da seed — travessia bidirecional."""
        return self.traverse(
            snapshot_id, [seed], direction="both", **kw)  # type: ignore[arg-type]


def _structural_score(steps: list[TraversalStep]) -> float:
    """Score contínuo para ordenação estável (espelha a chave de sort)."""
    depth_penalty = len(steps)
    rel = min((_REL_WEIGHT.get(s.relation, 5) for s in steps), default=5)
    status = min(
        (_STATUS_RANK.get(s.resolution_status, 3) for s in steps),
        default=3,
    )
    return round(depth_penalty + rel * 0.1 + status * 0.05, 4)
