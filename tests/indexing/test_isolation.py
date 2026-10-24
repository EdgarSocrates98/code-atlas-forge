"""lf-0033: isolamento cross-repo/snapshot, corrupção, consistência.

Cobre: contaminação entre repositórios com conteúdo idêntico,
invisibilidade de snapshots STALE às consultas, detecção de banco
corrompido, overflow de inteiros no decoder SCIP, e a equivalência
cold x incremental x incremental+cache x rebuild.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "golden"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resolvers"))

from eval_harness import CORPUS_DIR
from scip_fixture import f_string, f_varint, index_bytes, tool_info

from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _canonical(db: sqlite3.Connection, snapshot_id: int) -> dict[str, set]:
    occs = db.execute(
        """
        SELECT s.kind, s.qualified_name, s.language, sf.relative_path,
               o.parse_status, o.content_hash
          FROM symbol_occurrences o
          JOIN symbols s ON s.id = o.logical_symbol_id
          JOIN source_files sf ON sf.id = o.source_file_id
         WHERE o.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    nodes = {
        (r["kind"], r["qualified_name"], r["language"],
         r["relative_path"], r["parse_status"], r["content_hash"])
        for r in occs
    }
    rels = db.execute(
        """
        SELECT r.type, r.resolution_status, r.confidence_class,
               sf_from.relative_path AS fp,
               s_from.qualified_name AS fq,
               sf_to.relative_path AS tp,
               s_to.qualified_name AS tq,
               r.to_unresolved
          FROM relations r
          JOIN symbol_occurrences o_from ON o_from.id = r.from_id
          JOIN source_files sf_from ON sf_from.id = o_from.source_file_id
          JOIN symbols s_from ON s_from.id = o_from.logical_symbol_id
          LEFT JOIN symbol_occurrences o_to ON o_to.id = r.to_id
          LEFT JOIN source_files sf_to ON sf_to.id = o_to.source_file_id
          LEFT JOIN symbols s_to ON s_to.id = o_to.logical_symbol_id
         WHERE r.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    edges = {
        (r["type"], r["fp"], r["fq"], r["tp"], r["tq"],
         r["to_unresolved"] or "", r["resolution_status"],
         r["confidence_class"])
        for r in rels
    }
    return {"nodes": nodes, "edges": edges}


def _dep_repo(root: Path) -> Path:
    r = root / "dep"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (r / "pkg" / "app.py").write_text(
        "from .util import helper\n\n\ndef main():\n    return helper()\n"
    )
    return r


# --- cross-repo / cross-snapshot contamination -------------------------


def test_identical_repos_no_cross_contamination(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Duas cópias do MESMO corpus no mesmo banco: qnames colidem mas
    nenhuma relação cruza snapshots/repositórios."""
    a = tmp_path / "repo_a"
    b = tmp_path / "repo_b"
    shutil.copytree(CORPUS_DIR / "py_advanced", a)
    shutil.copytree(CORPUS_DIR / "py_advanced", b)
    store = Store(db)
    out_a = IndexCoordinator(db).index(a)
    out_b = IndexCoordinator(db).index(b)
    assert out_a.snapshot_id != out_b.snapshot_id  # type: ignore[attr-defined]
    for sid in (out_a.snapshot_id, out_b.snapshot_id):  # type: ignore[attr-defined]
        assert store.cross_snapshot_relations(int(sid)) == []
    ca, cb = _canonical(db, int(out_a.snapshot_id)), _canonical(  # type: ignore[attr-defined]
        db, int(out_b.snapshot_id)  # type: ignore[attr-defined]
    )
    assert ca == cb


def test_relations_scoped_to_own_snapshot(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Conteúdo distinto: toda aresta tem endpoints no próprio snapshot."""
    a = _dep_repo(tmp_path / "one")
    a.parent.mkdir(parents=True, exist_ok=True)
    b = tmp_path / "two"
    shutil.copytree(CORPUS_DIR / "ts_advanced", b)
    store = Store(db)
    out_a = IndexCoordinator(db).index(a)
    out_b = IndexCoordinator(db).index(b)
    for sid in (out_a.snapshot_id, out_b.snapshot_id):  # type: ignore[attr-defined]
        assert store.cross_snapshot_relations(int(sid)) == []
    na = db.execute(
        "SELECT COUNT(*) c FROM relations WHERE snapshot_id=?",
        (int(out_a.snapshot_id),),  # type: ignore[attr-defined]
    ).fetchone()["c"]
    nb = db.execute(
        "SELECT COUNT(*) c FROM relations WHERE snapshot_id=?",
        (int(out_b.snapshot_id),),  # type: ignore[attr-defined]
    ).fetchone()["c"]
    assert na > 0 and nb > 0 and na != nb


def test_stale_snapshot_invisible_to_queries(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Após reindex incremental, o snapshot antigo vira STALE e as
    consultas (latest_ready) veem só o novo READY."""
    repo = _dep_repo(tmp_path)
    out1 = IndexCoordinator(db).index(repo)
    sid1 = int(out1.snapshot_id)  # type: ignore[attr-defined]
    (repo / "pkg" / "util.py").write_text(
        "def helper():\n    return 2\n"
    )
    out2 = IndexCoordinator(db).index(repo)
    sid2 = int(out2.snapshot_id)  # type: ignore[attr-defined]
    assert sid2 != sid1
    status = {
        r["id"]: r["status"]
        for r in db.execute("SELECT id, status FROM snapshots").fetchall()
    }
    assert status[sid1] == "STALE" and status[sid2] == "READY"
    # consulta direta: somente o READY corrente é exposto
    store = Store(db)
    repo_row = db.execute(
        "SELECT id FROM repositories LIMIT 1"
    ).fetchone()
    assert int(store.latest_ready_snapshot(int(repo_row["id"])).id) == sid2  # type: ignore[attr-defined]


# --- banco corrompido: erro tipado, não falha silenciosa ----------------


def test_corrupt_db_file_detected(tmp_path: Path) -> None:
    p = tmp_path / "bad.db"
    p.write_bytes(b"garbage-not-sqlite" * 16)
    with pytest.raises(sqlite3.DatabaseError):
        conn = connect(p)
        migrate(conn)


# --- decoder SCIP: overflow de inteiro ---------------------------------


def test_scip_varint_overflow_rejected(tmp_path: Path) -> None:
    """Varint de >10 bytes (integer overflow) é rejeitado pelo reader."""
    from code_atlas.resolvers.scip.decoder import DecodeError, Reader

    r = Reader(b"\xff" * 11 + b"\x00")
    with pytest.raises(DecodeError):
        r.varint()


def test_scip_huge_field_values_bounded(tmp_path: Path) -> None:
    """Campos com valores extremos (2^63) não quebram o loader."""
    from code_atlas.resolvers.scip.loader import load_index

    big_doc = f_string(1, "a.py") + f_string(4, "python") + f_varint(6, 2**63 - 1)
    blob = index_bytes("file:///x", [big_doc], tool=tool_info())
    p = tmp_path / "big.scip"
    p.write_bytes(blob)
    out = load_index(p)
    assert out.index is not None or out.errors  # nunca crash


# --- consistência: cold x incremental x +cache x rebuild ---------------


def test_consistency_cold_incremental_cache_rebuild(
    tmp_path: Path,
) -> None:
    """O mesmo conteúdo indexado por quatro caminhos produz snapshots
    canonicamente idênticos (nós e arestas)."""
    repo = _dep_repo(tmp_path / "work")
    repo.parent.mkdir(parents=True, exist_ok=True)

    # (a) incremental+cache: indexa original, muta, reindexa no mesmo DB
    db_a = connect(tmp_path / "a.db")
    migrate(db_a)
    IndexCoordinator(db_a).index(repo)
    target = repo / "pkg" / "util.py"
    target.write_text("def helper():\n    return 2\n")
    out_inc = IndexCoordinator(db_a).index(repo)
    assert out_inc.status == "success"  # type: ignore[attr-defined]
    canon = _canonical(db_a, int(out_inc.snapshot_id))  # type: ignore[attr-defined]
    db_a.close()

    # (b) cold full do repo mutado em banco novo
    db_b = connect(tmp_path / "b.db")
    migrate(db_b)
    out_cold = IndexCoordinator(db_b).index(repo)
    assert _canonical(db_b, int(out_cold.snapshot_id)) == canon  # type: ignore[attr-defined]
    db_b.close()

    # (c) rebuild após invalidação total: novo DB, indexa, reindexa noop,
    #     depois muta de novo e indexa — cadeia de dois incrementais
    db_c = connect(tmp_path / "c.db")
    migrate(db_c)
    IndexCoordinator(db_c).index(repo)
    (repo / "pkg" / "app.py").write_text(
        "from .util import helper\n\n\ndef main():\n    return helper() + 1\n"
    )
    out_inc2 = IndexCoordinator(db_c).index(repo)
    canon2 = _canonical(db_c, int(out_inc2.snapshot_id))  # type: ignore[attr-defined]
    db_c.close()

    # (d) cold do estado final
    db_d = connect(tmp_path / "d.db")
    migrate(db_d)
    out_final = IndexCoordinator(db_d).index(repo)
    assert _canonical(db_d, int(out_final.snapshot_id)) == canon2  # type: ignore[attr-defined]
    db_d.close()
