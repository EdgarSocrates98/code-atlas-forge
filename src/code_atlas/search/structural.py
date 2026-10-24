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

SCHEMA_VERSION = "1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


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
        list_sql = (
            "SELECT DISTINCT s.id, s.language, s.qualified_name, s.kind "
            + join
            + " ORDER BY s.qualified_name, s.id LIMIT ? OFFSET ?"
        )
        rows = self._conn.execute(list_sql, [*params, limit, offset]).fetchall()
        items = tuple(
            {
                "symbol_id": int(r["id"]),
                "qualified_name": r["qualified_name"],
                "language": r["language"],
                "kind": r["kind"],
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
        """Relações tocando o símbolo (from ou to via occurrences)."""
        detail = self.symbol_detail(repo_id, snapshot_id, ident)
        if detail is None:
            return None, f"symbol {ident!r} not found in repository"
        sym_id = int(detail["symbol_id"])

        conds = [
            "r.snapshot_id = ?",
            "(o1.logical_symbol_id = ? OR o2.logical_symbol_id = ?)",
        ]
        params: list[object] = [snapshot_id, sym_id, sym_id]
        if relation_type:
            conds.append("r.type = ?")
            params.append(relation_type)
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


def _escape_like(term: str) -> str:
    """Escapa curingas LIKE — input do usuário nunca vira padrão."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clamp_limit(limit: int | None) -> int:
    """Sane default + teto duro — saída nunca é ilimitada."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(0, min(int(limit), MAX_LIMIT))
