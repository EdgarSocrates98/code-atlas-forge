"""Persistência das tabelas de federação (lf-0118, schema v10).

Mesma disciplina de ``storage.store``: somente queries parametrizadas,
SQL constante, nenhuma concatenação. ``FederationStore`` cobre apenas as
tabelas v10 — leituras de ``repositories``/``snapshots``/domínio passam
pelo ``Store`` compartilhado (uma conexão, dois repositórios).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from code_atlas.federation.model import (
    FederationMember,
    FederationWorkspace,
)


class FederationStore:
    """CRUD mínimo de ``federation_workspaces``/``federation_members``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- workspaces -------------------------------------------------

    def create_workspace(self, name: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO federation_workspaces (name) VALUES (?)", (name,)
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def workspace_by_id(self, workspace_id: int) -> FederationWorkspace | None:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM federation_workspaces"
            " WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        return _ws_from_row(row) if row is not None else None

    def workspace_by_name(self, name: str) -> FederationWorkspace | None:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM federation_workspaces"
            " WHERE name = ?",
            (name,),
        ).fetchone()
        return _ws_from_row(row) if row is not None else None

    def list_workspaces(self) -> list[FederationWorkspace]:
        rows = self._conn.execute(
            "SELECT id, name, created_at FROM federation_workspaces"
            " ORDER BY name"
        ).fetchall()
        return [_ws_from_row(r) for r in rows]

    def member_count(self, workspace_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM federation_members WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        return int(row["c"])

    # --- members ----------------------------------------------------

    def add_member(
        self, workspace_id: int, repository_id: int
    ) -> int:
        """Registra repo no workspace (``INSERT OR IGNORE`` — idempotente).

        Retorna a row id do membro (existente ou criada).
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO federation_members"
            " (workspace_id, repository_id) VALUES (?, ?)",
            (workspace_id, repository_id),
        )
        row = self._conn.execute(
            "SELECT id FROM federation_members"
            " WHERE workspace_id = ? AND repository_id = ?",
            (workspace_id, repository_id),
        ).fetchone()
        assert row is not None  # UNIQUE + INSERT OR IGNORE garante a linha
        return int(row["id"])

    def remove_member(self, workspace_id: int, repository_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM federation_members"
            " WHERE workspace_id = ? AND repository_id = ?",
            (workspace_id, repository_id),
        )
        return int(cur.rowcount or 0) > 0

    def member_for(
        self, workspace_id: int, repository_id: int
    ) -> FederationMember | None:
        row = self._conn.execute(
            "SELECT id, workspace_id, repository_id, pinned_snapshot_id,"
            " added_at FROM federation_members"
            " WHERE workspace_id = ? AND repository_id = ?",
            (workspace_id, repository_id),
        ).fetchone()
        return _member_from_row(row) if row is not None else None

    def list_members(self, workspace_id: int) -> list[FederationMember]:
        rows = self._conn.execute(
            "SELECT id, workspace_id, repository_id, pinned_snapshot_id,"
            " added_at FROM federation_members"
            " WHERE workspace_id = ? ORDER BY repository_id",
            (workspace_id,),
        ).fetchall()
        return [_member_from_row(r) for r in rows]

    def set_pinned_snapshot(
        self, workspace_id: int, repository_id: int, snapshot_id: int | None
    ) -> None:
        """Grava (ou limpa, ``None``) o pin do membro — validação é do engine."""
        self._conn.execute(
            "UPDATE federation_members SET pinned_snapshot_id = ?"
            " WHERE workspace_id = ? AND repository_id = ?",
            (snapshot_id, workspace_id, repository_id),
        )

    def repo_identities(
        self, repository_ids: Iterable[int]
    ) -> dict[int, str]:
        """``{repository_id: identity_key}`` — identidade lógica do membro.

        Componente "repo identity" da cache key da view (lf-0134): um
        re-bind de ``identity_key`` sob o mesmo ``repo_id`` muda a
        identidade resolvida mesmo com snapshots inalterados.
        """
        ids = sorted({int(r) for r in repository_ids})
        if not ids:
            return {}
        # SQL constante (auditoria AST): IN variável via json_each —
        # mesmo padrão de hidratação em lote do resto do código.
        rows = self._conn.execute(
            "SELECT id, identity_key FROM repositories"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        ).fetchall()
        return {int(r["id"]): str(r["identity_key"]) for r in rows}

    # --- leitura federada ------------------------------------------------

    def snapshot_entity_occurrences(
        self, snapshot_id: int
    ) -> list[sqlite3.Row]:
        """Ocorrências de entidade de domínio de UM snapshot membro.

        Mesma forma de ``Store.domain_occurrences_snapshot`` com
        ``line``/``col`` incluídos — a leitura federada nunca mistura
        snapshots: o ``WHERE`` é sempre o ``snapshot_id`` resolvido para
        aquele repo-membro.
        """
        return self._conn.execute(
            """
            SELECT deo.id AS occ_id, deo.entity_id, deo.line, deo.col,
                   deo.extraction_status,
                   de.domain, de.kind, de.identity_key, de.qualified_name,
                   de.name, de.catalog, de.namespace, de.environment,
                   sf.relative_path AS source_path
              FROM domain_entity_occurrences deo
              JOIN domain_entities de ON de.id = deo.entity_id
              LEFT JOIN source_files sf ON sf.id = deo.source_file_id
             WHERE deo.snapshot_id = ?
             ORDER BY deo.id
            """,
            (snapshot_id,),
        ).fetchall()


def _ws_from_row(row: sqlite3.Row) -> FederationWorkspace:
    return FederationWorkspace(
        id=int(row["id"]),
        name=str(row["name"]),
        created_at=str(row["created_at"]),
    )


def _member_from_row(row: sqlite3.Row) -> FederationMember:
    return FederationMember(
        id=int(row["id"]),
        workspace_id=int(row["workspace_id"]),
        repository_id=int(row["repository_id"]),
        pinned_snapshot_id=(
            None
            if row["pinned_snapshot_id"] is None
            else int(row["pinned_snapshot_id"])
        ),
        added_at=str(row["added_at"]),
    )
