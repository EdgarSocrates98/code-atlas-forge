"""IndexCoordinator: scan → BUILDING → validação → READY atômico (Blueprint §6).

Regras:

- Inventário idêntico (mesmo ``worktree_digest`` + mesmas versões de
  analisadores) é **no-op**: não cria snapshot novo, registra job ``noop``.
- Falha dentro da transação faz rollback completo; o snapshot é marcado
  ``FAILED`` em transação separada para diagnóstico e o ``READY`` anterior
  permanece intacto.
- Escritor único via ``BEGIN IMMEDIATE``; leitores concorrentes sob WAL.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.discovery.models import ScanResult
from code_atlas.discovery.scanner import scan as default_scan
from code_atlas.storage.store import Store

ANALYZER_NAME = "code-atlas"
ANALYZER_VERSION = "0.1.0"


def _analyzer_versions(scan: ScanResult) -> dict[str, str]:
    return {
        "scanner": f"{ANALYZER_NAME}-scanner/{ANALYZER_VERSION}",
        "scan_mode": str(scan.mode),
    }


@dataclass(frozen=True)
class IndexOutcome:
    """Resultado de uma tentativa de indexação."""

    snapshot_id: int | None
    job_id: int
    status: str  # success | failed | noop
    files_indexed: int
    noop: bool = False
    error: str = ""


class IndexCoordinator:
    """Coordena indexação sobre uma conexão sqlite3 migrada."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        scanner: Callable[[Path], ScanResult] = default_scan,
    ) -> None:
        self._conn = conn
        self._store = Store(conn)
        self._scanner = scanner

    def index(self, root: Path) -> IndexOutcome:
        """Escaneia `root` e publica snapshot READY (ou no-op) atomicamente."""
        scan = self._scanner(Path(root))
        digest = scan.manifest_sha256()
        versions = _analyzer_versions(scan)
        store = self._store

        # Job registrado em transação própria para sobreviver a rollback —
        # falhas precisam ficar inspecionáveis.
        with self._conn:
            repo_id = store.get_or_create_repository(str(Path(root).resolve()))
            job_id = store.insert_index_job(repo_id, digest)

        # Escritor único: BEGIN IMMEDIATE serializa com outros escritores.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = store.latest_ready_snapshot(repo_id)

            if (
                current is not None
                and current.worktree_digest == digest
                and dict(current.analyzer_versions) == versions
            ):
                store.finish_index_job(
                    job_id, "noop", snapshot_id=current.id,
                    counts={"files": 0}, failures=(),
                )
                self._conn.commit()
                return IndexOutcome(
                    snapshot_id=current.id, job_id=job_id, status="noop",
                    files_indexed=0, noop=True,
                )

            snapshot_id = store.insert_snapshot(
                repo_id,
                worktree_digest=digest,
                analyzer_versions=versions,
                parent_snapshot_id=current.id if current else None,
                git_ref=scan.git.branch,
                commit_sha=scan.git.head_sha or None,
            )
            rows = [(store.upsert_source_file(repo_id, f), f) for f in scan.files]
            store.insert_inventory_rows(snapshot_id, rows)
            self._validate(snapshot_id)
            store.publish_snapshot(repo_id, snapshot_id)
            store.finish_index_job(
                job_id, "success", snapshot_id=snapshot_id,
                counts={"files": len(rows), "secrets_skipped": scan.secret_count},
                failures=tuple(str(w.kind) for w in scan.warnings),
            )
            self._conn.commit()
            return IndexOutcome(
                snapshot_id=snapshot_id, job_id=job_id, status="success",
                files_indexed=len(rows),
            )
        except Exception as exc:
            self._conn.rollback()
            failed_snap = self._mark_failed(repo_id=repo_id, job_id=job_id, error=str(exc))
            return IndexOutcome(
                snapshot_id=failed_snap, job_id=job_id, status="failed",
                files_indexed=0, error=str(exc),
            )

    def _validate(self, snapshot_id: int) -> None:
        """Integrity gate antes do READY; levanta para abortar a transação."""
        violations = self._store.foreign_key_violations()
        if violations:
            raise sqlite3.IntegrityError(
                f"FK violations in snapshot {snapshot_id}: {violations[:5]}"
            )
        cross = self._store.cross_snapshot_relations(snapshot_id)
        if cross:
            raise sqlite3.IntegrityError(
                f"cross-snapshot relations {cross[:5]} in snapshot {snapshot_id}"
            )

    def _mark_failed(self, repo_id: int, job_id: int, error: str) -> int:
        """Insere snapshot FAILED para diagnóstico (transação separada).

        O BUILDING abortado já sofreu rollback; registramos um FAILED novo
        para que falhas fiquem inspecionáveis sem tocar no READY anterior.
        """
        with self._conn:
            snap_id = self._store.insert_snapshot(
                repo_id,
                worktree_digest="",
                analyzer_versions={},
                parent_snapshot_id=None,
            )
            self._store.set_snapshot_status(snap_id, SnapshotStatus.FAILED, error[:500])
            self._store.finish_index_job(
                job_id, "failed", snapshot_id=snap_id, failures=(error[:500],)
            )
        return snap_id
