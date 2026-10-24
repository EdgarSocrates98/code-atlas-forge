"""Fixtures de storage/indexing: DB migrado em tmp_path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    (root / "b.py").write_text("y = 2\n")
    return root


@pytest.fixture()
def coordinator(db: sqlite3.Connection) -> IndexCoordinator:
    return IndexCoordinator(db)
