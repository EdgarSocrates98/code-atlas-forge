"""Atomicidade transacional das migrações (Phase 0 / P1.3).

`executescript()` fazia COMMIT implícito — uma DDL falha no meio do
script deixava objetos aplicados sem registro em schema_migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.storage import migrations as mig
from code_atlas.storage.db import connect


def _fresh(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "ws" / "atlas.db")


def test_failed_migration_rolls_back_all_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _fresh(tmp_path)
    good = mig.migrations(fts5=mig.schema_version(conn) >= 0 and True)
    bad = mig.Migration(
        99,
        "poison",
        "CREATE TABLE poison_ok (id INTEGER);\nCREATE TABLE boom (oops ;",
    )
    monkeypatch.setattr(mig, "migrations", lambda *, fts5: [*good, bad])

    with pytest.raises(mig.MigrationError):
        mig.migrate(conn)

    # nada do script ruim pode ter sobrevivido
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "poison_ok" not in names
    assert "boom" not in names
    # nem o registro de sucesso
    v = conn.execute(
        "SELECT version FROM schema_migrations WHERE version = 99"
    ).fetchone()
    assert v is None


def test_recovers_and_reruns_after_failed_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _fresh(tmp_path)
    mig.migrate(conn)  # aplica v1+v2 de verdade
    before = mig.schema_version(conn)

    bad = mig.Migration(50, "will fail", "CREATE TABLE nope (bad syntax;")
    real = mig.migrations
    monkeypatch.setattr(
        mig, "migrations", lambda *, fts5: [*real(fts5=fts5), bad]
    )
    with pytest.raises(mig.MigrationError):
        mig.migrate(conn)
    assert mig.schema_version(conn) == before  # estado preservado

    monkeypatch.undo()
    assert mig.migrate(conn) == before  # reexecução limpa


def test_older_schema_db_opens_and_migrates(tmp_path: Path) -> None:
    conn = _fresh(tmp_path)
    mig.migrate(conn, fts5=False)  # aplica só v1 (banco "antigo")
    assert mig.schema_version(conn) == 1
    conn.close()

    conn2 = connect(tmp_path / "ws" / "atlas.db")  # reabre e aplica v2
    assert mig.migrate(conn2) == 2
    assert mig.migrate(conn2) == 2  # idempotente


def test_split_statements_handles_comments_and_multiline() -> None:
    sql = (
        "-- comentário isolado\n"
        "CREATE TABLE a (\n  id INTEGER,\n  x TEXT\n);\n"
        "CREATE INDEX i ON a (x);\n"
        "-- comentário no fim\n"
    )
    stmts = list(mig._split_statements(sql))
    assert len(stmts) == 2
    assert stmts[0].startswith("-- comentário")  # comentário viaja junto
    assert "CREATE INDEX" in stmts[1]
