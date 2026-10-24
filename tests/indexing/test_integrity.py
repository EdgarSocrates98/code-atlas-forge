"""lf-0015 Phase 0: integridade scan→parse, evidências e concorrência.

- conteúdo parseado precisa bater com o sha256 do manifest;
- evidência filtrada não pode desalinhar índices de relações;
- dois indexadores concorrentes → apenas uma publicação READY.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from code_atlas.discovery import scan
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.coordinator import IndexOutcome
from code_atlas.indexing.persist import persist_graph
from code_atlas.parsers.contracts import (
    EvidenceRecord,
    GraphBuildResult,
    GraphEdge,
    GraphNode,
)
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    r = tmp_path / "tiny"
    r.mkdir()
    (r / "a.py").write_text("def f(): return 1\n")
    (r / "b.py").write_text("def g(): return 2\n")
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


# --- 0.2: scan→parse content identity ----------------------------------


def test_content_mismatch_between_scan_and_parse(
    db: sqlite3.Connection, tiny_repo: Path
) -> None:
    """Arquivo alterado entre scan e parse nunca vira READY silencioso."""
    stale = scan(tiny_repo)
    (tiny_repo / "a.py").write_text("def mudado(): return 99\n")

    coord = IndexCoordinator(db, scanner=lambda _root: stale)
    out = coord.index(tiny_repo)

    # snapshot é publicado, mas a.py marcada como falha auditável
    assert out.status == "success"
    occ = db.execute(
        "SELECT o.parse_status FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE f.relative_path='a.py' AND o.snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    assert occ and all(r["parse_status"] == "parse-failed" for r in occ)
    ev = db.execute(
        "SELECT notes FROM evidence WHERE path='a.py' AND snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    assert any("parse-failure:content-mismatch" in (r["notes"] or "")
               for r in ev)
    # b.py (inalterada) parseou normalmente
    ok = db.execute(
        "SELECT o.parse_status FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE f.relative_path='b.py' AND o.snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    assert ok and all(r["parse_status"] == "parsed" for r in ok)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="os.symlink exige privilégio no Windows")
def test_symlink_swap_between_scan_and_parse(
    db: sqlite3.Connection, tiny_repo: Path
) -> None:
    """Arquivo trocado por symlink-para-fora entre scan e parse → falha."""
    stale = scan(tiny_repo)
    outside = tiny_repo.parent / "outside.txt"
    outside.write_text("fora\n")
    (tiny_repo / "a.py").unlink()
    os.symlink(outside, tiny_repo / "a.py")

    coord = IndexCoordinator(db, scanner=lambda _root: stale)
    out = coord.index(tiny_repo)
    assert out.status == "success"
    occ = db.execute(
        "SELECT o.parse_status FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE f.relative_path='a.py' AND o.snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    # symlink escape recusado OU hash divergente — nunca parse do alvo
    assert occ and all(r["parse_status"] == "parse-failed" for r in occ)
    ev = db.execute(
        "SELECT notes FROM evidence WHERE path='a.py' AND snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    assert any("parse-failure:" in (r["notes"] or "") for r in ev)


def test_unhashed_file_is_parse_failed(
    db: sqlite3.Connection, tiny_repo: Path
) -> None:
    """sha256 None (oversize/erro) → parse-failed, nunca identidade cega."""
    real = scan(tiny_repo)
    files = tuple(
        replace(f, sha256=None) if f.path == "a.py" else f
        for f in real.files
    )
    tampered = replace(real, files=files)
    coord = IndexCoordinator(db, scanner=lambda _root: tampered)
    out = coord.index(tiny_repo)
    assert out.status == "success"
    occ = db.execute(
        "SELECT o.parse_status FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE f.relative_path='a.py' AND o.snapshot_id=?",
        (out.snapshot_id,)).fetchall()
    assert occ and all(r["parse_status"] == "parse-failed" for r in occ)


# --- 0.3: mapeamento explícito de evidências ----------------------------


def test_evidence_index_survives_filtered_records(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Evidência com path fora do inventário não desloca as demais."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "x.py").write_text("def f(): return 1\n")
    scan_res = scan(repo)
    store = Store(db)
    repo_id = store.get_or_create_repository(str(repo))
    snap_id = store.insert_snapshot(repo_id, "d", {}, None)
    rows = [(store.upsert_source_file(repo_id, f), f) for f in scan_res.files]
    store.insert_inventory_rows(snap_id, rows)
    file_ids = {f.path: fid for fid, f in rows}

    file_node = GraphNode(
        node_id="file:x.py", kind="file", name="x.py",
        qualified_name="x.py", path="x.py",
    )
    sym_node = GraphNode(
        node_id="sym:x.py.f", kind="function", name="f",
        qualified_name="x.py.f", language="python", path="x.py",
    )
    evidence = (
        # idx 0 → path FORA do inventário (filtrado na persistência)
        EvidenceRecord(path="ausente.py", span=None, content_hash="h0",
                       extractor="t", extractor_version="1"),
        # idx 1 → evidência real referenciada pela aresta
        EvidenceRecord(path="x.py", span=None, content_hash="h1",
                       extractor="t", extractor_version="1"),
    )
    edge = GraphEdge(
        type="DEFINES", from_id="file:x.py", to_id="sym:x.py.f",
        resolution_status="resolved", evidence_index=(1,),
    )
    graph = GraphBuildResult(
        nodes=(file_node, sym_node), edges=(edge,), evidence=evidence,
        diagnostics=(),
    )
    persist_graph(store, repo_id, snap_id, file_ids, graph, fts5=False)

    rel = db.execute(
        "SELECT evidence_ids FROM relations WHERE snapshot_id=?",
        (snap_id,)).fetchone()
    ev_ids = json.loads(rel["evidence_ids"])
    assert len(ev_ids) == 1
    # o id persistido corresponde à evidência do path x.py, não a outra
    got = db.execute(
        "SELECT path FROM evidence WHERE id=?", (ev_ids[0],)).fetchone()
    assert got["path"] == "x.py"
    # e a evidência filtrada sequer existe na tabela
    n = db.execute("SELECT COUNT(*) AS c FROM evidence").fetchone()
    assert n["c"] == 1


# --- 0.4: consistência concorrente ---------------------------------------


def test_concurrent_indexers_single_ready(
    tmp_path: Path, tiny_repo: Path
) -> None:
    """Dois coordenadores no mesmo DB: só uma publicação READY."""
    db_path = tmp_path / "shared.db"
    boot = connect(db_path)
    migrate(boot)
    boot.close()

    barrier = threading.Barrier(2)
    outcomes: list[IndexOutcome] = []
    errors: list[BaseException] = []

    def worker() -> None:
        conn = connect(db_path)  # conexão própria por thread (sqlite)
        try:
            def gated_scanner(root: Path):
                res = scan(root)
                barrier.wait(timeout=15)  # ambos prontos p/ escrever juntos
                return res
            out = IndexCoordinator(conn, scanner=gated_scanner).index(tiny_repo)
            outcomes.append(out)
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    statuses = sorted(o.status for o in outcomes)
    # um publica READY; o outro vê o digest idêntico → noop (ou falha
    # honesta de lock — mas nunca 2 READY para a mesma revisão)
    assert statuses == ["noop", "success"], statuses

    check = connect(db_path)
    ready = check.execute(
        "SELECT COUNT(*) AS c FROM snapshots WHERE status='READY'"
    ).fetchone()
    assert ready["c"] == 1
    check.close()


def test_repository_dedup_under_concurrency(
    tmp_path: Path, tiny_repo: Path
) -> None:
    """Dois indexadores não criam dois repositórios p/ o mesmo handle."""
    db_path = tmp_path / "dedup.db"
    conn0 = connect(db_path)
    migrate(conn0)
    conn0.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker() -> None:
        conn = connect(db_path)
        try:
            with conn:
                Store(conn).get_or_create_repository(str(tiny_repo))
            barrier.wait(timeout=15)
            with conn:
                Store(conn).get_or_create_repository(str(tiny_repo))
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    check = connect(db_path)
    n = check.execute("SELECT COUNT(*) AS c FROM repositories").fetchone()
    assert n["c"] == 1
    check.close()


def test_lost_write_lock_marks_failed_not_crash(
    tmp_path: Path, tiny_repo: Path
) -> None:
    """BEGIN IMMEDIATE negado além do retry → FAILED, nunca exceção nua."""
    db_path = tmp_path / "lock.db"
    holder = connect(db_path)
    migrate(holder)
    # segura o lock de escrita permanentemente durante o teste
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO repositories (root_handle) VALUES ('holder')")

    waiter = connect(db_path)
    waiter.execute("PRAGMA busy_timeout = 50")  # falha rápida no teste
    try:
        out = IndexCoordinator(waiter).index(tiny_repo)
        # lock integralmente preso: nem o job é registrado — outcome
        # failed honesto, exceção nunca vaza para o caller
        assert out.status == "failed"
        assert out.job_id is None
        assert out.error
        # nenhum READY publicado por este caminho
        row = waiter.execute(
            "SELECT COUNT(*) AS c FROM snapshots WHERE status='READY'"
        ).fetchone()
        assert row["c"] == 0
    finally:
        waiter.close()
        holder.rollback()
        holder.close()


def test_acquire_write_lock_retries_then_reports(
    tmp_path: Path, tiny_repo: Path
) -> None:
    """Lock negado em BEGIN IMMEDIATE → erro tipado após retries."""
    db_path = tmp_path / "lock2.db"
    holder = connect(db_path)
    migrate(holder)
    holder.execute("BEGIN IMMEDIATE")

    waiter = connect(db_path)
    waiter.execute("PRAGMA busy_timeout = 10")
    try:
        coord = IndexCoordinator(waiter)
        coord._BEGIN_ATTEMPTS = 3
        coord._BEGIN_BACKOFF_S = 0.01
        err = coord._acquire_write_lock()  # type: ignore[attr-defined]
        assert isinstance(err, sqlite3.OperationalError)
        waiter.rollback()  # aborta qualquer BEGIN parcial
    finally:
        waiter.close()
        holder.rollback()
        holder.close()


def test_begin_lock_denied_fails_with_failed_snapshot(
    tmp_path: Path, tiny_repo: Path
) -> None:
    """Falha em _acquire_write_lock → FAILED + snapshot diagnóstico."""
    db_path = tmp_path / "lock3.db"
    conn = connect(db_path)
    migrate(conn)
    coord = IndexCoordinator(conn)
    coord._acquire_write_lock = lambda: sqlite3.OperationalError(  # type: ignore[method-assign]
        "database is locked")
    out = coord.index(tiny_repo)
    try:
        assert out.status == "failed"
        assert "locked" in out.error
        # snapshot FAILED registrado; job finalizado como failed
        snap = Store(conn).snapshot_by_id(out.snapshot_id)  # type: ignore[arg-type]
        assert snap is not None and str(snap.status).endswith("FAILED")
        job = conn.execute(
            "SELECT status FROM index_jobs WHERE id=?",
            (out.job_id,)).fetchone()
        assert job["status"] == "failed"
    finally:
        conn.close()
