"""Acesso a dados do grafo (GraphStore parcial do Cycle 1).

Somente queries parametrizadas. O Cycle 1 escreve inventário + snapshots;
``symbols``/``relations``/``evidence`` existem no schema mas não são
populados por parsing real ainda.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.contracts.models import Repository, Snapshot
from code_atlas.discovery.models import SourceFile, WarningKind


def _snapshot_from_row(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=int(row["id"]),
        repository_id=int(row["repository_id"]),
        git_ref=str(row["git_ref"]),
        commit_sha=row["commit_sha"],
        worktree_digest=str(row["worktree_digest"]),
        analyzer_versions=json.loads(row["analyzer_versions"]),
        parent_snapshot_id=row["parent_snapshot_id"],
        status=SnapshotStatus(str(row["status"])),
        created_at=str(row["created_at"]),
    )


class Store:
    """Repositório de dados sobre uma conexão sqlite3 já configurada."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- repositories -------------------------------------------------

    def get_or_create_repository(self, root_handle: str) -> int:
        """Repo estável por root_handle; não duplica em rescans."""
        row = self._conn.execute(
            "SELECT id FROM repositories WHERE root_handle = ?", (root_handle,)
        ).fetchone()
        if row:
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO repositories (root_handle) VALUES (?)", (root_handle,)
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def repository_id_for(self, root_handle: str) -> int | None:
        """Id do repo indexado ou None (read-only — nunca cria)."""
        row = self._conn.execute(
            "SELECT id FROM repositories WHERE root_handle = ?", (root_handle,)
        ).fetchone()
        return int(row["id"]) if row else None

    def repository_by_id(self, repo_id: int) -> Repository | None:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        if row is None:
            return None
        return Repository(
            id=int(row["id"]),
            root_handle=str(row["root_handle"]),
            origin=str(row["origin"]),
            git_provider=str(row["git_provider"]),
            default_branch=str(row["default_branch"]),
        )

    # --- snapshots ----------------------------------------------------

    def latest_ready_snapshot(self, repository_id: int) -> Snapshot | None:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE repository_id = ? AND status = 'READY' "
            "ORDER BY id DESC LIMIT 1",
            (repository_id,),
        ).fetchone()
        return _snapshot_from_row(row) if row else None

    def snapshot_by_id(self, snapshot_id: int) -> Snapshot | None:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return _snapshot_from_row(row) if row else None

    def insert_snapshot(
        self,
        repository_id: int,
        worktree_digest: str,
        analyzer_versions: dict[str, str],
        parent_snapshot_id: int | None,
        git_ref: str = "",
        commit_sha: str | None = None,
    ) -> int:
        """Cria snapshot em estado BUILDING."""
        cur = self._conn.execute(
            "INSERT INTO snapshots (repository_id, git_ref, commit_sha, "
            "worktree_digest, analyzer_versions, parent_snapshot_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'BUILDING')",
            (
                repository_id,
                git_ref,
                commit_sha,
                worktree_digest,
                json.dumps(analyzer_versions, sort_keys=True),
                parent_snapshot_id,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def set_snapshot_status(
        self, snapshot_id: int, status: SnapshotStatus, error: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE snapshots SET status = ?, error = COALESCE(?, error) WHERE id = ?",
            (str(status), error, snapshot_id),
        )

    def publish_snapshot(self, repository_id: int, snapshot_id: int) -> None:
        """Marca o novo READY e torna STALE os anteriores (mesma transação)."""
        self._conn.execute(
            "UPDATE snapshots SET status = 'STALE' "
            "WHERE repository_id = ? AND status = 'READY' AND id != ?",
            (repository_id, snapshot_id),
        )
        self.set_snapshot_status(snapshot_id, SnapshotStatus.READY)

    # --- source files + inventory ------------------------------------

    def upsert_source_file(self, repository_id: int, f: SourceFile) -> int:
        """Dedup por (repository_id, relative_path) — chave de conteúdo estável."""
        row = self._conn.execute(
            "SELECT id FROM source_files WHERE repository_id = ? AND relative_path = ?",
            (repository_id, f.path),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO source_files (repository_id, relative_path, language, "
            "sha256, size_bytes, ignored) VALUES (?, ?, ?, ?, ?, ?)",
            (repository_id, f.path, f.language, f.sha256, f.size_bytes, int(f.ignored)),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def insert_inventory_rows(
        self, snapshot_id: int, rows: Iterable[tuple[int, SourceFile]]
    ) -> None:
        """Insere (snapshot, source_file) com atributos da ocorrência."""
        self._conn.executemany(
            "INSERT INTO file_inventory (snapshot_id, source_file_id, sha256, "
            "size_bytes, ignored) VALUES (?, ?, ?, ?, ?)",
            [(snapshot_id, fid, f.sha256, f.size_bytes, int(f.ignored)) for fid, f in rows],
        )

    def inventory_count(self, snapshot_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM file_inventory WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return int(row["c"])

    def foreign_key_violations(self) -> list[str]:
        """Lista violações FK pendentes (integrity gate antes do READY)."""
        rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        return [str(tuple(r)) for r in rows]

    def cross_snapshot_relations(self, snapshot_id: int) -> list[int]:
        """Relações cujos endpoints apontam fora do próprio snapshot.

        ``from_id`` é NOT NULL (JOIN); ``to_id`` pode ser NULL em alvos
        unresolved/external — LEFT JOIN + checagem só quando presente.
        """
        rows = self._conn.execute(
            """
            SELECT r.id FROM relations r
            JOIN symbol_occurrences o1 ON o1.id = r.from_id
            LEFT JOIN symbol_occurrences o2 ON o2.id = r.to_id
            WHERE r.snapshot_id = ?
              AND (o1.snapshot_id != ?
                   OR (r.to_id IS NOT NULL AND o2.snapshot_id != ?))
            """,
            (snapshot_id, snapshot_id, snapshot_id),
        ).fetchall()
        return [int(r["id"]) for r in rows]

    # --- symbols / occurrences / relations / evidence (Cycle 2) -------

    def upsert_symbol(
        self, repository_id: int, language: str, qualified_name: str, kind: str
    ) -> tuple[int, bool]:
        """Dedup por (repo, language, qname, kind) — retorna (id, created)."""
        row = self._conn.execute(
            "SELECT id FROM symbols WHERE repository_id = ? AND language = ? "
            "AND qualified_name = ? AND kind = ?",
            (repository_id, language, qualified_name, kind),
        ).fetchone()
        if row:
            return int(row["id"]), False
        cur = self._conn.execute(
            "INSERT INTO symbols (repository_id, language, qualified_name, kind) "
            "VALUES (?, ?, ?, ?)",
            (repository_id, language, qualified_name, kind),
        )
        return int(cur.lastrowid), True  # type: ignore[arg-type]

    def insert_fts_symbol(
        self, symbol_id: int, qualified_name: str, language: str, kind: str
    ) -> None:
        """Popula o índice FTS (só chamado quando a build suporta FTS5)."""
        self._conn.execute(
            "INSERT INTO fts_symbols (rowid, qualified_name, language, kind) "
            "VALUES (?, ?, ?, ?)",
            (symbol_id, qualified_name, language, kind),
        )

    def insert_occurrence(
        self,
        snapshot_id: int,
        symbol_id: int,
        source_file_id: int,
        span_start: int | None,
        span_end: int | None,
        line: int | None,
        col: int | None,
        content_hash: str,
        parse_status: str = "parsed",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO symbol_occurrences (snapshot_id, logical_symbol_id, "
            "source_file_id, span_start, span_end, line, col, content_hash, "
            "parse_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id, symbol_id, source_file_id, span_start, span_end,
                line, col, content_hash, parse_status,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def insert_evidence(
        self,
        snapshot_id: int,
        source_file_id: int,
        path: str,
        span_json: str | None,
        content_hash: str,
        extractor: str,
        extractor_version: str,
        notes: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO evidence (snapshot_id, source_file_id, path, span, "
            "content_hash, extractor, extractor_version, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id, source_file_id, path, span_json, content_hash,
                extractor, extractor_version, notes,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def insert_relations(self, rows: Iterable[tuple[object, ...]]) -> int:
        """Batch de relações — executemany (nenhum id precisa voltar)."""
        self._conn.executemany(
            "INSERT INTO relations (snapshot_id, from_id, to_id, to_unresolved, "
            "type, resolution_status, confidence_class, resolver, evidence_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )
        return self._conn.total_changes  # para telemetria externa

    # --- index jobs ---------------------------------------------------

    def insert_index_job(self, repository_id: int, manifest_hash: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO index_jobs (repository_id, status, manifest_hash) "
            "VALUES (?, 'running', ?)",
            (repository_id, manifest_hash),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def finish_index_job(
        self,
        job_id: int,
        status: str,
        snapshot_id: int | None = None,
        counts: dict[str, int] | None = None,
        failures: tuple[str, ...] = (),
    ) -> None:
        self._conn.execute(
            "UPDATE index_jobs SET status = ?, snapshot_id = ?, counts = ?, "
            "failures = ?, finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (
                status,
                snapshot_id,
                json.dumps(counts or {}, sort_keys=True),
                json.dumps(list(failures)),
                job_id,
            ),
        )

    def last_warnings_digest(self, warnings: Iterable[WarningKind]) -> str:
        return json.dumps(sorted({str(w) for w in warnings}))
