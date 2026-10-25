"""Fixtures de testes do pacote `domains` (lf-0110)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.storage import connect, migrate


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()
