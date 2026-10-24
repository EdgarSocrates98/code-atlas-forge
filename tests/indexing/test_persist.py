"""Integração lf-0011: scan → parse → grafo → snapshot READY persistido.

Cobre: counts reais em symbols/occurrences/relations/evidence, no-op por
digest+versões, versão de extrator derrotando no-op, falha preservando o
READY anterior, determinismo de ids em DBs frescos e estados unresolved
persistidos sem inventar alvos.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.coordinator import ANALYZER_VERSION
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def mixed_repo(tmp_path: Path) -> Path:
    r = tmp_path / "mixed"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "util.py").write_text(
        "class Base: pass\n\ndef helper(): return 1\n"
    )
    (r / "pkg" / "core.py").write_text(
        "from pkg.util import helper, Base\n\n"
        "class Engine(Base):\n"
        "    def run(self): return helper()\n"
    )
    (r / "app.ts").write_text(
        "export interface Svc { ping(): void }\n"
        "export class Api implements Svc { ping() {} }\n"
    )
    (r / "broken.py").write_text("def x(:\n")
    (r / "data.txt").write_text("not code\n")
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _rows(db: sqlite3.Connection, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return db.execute(sql, args).fetchall()


def test_index_persists_graph(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    out = IndexCoordinator(db).index(mixed_repo)
    assert out.status == "success", out.error
    assert out.snapshot_id is not None
    sid = out.snapshot_id

    qnames = {r["qualified_name"] for r in _rows(
        db, "SELECT qualified_name FROM symbols")}
    assert "pkg.util.helper" in qnames
    assert "pkg.core.Engine" in qnames
    assert "pkg.core.Engine.run" in qnames
    assert any("Api" in q for q in qnames)

    occs = _rows(db,
        "SELECT COUNT(*) AS c FROM symbol_occurrences WHERE snapshot_id=?",
        (sid,))[0]
    assert occs["c"] > 0

    rels = _rows(db,
        "SELECT type, resolution_status, to_unresolved FROM relations "
        "WHERE snapshot_id=?", (sid,))
    types = {r["type"] for r in rels}
    assert {"DEFINES", "IMPORTS", "EXTENDS"} <= types
    # EXTENDS resolvido aponta para ocorrência no mesmo snapshot
    ext = _rows(db,
        "SELECT r.to_id, o.snapshot_id FROM relations r "
        "JOIN symbol_occurrences o ON o.id = r.to_id "
        "WHERE r.type='EXTENDS' AND r.snapshot_id=?", (sid,))
    assert ext and all(r["snapshot_id"] == sid for r in ext)
    # `import`-resolvable ⇒ to_id NOT NULL; external text preserved
    unresolved = [r for r in rels if r["resolution_status"] != "resolved"]
    for r in unresolved:
        assert r["to_unresolved"]  # texto preservado, nada inventado

    ev = _rows(db, "SELECT COUNT(*) AS c, extractor_version FROM evidence "
                   "WHERE snapshot_id=? GROUP BY extractor_version", (sid,))
    assert ev and all(r["extractor_version"] for r in ev)

    job = db.execute(
        "SELECT counts, failures FROM index_jobs WHERE snapshot_id=?",
        (sid,)).fetchone()
    counts = json.loads(job["counts"])
    assert counts["files_parsed"] >= 3
    assert counts["files_with_errors"] >= 1  # broken.py → syntax-errors
    assert "broken.py" in job["failures"]


def test_broken_file_occurrence_status(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    out = IndexCoordinator(db).index(mixed_repo)
    assert out.status == "success"
    rows = _rows(db,
        "SELECT o.parse_status FROM symbol_occurrences o "
        "JOIN source_files f ON f.id = o.source_file_id "
        "WHERE o.snapshot_id=? AND f.relative_path='broken.py'",
        (out.snapshot_id,))
    # módulo broken.py vira ocorrência com status explícito
    assert rows and all(r["parse_status"] == "parsed-with-errors"
                        for r in rows)
    ev = _rows(db,
        "SELECT notes FROM evidence WHERE snapshot_id=? "
        "AND path='broken.py'", (out.snapshot_id,))
    assert any("parse-failure:syntax-errors" in (r["notes"] or "")
               for r in ev)


def test_noop_does_not_reparse(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    coord = IndexCoordinator(db)
    coord.index(mixed_repo)
    calls = {"n": 0}
    orig = coord._engine.parse  # type: ignore[attr-defined]

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    coord._engine.parse = counting  # type: ignore[method-assign]
    second = coord.index(mixed_repo)
    assert second.status == "noop"
    assert calls["n"] == 0  # fast-path: nenhum parse no noop
    snaps = db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()
    assert snaps["c"] == 1


def test_extractor_version_change_defeats_noop(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    coord = IndexCoordinator(db)
    coord.index(mixed_repo)
    first = coord.index(mixed_repo)  # segundo run sem mudança → noop
    orig = coord._analyzer_versions

    def bumped(scan):
        v = orig(scan)
        v["parser:python"] = "tree-sitter-python/9.9.9+0.0.0"
        return v

    coord._analyzer_versions = bumped  # type: ignore[method-assign]
    second = coord.index(mixed_repo)
    assert second.status == "success"  # nova versão → reindexa
    assert second.snapshot_id != first.snapshot_id


def test_failure_mid_write_preserves_ready(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    coord = IndexCoordinator(db)
    first = coord.index(mixed_repo)
    assert first.status == "success"

    store = Store(db)
    store.insert_relations = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        sqlite3.IntegrityError("boom mid-write")
    )
    coord2 = IndexCoordinator(db)
    coord2._store = store  # type: ignore[assignment]
    (mixed_repo / "new.py").write_text("z = 1\n")
    out = coord2.index(mixed_repo)
    assert out.status == "failed"

    ready = store.latest_ready_snapshot(
        db.execute("SELECT repository_id FROM snapshots WHERE id=?",
                   (first.snapshot_id,)).fetchone()["repository_id"])
    assert ready is not None and ready.id == first.snapshot_id
    assert ready.status is SnapshotStatus.READY
    # nada parcial do snapshot abortado persistiu
    leftover = _rows(db,
        "SELECT COUNT(*) AS c FROM relations r JOIN snapshots s "
        "ON s.id=r.snapshot_id WHERE s.status='FAILED'")
    assert leftover[0]["c"] == 0


def test_deterministic_ids_across_fresh_dbs(
    tmp_path: Path, mixed_repo: Path
) -> None:
    def snapshot_rows(db_path: Path) -> tuple:
        conn = connect(db_path)
        migrate(conn)
        out = IndexCoordinator(conn).index(mixed_repo)
        assert out.status == "success"
        syms = conn.execute(
            "SELECT language, qualified_name, kind FROM symbols "
            "ORDER BY id").fetchall()
        rels = conn.execute(
            "SELECT from_id, to_id, to_unresolved, type, "
            "resolution_status FROM relations ORDER BY id").fetchall()
        occ = conn.execute(
            "SELECT logical_symbol_id, source_file_id, span_start, "
            "span_end, parse_status FROM symbol_occurrences ORDER BY id"
        ).fetchall()
        conn.close()
        return tuple(map(tuple, syms)), tuple(map(tuple, rels)), \
            tuple(map(tuple, occ))

    a = snapshot_rows(tmp_path / "a.db")
    b = snapshot_rows(tmp_path / "b.db")
    assert a == b  # ids, counts e spans idênticos entre bancos frescos


def test_graph_versions_recorded_in_snapshot(
    db: sqlite3.Connection, mixed_repo: Path
) -> None:
    out = IndexCoordinator(db).index(mixed_repo)
    snap = Store(db).snapshot_by_id(out.snapshot_id)  # type: ignore[arg-type]
    assert snap is not None
    av = dict(snap.analyzer_versions)
    assert av.get("parser:python", "").startswith("tree-sitter")
    assert "+" in av["parser:python"]  # "parser+grammar"
    assert av.get("scanner", "").endswith(ANALYZER_VERSION)
