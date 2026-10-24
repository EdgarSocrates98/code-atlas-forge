"""StructuralSearch: queries reais sobre o grafo persistido (Phase 6).

Somente SQL parametrizado. Todas as queries têm ``snapshot_id`` como
âncora — nunca vaza dado entre snapshots nem entre repositórios.

Paginação: ``LIMIT ? OFFSET ?`` com binds; ``total`` vem de um COUNT
paralelo, e ``truncated`` = ``offset + len(results) < total``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.contracts.models import Snapshot
from code_atlas.storage.store import Store

SCHEMA_VERSION = "2"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_DEPTH = 8  # lf-0022: teto duro de --depth (traversal limitado)


@dataclass(frozen=True)
class SearchFilters:
    """Filtros opcionais — None significa sem restrição."""

    language: str | None = None
    kind: str | None = None
    path: str | None = None  # substring em relative_path
    relation_type: str | None = None


@dataclass(frozen=True)
class Page:
    """Resultado paginado com flag de truncamento honesta."""

    total: int
    truncated: bool
    items: tuple[dict[str, Any], ...]


class StructuralSearch:
    """Implementação real sobre sqlite (Storage-backed SearchPort)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._store = Store(conn)

    # -- snapshot resolution -------------------------------------------

    def repo_for_root(self, root_handle: str) -> int | None:
        return self._store.repository_id_for(root_handle)

    def repository_info(self, repo_id: int) -> dict[str, Any] | None:
        """{id, root_handle, identity_key} para o envelope (lf-0022)."""
        row = self._conn.execute(
            "SELECT id, root_handle, identity_key FROM repositories"
            " WHERE id = ?",
            (repo_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "root_handle": row["root_handle"],
            "identity_key": row["identity_key"],
        }

    def resolve_snapshot(
        self, repo_id: int, snapshot_id: int | None
    ) -> tuple[Snapshot | None, str]:
        """READY mais recente por padrão; --snapshot exige id READY do repo."""
        if snapshot_id is None:
            snap = self._store.latest_ready_snapshot(repo_id)
            return (snap, "" if snap else "no READY snapshot for repository")
        snap = self._store.snapshot_by_id(snapshot_id)
        if snap is None or snap.repository_id != repo_id:
            return None, f"snapshot {snapshot_id} not found for this repository"
        if snap.status is not SnapshotStatus.READY:
            return None, f"snapshot {snapshot_id} is {snap.status}, not READY"
        return snap, ""

    # -- queries ---------------------------------------------------------

    def search_symbols(
        self,
        repo_id: int,
        snapshot_id: int,
        query: str,
        filters: SearchFilters,
        limit: int,
        offset: int,
    ) -> Page:
        """Substring case-insensitive sobre qualified_name/name."""
        conds = ["o.snapshot_id = ?", "s.repository_id = ?"]
        params: list[object] = [snapshot_id, repo_id]
        if query:
            conds.append(
                "(s.qualified_name LIKE ? ESCAPE '\\' "
                "OR s.qualified_name LIKE ? ESCAPE '\\')"
            )
            like = f"%{_escape_like(query)}%"
            params += [like, like]
        if filters.language:
            conds.append("s.language = ?")
            params.append(filters.language)
        if filters.kind:
            conds.append("s.kind = ?")
            params.append(filters.kind)
        if filters.path:
            conds.append("f.relative_path LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(filters.path)}%")
        # `where` contém SOMENTE fragmentos constantes do código (nomes de
        # coluna + ? binds) — input do usuário flui exclusivamente por
        # `params`. A auditoria estática exige a query em variável local.
        where = " AND ".join(conds)
        join = (
            "FROM symbols s "
            "JOIN symbol_occurrences o ON o.logical_symbol_id = s.id "
            "JOIN source_files f ON f.id = o.source_file_id "
            f"WHERE {where}"
        )
        count_sql = "SELECT COUNT(DISTINCT s.id) AS c " + join
        total_row = self._conn.execute(count_sql, params).fetchone()
        # lf-0051: occurrence representativa + path por subquery escalar
        # (MIN id no snapshot) — grounding hit→ocorrência sem duplicar
        # linhas por arquivo. Params das subqueries precedem os do WHERE.
        list_sql = (
            "SELECT DISTINCT s.id, s.language, s.qualified_name, s.kind,"
            " (SELECT MIN(o2.id) FROM symbol_occurrences o2"
            "  WHERE o2.logical_symbol_id = s.id"
            "  AND o2.snapshot_id = ?) AS occurrence_id,"
            " (SELECT f2.relative_path FROM symbol_occurrences o3"
            "  JOIN source_files f2 ON f2.id = o3.source_file_id"
            "  WHERE o3.logical_symbol_id = s.id"
            "  AND o3.snapshot_id = ? ORDER BY o3.id LIMIT 1) AS path "
            + join
            + " ORDER BY s.qualified_name, s.id LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(
            list_sql,
            [snapshot_id, snapshot_id, *params, limit, offset],
        ).fetchall()
        items = tuple(
            {
                "symbol_id": int(r["id"]),
                "qualified_name": r["qualified_name"],
                "language": r["language"],
                "kind": r["kind"],
                "occurrence_id": (
                    int(r["occurrence_id"])
                    if r["occurrence_id"] is not None else 0),
                "path": r["path"] or "",
            }
            for r in rows
        )
        total = int(total_row["c"])
        return Page(total, offset + len(items) < total, items)

    def symbol_detail(
        self, repo_id: int, snapshot_id: int, ident: str
    ) -> dict[str, Any] | None:
        """Detalhe de símbolo por id numérico ou qualified_name exato."""
        if ident.isdigit():
            sym = self._conn.execute(
                "SELECT * FROM symbols WHERE id = ? AND repository_id = ?",
                (int(ident), repo_id),
            ).fetchone()
        else:
            sym = self._conn.execute(
                "SELECT * FROM symbols WHERE qualified_name = ? "
                "AND repository_id = ? ORDER BY id LIMIT 1",
                (ident, repo_id),
            ).fetchone()
        if sym is None:
            return None
        occs = self._conn.execute(
            """
            SELECT o.id, o.line, o.col, o.parse_status, o.content_hash,
                   f.relative_path
            FROM symbol_occurrences o
            JOIN source_files f ON f.id = o.source_file_id
            WHERE o.snapshot_id = ? AND o.logical_symbol_id = ?
            ORDER BY f.relative_path, o.line
            """,
            (snapshot_id, int(sym["id"])),
        ).fetchall()
        return {
            "symbol_id": int(sym["id"]),
            "qualified_name": sym["qualified_name"],
            "language": sym["language"],
            "kind": sym["kind"],
            "occurrences": [
                {
                    "occurrence_id": int(o["id"]),
                    "path": o["relative_path"],
                    "line": o["line"],
                    "col": o["col"],
                    "parse_status": o["parse_status"],
                }
                for o in occs
            ],
        }

    def references(
        self,
        repo_id: int,
        snapshot_id: int,
        ident: str,
        relation_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Page | None, str]:
        """Arestas **entrantes** do símbolo (lf-0022: ``o2`` é o símbolo).

        Default: tipos REFERENCES+CALLS; ``relation_type`` filtra outro
        tipo explicitamente. ``ambiguous`` permanece ``ambiguous`` —
        nunca promovido a ``resolved``.
        """
        detail = self.symbol_detail(repo_id, snapshot_id, ident)
        if detail is None:
            return None, f"symbol {ident!r} not found in repository"
        sym_id = int(detail["symbol_id"])

        conds = ["r.snapshot_id = ?", "o2.logical_symbol_id = ?"]
        params: list[object] = [snapshot_id, sym_id]
        if relation_type:
            conds.append("r.type = ?")
            params.append(relation_type)
        else:
            conds.append("r.type IN ('REFERENCES','CALLS')")
        where = " AND ".join(conds)

        # ver nota em search_symbols: fragments constantes + binds
        base = (
            "FROM relations r "
            "JOIN symbol_occurrences o1 ON o1.id = r.from_id "
            "JOIN symbols sf ON sf.id = o1.logical_symbol_id "
            "JOIN source_files ff ON ff.id = o1.source_file_id "
            "LEFT JOIN symbol_occurrences o2 ON o2.id = r.to_id "
            "LEFT JOIN symbols st ON st.id = o2.logical_symbol_id "
            "LEFT JOIN source_files ft ON ft.id = o2.source_file_id "
            f"WHERE {where}"
        )
        count_sql = "SELECT COUNT(*) AS c " + base
        total_row = self._conn.execute(count_sql, params).fetchone()
        list_sql = (
            "SELECT r.id, r.type, r.resolution_status, r.confidence_class, "
            "r.resolver, r.to_unresolved, r.evidence_ids, "
            "sf.qualified_name AS from_qname, st.qualified_name AS to_qname, "
            "ff.relative_path AS from_path, ft.relative_path AS to_path, "
            "o1.line AS from_line, o2.line AS to_line "
            + base
            + " ORDER BY r.id LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(list_sql, [*params, limit, offset]).fetchall()
        items = tuple(
            {
                "relation_id": int(r["id"]),
                "type": r["type"],
                "from": {
                    "symbol": r["from_qname"],
                    "path": r["from_path"],
                    "line": r["from_line"],
                },
                "to": (
                    {
                        "symbol": r["to_qname"],
                        "path": r["to_path"],
                        "line": r["to_line"],
                    }
                    if r["to_qname"] is not None
                    else None
                ),
                "to_unresolved": r["to_unresolved"],
                "resolution_status": r["resolution_status"],
                "confidence_class": r["confidence_class"],
                "resolver": r["resolver"],
                "evidence_ids": json.loads(r["evidence_ids"]),
            }
            for r in rows
        )
        total = int(total_row["c"])
        return Page(total, offset + len(items) < total, items), ""

    def graph_dump(
        self, snapshot_id: int, filters: SearchFilters, limit: int
    ) -> dict[str, Any]:
        """Nós + arestas do snapshot — JSON exportável, sem dependência de UI."""
        nconds = ["o.snapshot_id = ?"]
        nparams: list[object] = [snapshot_id]
        if filters.language:
            nconds.append("s.language = ?")
            nparams.append(filters.language)
        if filters.kind:
            nconds.append("s.kind = ?")
            nparams.append(filters.kind)
        if filters.path:
            nconds.append("f.relative_path LIKE ? ESCAPE '\\'")
            nparams.append(f"%{_escape_like(filters.path)}%")
        nodes_sql = (
            "SELECT o.id AS occurrence_id, s.id AS symbol_id, "
            "s.qualified_name, s.kind, s.language, "
            "f.relative_path, o.line, o.col, o.parse_status "
            "FROM symbol_occurrences o "
            "JOIN symbols s ON s.id = o.logical_symbol_id "
            "JOIN source_files f ON f.id = o.source_file_id "
            "WHERE " + " AND ".join(nconds) + " ORDER BY o.id LIMIT ?"
        )
        nodes = self._conn.execute(nodes_sql, [*nparams, limit]).fetchall()

        econds = ["r.snapshot_id = ?"]
        eparams: list[object] = [snapshot_id]
        if filters.relation_type:
            econds.append("r.type = ?")
            eparams.append(filters.relation_type)
        edges_sql = (
            "SELECT r.id, r.from_id, r.to_id, r.to_unresolved, r.type, "
            "r.resolution_status, r.confidence_class, r.resolver "
            "FROM relations r WHERE " + " AND ".join(econds)
            + " ORDER BY r.id LIMIT ?"
        )
        edges = self._conn.execute(edges_sql, [*eparams, limit]).fetchall()

        return {
            "nodes": [
                {
                    "occurrence_id": int(n["occurrence_id"]),
                    "symbol_id": int(n["symbol_id"]),
                    "qualified_name": n["qualified_name"],
                    "kind": n["kind"],
                    "language": n["language"],
                    "path": n["relative_path"],
                    "line": n["line"],
                    "parse_status": n["parse_status"],
                }
                for n in nodes
            ],
            "edges": [
                {
                    "relation_id": int(e["id"]),
                    "from_occurrence": e["from_id"],
                    "to_occurrence": e["to_id"],
                    "to_unresolved": e["to_unresolved"],
                    "type": e["type"],
                    "resolution_status": e["resolution_status"],
                    "confidence_class": e["confidence_class"],
                    "resolver": e["resolver"],
                }
                for e in edges
            ],
            "counts": {"nodes": len(nodes), "edges": len(edges)},
        }

    def definitions(
        self, repo_id: int, snapshot_id: int, ident: str
    ) -> dict[str, Any] | None:
        """Sites de definição do símbolo (path + span) — lf-0022.

        Ocorrências do símbolo lógico são seus pontos de definição;
        inclui ``parse_status`` para honestidade do site.
        """
        detail = self.symbol_detail(repo_id, snapshot_id, ident)
        if detail is None:
            return None
        return {
            "symbol_id": detail["symbol_id"],
            "qualified_name": detail["qualified_name"],
            "kind": detail["kind"],
            "language": detail["language"],
            "definitions": [
                {
                    "path": o["path"],
                    "line": o["line"],
                    "col": o["col"],
                    "parse_status": o["parse_status"],
                }
                for o in detail["occurrences"]
            ],
        }

    def dependencies(
        self,
        repo_id: int,
        snapshot_id: int,
        ident: str,
        depth: int,
        limit: int,
        offset: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """Arestas **saíntes** do símbolo com traversal BFS — lf-0022.

        ``depth`` já chega clampado (1..MAX_DEPTH). Ciclo-safe:
        ``visited`` de symbol_ids impede re-expansão. ``truncated`` é
        verdadeiro quando o budget de nós (``limit`` por página acumula
        ``MAX_LIMIT``) ou o ``offset`` corta o resultado. Arestas
        ``ambiguous``/``unresolved`` aparecem com seu status real —
        nunca promovidas a ``resolved``.
        """
        detail = self.symbol_detail(repo_id, snapshot_id, ident)
        if detail is None:
            return None, f"symbol {ident!r} not found in repository"
        start_id = int(detail["symbol_id"])

        node_budget = MAX_LIMIT
        visited = {start_id}
        frontier = {start_id}
        edges: list[dict[str, Any]] = []
        truncated = False
        edge_sql = (
            "SELECT r.id, r.type, r.resolution_status, r.confidence_class,"
            " r.resolver, r.to_unresolved, r.evidence_ids,"
            " sf.qualified_name AS from_qname, st.id AS to_symbol_id,"
            " st.qualified_name AS to_qname,"
            " ff.relative_path AS from_path, ft.relative_path AS to_path,"
            " o1.line AS from_line, o2.line AS to_line"
            "  FROM relations r"
            "  JOIN symbol_occurrences o1 ON o1.id = r.from_id"
            "  JOIN symbols sf ON sf.id = o1.logical_symbol_id"
            "  JOIN source_files ff ON ff.id = o1.source_file_id"
            "  LEFT JOIN symbol_occurrences o2 ON o2.id = r.to_id"
            "  LEFT JOIN symbols st ON st.id = o2.logical_symbol_id"
            "  LEFT JOIN source_files ft ON ft.id = o2.source_file_id"
            " WHERE r.snapshot_id = ? AND o1.logical_symbol_id = ?"
            "   AND r.type IN ('IMPORTS','CALLS','REFERENCES','EXTENDS',"
            "                  'IMPLEMENTS')"
            " ORDER BY r.id"
        )
        for level in range(1, depth + 1):
            if not frontier or truncated:
                break
            next_frontier: set[int] = set()
            for sym_id in sorted(frontier):
                rows = self._conn.execute(
                    edge_sql, (snapshot_id, sym_id)
                ).fetchall()
                for r in rows:
                    if len(edges) >= node_budget:
                        truncated = True
                        break
                    to_sid = r["to_symbol_id"]
                    if to_sid is not None and int(to_sid) not in visited:
                        visited.add(int(to_sid))
                        next_frontier.add(int(to_sid))
                    edges.append(
                        {
                            "relation_id": int(r["id"]),
                            "depth": level,
                            "type": r["type"],
                            "from": {
                                "symbol": r["from_qname"],
                                "path": r["from_path"],
                                "line": r["from_line"],
                            },
                            "to": (
                                {
                                    "symbol": r["to_qname"],
                                    "symbol_id": int(to_sid),
                                    "path": r["to_path"],
                                    "line": r["to_line"],
                                }
                                if r["to_qname"] is not None
                                else None
                            ),
                            "to_unresolved": r["to_unresolved"],
                            "resolution_status": r["resolution_status"],
                            "confidence_class": r["confidence_class"],
                            "resolver": r["resolver"],
                            "evidence_ids": json.loads(r["evidence_ids"]),
                        }
                    )
                if truncated:
                    break
            frontier = next_frontier
        total = len(edges)
        page_items = edges[offset : offset + limit]
        truncated = truncated or (offset + len(page_items) < total)
        return (
            {
                "symbol_id": start_id,
                "qualified_name": detail["qualified_name"],
                "depth": depth,
                "total": total,
                "truncated": truncated,
                "results": page_items,
                "visited_symbols": len(visited),
            },
            "",
        )


def _escape_like(term: str) -> str:
    """Escapa curingas LIKE — input do usuário nunca vira padrão."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clamp_limit(limit: int | None) -> int:
    """Sane default + teto duro — saída nunca é ilimitada."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(0, min(int(limit), MAX_LIMIT))
