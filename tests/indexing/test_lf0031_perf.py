"""lf-0031 Phase 7: instrumentação por etapa + otimizações SQLite.

Cobre: ``get_many`` batchado (hit/miss/corrompido/chunk boundary),
campos ``stage_ms_*`` no job report, GC opt-in de snapshots STALE
(retain_snapshots) e parsing da config ``[indexing]``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.config import ConfigError, WorkspaceConfig
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.cache import ParseResultCache
from code_atlas.indexing.reuse import cache_key_for, config_digest
from code_atlas.parsers.engine import ParseEngine
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (r / "pkg" / "app.py").write_text(
        "from pkg.util import helper\n\n\ndef main():\n    return helper()\n"
    )
    (r / "solo.py").write_text("def isolated():\n    return 0\n")
    return r


def _repo_id(db: sqlite3.Connection, repo: Path) -> int:
    rid = Store(db).repository_id_for(str(repo.resolve()))
    assert rid is not None
    return rid


def _keys(db: sqlite3.Connection, repo: Path) -> list:
    """CacheKeys reais dos arquivos indexados (via scan do coordinator)."""
    from code_atlas.discovery.scanner import scan

    s = scan(repo)
    eng = ParseEngine()
    digest = config_digest(eng)
    out = []
    for f in s.files:
        adapter = eng.registry.for_path(f.path)
        if adapter is not None:
            out.append(cache_key_for(f, adapter, digest))
    return out


def test_get_many_returns_all_hits(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    cache = ParseResultCache(db, _repo_id(db, repo))
    keys = _keys(db, repo)
    got = cache.get_many(keys)
    assert sorted(got) == sorted(k.path for k in keys)
    # paridade com get(): mesmo resultado desserializado
    k0 = next(k for k in keys if k.path == "solo.py")
    assert got["solo.py"] == cache.get(k0)


def test_get_many_corrupt_blob_is_miss(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    rid = _repo_id(db, repo)
    db.execute(
        "UPDATE parse_result_cache SET payload = 'not-json{' "
        "WHERE repository_id = ? AND path = 'solo.py'",
        (rid,),
    )
    # sha256 do payload também precisa bater — corrupt blob significa
    # alterar payload E recomputar o sha (ou o sha falha primeiro).
    import hashlib

    db.execute(
        "UPDATE parse_result_cache SET payload_sha256 = ? "
        "WHERE repository_id = ? AND path = 'solo.py'",
        (hashlib.sha256(b"not-json{").hexdigest(), rid),
    )
    keys = _keys(db, repo)
    got = ParseResultCache(db, rid).get_many(keys)
    assert "solo.py" not in got
    assert "pkg/app.py" in got  # demais hits intactos


def test_get_many_chunk_boundary(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    rid = _repo_id(db, repo)
    keys = _keys(db, repo)
    real = keys[0]
    # 450 chaves: 1 real + 449 sintéticas → cruza o chunk de 400
    import dataclasses

    fake = [
        dataclasses.replace(real, path=f"fake{i}.py")
        for i in range(449)
    ]
    got = ParseResultCache(db, rid).get_many([real, *fake])
    assert list(got) == [real.path]


_STAGE_KEYS = [
    "stage_ms_discovery",
    "stage_ms_hashing",
    "stage_ms_invalidation",
    "stage_ms_parse",
    "stage_ms_graph_build",
    "stage_ms_semantic",
    "stage_ms_validation",
    "stage_ms_persist_rows",
    "stage_ms_fk_check",
    "stage_ms_publish_gc",
    "stage_ms_persist_publish",
]


def test_stage_instrumentation_reported(db, repo: Path) -> None:
    coord = IndexCoordinator(db)
    out = coord.index(repo)
    assert out.status == "success"
    for k in _STAGE_KEYS:
        assert isinstance(out.stats.get(k), int), k
    # incremental: cache_lookup aparece
    (repo / "solo.py").write_text("def isolated():\n    return 1\n")
    out2 = coord.index(repo, incremental=True)
    assert isinstance(out2.stats.get("stage_ms_cache_lookup"), int)
    assert out2.stats["files_reused"] > 0


def test_snapshot_retention_prunes_stale(db, repo: Path) -> None:
    coord = IndexCoordinator(db, snapshot_retention=1)
    for i in range(3):
        (repo / "solo.py").write_text(f"def isolated():\n    return {i}\n")
        out = coord.index(repo, incremental=(i > 0))
        assert out.status == "success"
    rows = db.execute(
        "SELECT status, COUNT(*) AS c FROM snapshots GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    assert counts.get("READY") == 1
    assert counts.get("STALE", 0) <= 1
    assert out.stats["snapshots_pruned"] >= 1
    # integridade referencial preservada pelo CASCADE
    assert Store(db).foreign_key_violations() == []


def test_snapshot_retention_disabled_by_default(db, repo: Path) -> None:
    coord = IndexCoordinator(db)  # retention=0 → ilimitado
    for i in range(3):
        (repo / "solo.py").write_text(f"def isolated():\n    return {i}\n")
        coord.index(repo, incremental=(i > 0))
    stale = db.execute(
        "SELECT COUNT(*) AS c FROM snapshots WHERE status = 'STALE'"
    ).fetchone()["c"]
    assert stale == 2  # histórico completo preservado


def test_gc_preserves_failed(db, repo: Path) -> None:
    store = Store(db)
    rid = store.get_or_create_repository("k", str(repo.resolve()))
    # semeadura manual: 1 READY + 1 STALE + 1 FAILED
    s1 = store.insert_snapshot(rid, worktree_digest="a" * 64,
                               analyzer_versions={}, parent_snapshot_id=None)
    s2 = store.insert_snapshot(rid, worktree_digest="b" * 64,
                               analyzer_versions={}, parent_snapshot_id=s1)
    s3 = store.insert_snapshot(rid, worktree_digest="c" * 64,
                               analyzer_versions={}, parent_snapshot_id=s2)
    from code_atlas.contracts.enums import SnapshotStatus

    store.set_snapshot_status(s1, SnapshotStatus.STALE)
    store.set_snapshot_status(s2, SnapshotStatus.FAILED)
    store.set_snapshot_status(s3, SnapshotStatus.READY)
    removed = store.gc_stale_snapshots(rid, keep=0)
    assert removed == 1  # só o STALE
    left = {
        r["id"]: r["status"]
        for r in db.execute("SELECT id, status FROM snapshots")
    }
    assert left[s2] == "FAILED"
    assert left[s3] == "READY"


def test_config_retain_snapshots(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'schema_version = 1\n[workspace]\nroot = "."\n'
        "[indexing]\nretain_snapshots = 3\n",
        encoding="utf-8",
    )
    assert WorkspaceConfig.load(cfg).retain_snapshots == 3
    cfg.write_text(
        '[workspace]\nroot = "."\nretain_snapshots = -1\n'
        "[indexing]\nretain_snapshots = -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        WorkspaceConfig.load(cfg)
    cfg.write_text(
        'indexing = "oops"\n[workspace]\nroot = "."\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        WorkspaceConfig.load(cfg)
