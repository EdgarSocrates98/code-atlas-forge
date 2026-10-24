"""lf-0019 Phase 4: motor incremental — change detector + planner.

Cobre: single-file change, rename por hash, delete, bump de analyzer,
sem-git, serialização do plano, e a propriedade golden: incremental ≡
full reindex canônico.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.discovery import scan
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.change_detector import ChangeKind, detect_changes
from code_atlas.indexing.dependency_planner import (
    IndexPlan,
    InvalidationPlanner,
    PlanAction,
)
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def dep_repo(tmp_path: Path) -> Path:
    r = tmp_path / "dep"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (r / "pkg" / "app.py").write_text(
        "from .util import helper\n\n\ndef main():\n    return helper()\n"
    )
    (r / "pkg" / "leaf.py").write_text(
        "from .app import main\n\n\ndef run():\n    return main()\n"
    )
    (r / "solo.py").write_text("def isolated():\n    return 0\n")
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _index(db: sqlite3.Connection, repo: Path, **kw: object) -> object:
    return IndexCoordinator(db).index(repo, **kw)  # type: ignore[arg-type]


def _canonical(db: sqlite3.Connection, snapshot_id: int) -> dict[str, set]:
    """Nós/arestas canônicos do snapshot — compara incremental x full."""
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


def _plan_actions(plan: IndexPlan) -> dict[str, str]:
    return {f.path: str(f.action) for f in plan.files}


# --- change detector (unidade) -----------------------------------------


def test_detector_added_modified_deleted_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "keep.py").write_text("x = 1\n")
    (repo / "mod.py").write_text("y = 1\n")
    (repo / "gone.py").write_text("z = 1\n")
    s0 = scan(repo)
    inv = [
        {"path": f.path, "language": f.language, "sha256": f.sha256,
         "parse_status": "parsed"}
        for f in s0.files
    ]
    versions = {"scanner": "v1", "parser:python": "p1"}
    (repo / "mod.py").write_text("y = 2\n")
    (repo / "gone.py").unlink()
    (repo / "new.py").write_text("n = 1\n")
    s1 = scan(repo)
    cs = detect_changes(s1, inv, versions, versions)
    kinds = {c.path: str(c.kind) for c in cs.changes}
    assert kinds["new.py"] == "added"
    assert kinds["mod.py"] == "modified"
    assert kinds["gone.py"] == "deleted"
    assert kinds["keep.py"] == "unchanged"


def test_detector_rename_only_on_hash_match(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "old.py").write_text("v = 7\n")
    (repo / "unrelated.py").write_text("w = 1\n")
    s0 = scan(repo)
    inv = [
        {"path": f.path, "language": f.language, "sha256": f.sha256,
         "parse_status": "parsed"}
        for f in s0.files
    ]
    versions: dict[str, str] = {"scanner": "v1"}
    (repo / "old.py").rename(repo / "new.py")
    s1 = scan(repo)
    cs = detect_changes(s1, inv, versions, versions)
    kinds = {c.path: str(c.kind) for c in cs.changes}
    assert kinds["new.py"] == "renamed"
    assert kinds["old.py"] == "deleted"
    renamed = cs.for_path("new.py")
    assert renamed is not None and renamed.old_path == "old.py"
    assert "content-identical" in renamed.reason


def test_detector_analyzer_bump_reclassifies_language(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.ts").write_text("export const y = 1;\n")
    s0 = scan(repo)
    inv = [
        {"path": f.path, "language": f.language, "sha256": f.sha256,
         "parse_status": "parsed"}
        for f in s0.files
    ]
    before = {"scanner": "v1", "parser:python": "p1", "parser:typescript": "t1"}
    after = {**before, "parser:python": "p2"}
    cs = detect_changes(s0, inv, before, after)
    kinds = {c.path: str(c.kind) for c in cs.changes}
    assert kinds["a.py"] == "modified"
    assert "analyzer version changed" in (
        cs.for_path("a.py").reason if cs.for_path("a.py") else ""
    )
    assert kinds["b.ts"] == "unchanged"


# --- planner (unidade) ---------------------------------------------------


def test_planner_dependents_transitive_and_cycle_safe() -> None:
    from code_atlas.indexing.change_detector import ChangeSet, FileChange

    edges = [
        ("pkg/app.py", "pkg/util.py", "", "resolved"),
        ("pkg/leaf.py", "pkg/app.py", "", "resolved"),
    ]
    # mudança em util → app e leaf revalidam (transitivo)
    changeset = ChangeSet(
        changes=(
            FileChange("pkg/util.py", ChangeKind.MODIFIED, "hash differs"),
            FileChange("pkg/app.py", ChangeKind.UNCHANGED, "same"),
            FileChange("pkg/leaf.py", ChangeKind.UNCHANGED, "same"),
        )
    )
    plan = InvalidationPlanner().plan(changeset, edges, {})
    acts = _plan_actions(plan)
    assert acts["pkg/util.py"] == "reparse"
    assert acts["pkg/app.py"] == "revalidate-relations"
    assert acts["pkg/leaf.py"] == "revalidate-relations"  # transitivo


def test_planner_cycle_no_hang() -> None:
    from code_atlas.indexing.change_detector import ChangeSet, FileChange

    edges = [
        ("a.py", "b.py", "", "resolved"),
        ("b.py", "a.py", "", "resolved"),
    ]
    cs = ChangeSet(
        changes=(
            FileChange("a.py", ChangeKind.MODIFIED, "hash"),
            FileChange("b.py", ChangeKind.UNCHANGED, "same"),
        )
    )
    plan = InvalidationPlanner().plan(cs, edges, {})
    acts = _plan_actions(plan)
    assert acts["a.py"] == "reparse"
    assert acts["b.py"] == "revalidate-relations"


def test_plan_json_serializable_and_sorted() -> None:
    from code_atlas.indexing.change_detector import ChangeSet, FileChange

    cs = ChangeSet(
        changes=(
            FileChange("b.py", ChangeKind.UNCHANGED, "same"),
            FileChange("a.py", ChangeKind.MODIFIED, "hash"),
        )
    )
    plan = InvalidationPlanner().plan(cs, [], {})
    payload = json.loads(plan.to_json())
    assert payload["counts"] == {"reparse": 1, "reuse": 1}
    assert [f["path"] for f in payload["files"]] == ["a.py", "b.py"]
    assert payload["files"][0]["reason"]
    assert plan.paths_for(PlanAction.REPARSE) == ("a.py",)


def test_planner_no_prior_graph_reparses_all() -> None:
    from code_atlas.indexing.change_detector import ChangeSet, FileChange

    cs = ChangeSet(
        changes=(
            FileChange("a.py", ChangeKind.ADDED, "new"),
            FileChange("b.py", ChangeKind.ADDED, "new"),
        )
    )
    plan = InvalidationPlanner().plan(cs, [], {})
    assert set(plan.paths_for(PlanAction.REPARSE)) == {"a.py", "b.py"}


# --- integração coordinator ----------------------------------------------


def test_incremental_single_file_change(db, dep_repo: Path) -> None:
    out0 = _index(db, dep_repo)
    assert out0.status == "success"
    # muda só util.py
    (dep_repo / "pkg" / "util.py").write_text(
        "def helper():\n    return 2\n"
    )
    coord = IndexCoordinator(db)
    out = coord.index(dep_repo, incremental=True)
    assert out.status == "success"
    plan = coord.last_plan
    assert plan is not None
    acts = _plan_actions(plan)
    assert acts["pkg/util.py"] == "reparse"
    # importadores direto e transitivo revalidam
    assert acts["pkg/app.py"] == "revalidate-relations"
    assert acts["pkg/leaf.py"] == "revalidate-relations"
    # independente fica reuse
    assert acts["solo.py"] == "reuse"
    assert acts["pkg/__init__.py"] == "reuse"
    # trabalho executado casa com o plano
    assert out.stats["files_parsed"] == 3  # util + app + leaf
    assert out.stats["files_reused"] == 2  # solo + __init__


def test_incremental_equals_full_reindex(db, dep_repo: Path) -> None:
    """Propriedade golden: modificar um arquivo e indexar incremental
    produz o mesmo grafo canônico de um full reindex."""
    _index(db, dep_repo)
    (dep_repo / "pkg" / "util.py").write_text(
        "def helper():\n    return 42\n\n\ndef extra():\n    return 1\n"
    )
    out_inc = IndexCoordinator(db).index(dep_repo, incremental=True)
    assert out_inc.status == "success"
    inc = _canonical(db, out_inc.snapshot_id)
    # full reindex do mesmo conteúdo
    out_full = IndexCoordinator(db).index(dep_repo, force=True)
    assert out_full.status == "success"
    full = _canonical(db, out_full.snapshot_id)
    assert inc == full


def test_incremental_importer_relations_reevaluated(db, dep_repo: Path) -> None:
    _index(db, dep_repo)
    snap0 = Store(db).latest_ready_snapshot(
        Store(db).repository_id_for(str(dep_repo.resolve()))
    )
    # remove helper → import de app quebra
    (dep_repo / "pkg" / "util.py").write_text("def other():\n    return 9\n")
    out = IndexCoordinator(db).index(dep_repo, incremental=True)
    assert out.status == "success"
    assert snap0 is not None
    edges = _canonical(db, out.snapshot_id)["edges"]
    # CALLS main→helper deve ter virado unresolved (não pode ficar stale
    # apontando para símbolo removido — to_text fica em to_unresolved)
    calls = {
        e for e in edges
        if e[0] == "CALLS" and e[5] == "helper" and e[1] == "pkg/app.py"
    }
    assert calls, "expected CALLS edge to helper text"
    assert all(e[6] == "unresolved" for e in calls)


def test_incremental_rename_and_delete(db, tmp_path: Path) -> None:
    repo = tmp_path / "rn"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    (repo / "gone.py").write_text("def g():\n    return 2\n")
    (repo / "use.py").write_text(
        "from gone import g\n\n\ndef u():\n    return g()\n"
    )
    _index(db, repo)
    (repo / "mod.py").rename(repo / "renamed.py")
    (repo / "gone.py").unlink()
    coord = IndexCoordinator(db)
    out = coord.index(repo, incremental=True)
    assert out.status == "success"
    plan = coord.last_plan
    assert plan is not None
    acts = _plan_actions(plan)
    assert acts["gone.py"] == "drop"
    assert acts["renamed.py"] == "reparse"
    assert acts["use.py"] == "revalidate-relations"
    canon = _canonical(db, out.snapshot_id)
    # entidade de gone.py sumiu
    assert not [n for n in canon["nodes"] if "gone" in n[1]]
    # edge do import de use.py ficou honestamente unresolved
    imp = [e for e in canon["edges"] if e[0] == "IMPORTS" and "gone" in e[5]]
    assert imp and all(e[6] in ("unresolved", "external") for e in imp)


def test_incremental_no_prior_falls_back_to_full(db, dep_repo: Path) -> None:
    """Sem snapshot anterior: incremental vira indexação completa."""
    out = _index(db, dep_repo, incremental=True)
    assert out.status == "success"
    canon = _canonical(db, out.snapshot_id)
    assert len(canon["nodes"]) > 0


def test_incremental_no_git_repo(db, tmp_path: Path) -> None:
    repo = tmp_path / "nogit"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "b.py").write_text("from a import f\n\n\ndef g():\n    return f()\n")
    _index(db, repo)
    (repo / "a.py").write_text("def f():\n    return 7\n")
    coord = IndexCoordinator(db)
    out = coord.index(repo, incremental=True)
    assert out.status == "success"
    assert coord.last_plan is not None
    acts = _plan_actions(coord.last_plan)
    assert acts["b.py"] == "revalidate-relations"


def test_plan_counts_match_executed_work(db, dep_repo: Path) -> None:
    _index(db, dep_repo)
    (dep_repo / "solo.py").write_text("def isolated():\n    return 5\n")
    coord = IndexCoordinator(db)
    out = coord.index(dep_repo, incremental=True)
    plan = coord.last_plan
    assert plan is not None
    counts = plan.counts()
    # 1 reparse (solo.py), 4 reuse
    assert counts["reparse"] == 1
    assert counts["reuse"] == 4
    # nenhum revalidate → executado = só o reparse
    assert out.stats["files_parsed"] == 1
    assert out.stats["files_reused"] == 4
