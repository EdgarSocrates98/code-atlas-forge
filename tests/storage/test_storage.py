"""Testes de storage: migrações, pragmas, snapshot lifecycle, concorrência."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate, schema_version
from code_atlas.storage.store import Store

EXPECTED_TABLES = {
    "schema_migrations", "repositories", "snapshots", "source_files",
    "file_inventory", "symbols", "symbol_occurrences", "relations",
    "evidence", "index_jobs",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ).fetchall()
    return {r["name"] for r in rows}


def test_migrations_fresh_db(db: sqlite3.Connection) -> None:
    assert schema_version(db) >= 1
    assert _tables(db) >= EXPECTED_TABLES
    fts_tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fts_symbols'"
    )}
    assert fts_tables == {"fts_symbols"}  # build tem FTS5


def test_migrations_idempotent(db: sqlite3.Connection) -> None:
    v1 = schema_version(db)
    assert migrate(db) == v1  # re-run limpo
    rows = db.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()
    assert rows["c"] == v1


def test_migrations_checksum_drift_detected(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.db")
    migrate(conn)
    conn.execute(
        "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
    )
    conn.commit()
    from code_atlas.storage.migrations import MigrationError

    with pytest.raises(MigrationError, match="drift"):
        migrate(conn)
    conn.close()


def test_pragmas(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fk_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO snapshots (repository_id, worktree_digest, status) "
            "VALUES (?, ?, 'READY')",
            (999, "d"),
        )


def test_snapshot_lifecycle_happy_path(
    coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection
) -> None:
    out = coordinator.index(corpus)
    assert out.status == "success"
    assert out.files_indexed == 2
    snap = Store(db).snapshot_by_id(out.snapshot_id)  # type: ignore[arg-type]
    assert snap is not None and snap.status is SnapshotStatus.READY
    job = db.execute(
        "SELECT status FROM index_jobs WHERE id = ?", (out.job_id,)
    ).fetchone()
    assert job["status"] == "success"


def test_noop_reindex(coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection) -> None:
    first = coordinator.index(corpus)
    second = coordinator.index(corpus)
    assert second.status == "noop" and second.noop
    assert second.snapshot_id == first.snapshot_id
    snaps = db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()
    assert snaps["c"] == 1  # nenhum snapshot extra criado
    inv = db.execute(
        "SELECT COUNT(*) AS c FROM file_inventory WHERE snapshot_id = ?",
        (first.snapshot_id,),
    ).fetchone()
    assert inv["c"] == 2  # sem duplicatas


def test_changed_inventory_new_snapshot(
    coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection
) -> None:
    first = coordinator.index(corpus)
    (corpus / "c.py").write_text("z = 3\n")
    second = coordinator.index(corpus)
    assert second.status == "success"
    assert second.snapshot_id != first.snapshot_id
    old = Store(db).snapshot_by_id(first.snapshot_id)  # type: ignore[arg-type]
    assert old is not None and old.status is SnapshotStatus.STALE
    new = Store(db).snapshot_by_id(second.snapshot_id)  # type: ignore[arg-type]
    assert new is not None and new.parent_snapshot_id == first.snapshot_id


def test_failed_index_preserves_ready(
    coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection
) -> None:
    first = coordinator.index(corpus)
    assert first.status == "success"

    # Força falha dentro da transação de publish.
    store = Store(db)
    orig = store.insert_inventory_rows
    store.insert_inventory_rows = lambda *a, **k: (_ for _ in ()).throw(
        sqlite3.IntegrityError("simulated failure")
    )
    coord2 = IndexCoordinator(db)
    coord2._store = store  # type: ignore[assignment]
    (corpus / "new.py").write_text("n = 1\n")
    failed = coord2.index(corpus)
    assert failed.status == "failed" and failed.error
    store.insert_inventory_rows = orig

    ready = Store(db).latest_ready_snapshot(
        db.execute("SELECT repository_id FROM snapshots WHERE id = ?",
                   (first.snapshot_id,)).fetchone()["repository_id"]
    )
    assert ready is not None and ready.id == first.snapshot_id
    assert ready.status is SnapshotStatus.READY
    # FAILED inspectável
    failed_snap = Store(db).snapshot_by_id(failed.snapshot_id)  # type: ignore[arg-type]
    assert failed_snap is not None and failed_snap.status is SnapshotStatus.FAILED
    job = db.execute(
        "SELECT status, failures FROM index_jobs WHERE id = ?", (failed.job_id,)
    ).fetchone()
    assert job["status"] == "failed" and "simulated" in job["failures"]


def test_concurrent_readers_during_write(tmp_path: Path, corpus: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    conn = connect(db_path)
    migrate(conn)
    IndexCoordinator(conn).index(corpus)
    conn.close()

    errors: list[str] = []

    def reader() -> None:
        try:
            r = connect(db_path)
            for _ in range(20):
                r.execute("SELECT COUNT(*) FROM snapshots").fetchone()
                r.execute("SELECT COUNT(*) FROM file_inventory").fetchone()
            r.close()
        except sqlite3.Error as exc:
            errors.append(str(exc))

    def writer() -> None:
        try:
            writer_conn = connect(db_path)  # conexão criada na própria thread
            (corpus / "w.py").write_text("w = 1\n")
            IndexCoordinator(writer_conn).index(corpus)
            writer_conn.close()
        except sqlite3.Error as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=reader) for _ in range(4)]
    wt = threading.Thread(target=writer)
    for t in threads:
        t.start()
    wt.start()
    wt.join()
    for t in threads:
        t.join()
    assert errors == []  # leitores e escritor nunca falham sob WAL


def test_atomic_publish_single_commit(
    coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection
) -> None:
    # Durante a transação, nada é visível até commit; após, tudo consistente.
    out = coordinator.index(corpus)
    assert out.status == "success"
    fk = db.execute("PRAGMA foreign_key_check").fetchall()
    assert fk == []
    # exatamente 1 READY por repo
    ready = db.execute(
        "SELECT COUNT(*) AS c FROM snapshots WHERE status = 'READY'"
    ).fetchone()
    assert ready["c"] == 1


def test_inventory_dedup_content_keyed(
    coordinator: IndexCoordinator, corpus: Path, db: sqlite3.Connection
) -> None:
    coordinator.index(corpus)
    (corpus / "a.py").write_text("x = 1\n")  # mesmo conteúdo, mtime muda
    out = coordinator.index(corpus)
    assert out.status in ("noop", "success")
    files = db.execute(
        "SELECT COUNT(*) AS c FROM source_files WHERE relative_path = 'a.py'"
    ).fetchone()
    assert files["c"] == 1  # dedup por (repo, path), não por timestamp
