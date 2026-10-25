"""Fixtures de federação — banco com dois repos indexados (lf-0118)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store
from federation.helpers import (
    REPO_A_FILES,
    REPO_B_FILES,
    index_repo,
    write_repo,
)


@pytest.fixture()
def fed_db(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, dict[str, object]]:
    """``(conn, ids)`` — ids = {repo_a, repo_b, snap_a, snap_b, roots}."""
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    root_a = write_repo(tmp_path / "repo_a", dict(REPO_A_FILES))
    root_b = write_repo(tmp_path / "repo_b", dict(REPO_B_FILES))
    snap_a = index_repo(conn, root_a)
    snap_b = index_repo(conn, root_b)
    store = Store(conn)
    sa = store.snapshot_by_id(snap_a)
    sb = store.snapshot_by_id(snap_b)
    assert sa is not None and sb is not None
    ids = {
        "repo_a": sa.repository_id,
        "repo_b": sb.repository_id,
        "snap_a": snap_a,
        "snap_b": snap_b,
        "root_a": root_a,
        "root_b": root_b,
    }
    yield conn, ids
    conn.close()
