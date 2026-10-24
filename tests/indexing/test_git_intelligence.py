"""lf-0021 Phase 6: git-aware change intelligence — golden tests.

Cobre com repos git REAIS (via ``gitbin.run_git`` — único site de
subprocess): identidade lógica vs path, snapshot→revisão, rename por
hash (git mv e filesystem), dirty worktree, branch switch, detached
HEAD, worktrees (.git file), missing .git, case-rename, ignorados após
branch switch, realocação, e reexport/delete de dependência.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from code_atlas.discovery.identity import identity_key
from code_atlas.discovery.scanner import probe_git, scan
from code_atlas.gitbin import git_binary, run_git
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.change_detector import detect_changes
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store

pytestmark = pytest.mark.skipif(
    git_binary() is None, reason="git binary absent"
)

_GIT_CFG = ["-c", "user.email=atlas@test", "-c", "user.name=atlas"]


def _git(repo: Path, *args: str) -> str:
    """``git -C repo -c user... args`` — helper único dos testes."""
    r = run_git([*_GIT_CFG, *args], cwd=repo)
    assert r.returncode == 0, r.stderr.decode()
    return r.stdout.decode().strip()


def _commit_all(repo: Path, msg: str = "c") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "app.py").write_text("def main():\n    return 1\n")
    (r / "util.py").write_text("def helper():\n    return 2\n")
    _git(r, "init", "-q", "-b", "main")
    _commit_all(r, "init")
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _index(db: sqlite3.Connection, repo: Path, **kw: object) -> object:
    return IndexCoordinator(db).index(repo, **kw)  # type: ignore[arg-type]


def _snapshot(db: sqlite3.Connection, sid: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
    assert row is not None
    return row


def _inv(db: sqlite3.Connection, snap_id: int) -> list[dict[str, object]]:
    return Store(db).snapshot_inventory(snap_id)


def _inv_paths(db: sqlite3.Connection, snap_id: int) -> set[str]:
    # `graphify-out/` é artefato de um watcher externo deste ambiente —
    # não faz parte do repo sob teste; filtrado para asserções estáveis.
    return {
        str(r["path"])
        for r in Store(db).snapshot_inventory(snap_id)
        if not str(r["path"]).startswith("graphify-out/")
    }


# --- probe / identidade -------------------------------------------------


def test_probe_captures_remote_and_root_commit(git_repo: Path) -> None:
    g = probe_git(git_repo)
    assert g.is_repo and g.branch == "main"
    assert g.root_commit == _git(git_repo, "rev-list", "--max-parents=0", "HEAD")
    assert g.remote_url == ""
    _git(git_repo, "remote", "add", "origin", "https://x.test/r.git")
    g2 = probe_git(git_repo)
    assert g2.remote_url == "https://x.test/r.git"


def test_identity_key_priority() -> None:
    from code_atlas.discovery.models import GitInfo

    root = Path("/tmp/whatever")
    g = GitInfo(available=True, is_repo=True,
                remote_url="https://x/r.git", root_commit="abc")
    assert identity_key(g, root) == "git-remote:https://x/r.git"
    g2 = GitInfo(available=True, is_repo=True, root_commit="abc")
    assert identity_key(g2, root) == "git-root:abc"
    g3 = GitInfo(available=False, is_repo=False)
    assert identity_key(g3, root).startswith("path:")


def test_relocation_preserves_identity(
    db: sqlite3.Connection, git_repo: Path, tmp_path: Path
) -> None:
    """Mover o diretório físico mantém o mesmo repository_id."""
    o1 = _index(db, git_repo)
    assert o1.status == "success"  # type: ignore[attr-defined]
    rid1 = db.execute(
        "SELECT repository_id FROM snapshots WHERE id = ?",
        (o1.snapshot_id,),  # type: ignore[attr-defined]
    ).fetchone()["repository_id"]
    moved = tmp_path / "elsewhere" / "repo"
    moved.parent.mkdir()
    git_repo.rename(moved)
    o2 = _index(db, moved)
    rid2 = db.execute(
        "SELECT repository_id FROM snapshots WHERE id = ?",
        (o2.snapshot_id,),  # type: ignore[attr-defined]
    ).fetchone()["repository_id"]
    assert rid2 == rid1
    # root_handle atualizado para diagnóstico
    rh = db.execute(
        "SELECT root_handle FROM repositories WHERE id = ?", (rid1,)
    ).fetchone()["root_handle"]
    assert rh == str(moved.resolve())


def test_two_clones_share_identity(db: sqlite3.Connection, git_repo: Path, tmp_path: Path) -> None:
    """Dois checkouts do mesmo remote vivem sob o mesmo repository_id."""
    _git(git_repo, "remote", "add", "origin", "https://x.test/shared.git")
    _index(db, git_repo)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(git_repo), str(clone))
    _git(clone, "remote", "set-url", "origin", "https://x.test/shared.git")
    o2 = _index(db, clone)
    assert o2.status in ("success", "noop")  # type: ignore[attr-defined]
    ids = {
        r["repository_id"]
        for r in db.execute("SELECT repository_id FROM snapshots")
    }
    assert len(ids) == 1


# --- snapshot → revisão ---------------------------------------------------


def test_dirty_worktree_marked_not_clean(db: sqlite3.Connection, git_repo: Path) -> None:
    o1 = _index(db, git_repo)
    snap1 = _snapshot(db, o1.snapshot_id)  # type: ignore[attr-defined]
    assert snap1["dirty"] == 0
    (git_repo / "app.py").write_text("def main():\n    return 99\n")
    o2 = _index(db, git_repo)
    snap2 = _snapshot(db, o2.snapshot_id)  # type: ignore[attr-defined]
    # dirty: commit_sha ainda é HEAD limpo mas dirty=1 marca divergência
    assert snap2["dirty"] == 1
    assert snap2["commit_sha"] == snap1["commit_sha"]
    assert snap2["worktree_digest"] != snap1["worktree_digest"]


def test_branch_switch_binds_snapshot(db: sqlite3.Connection, git_repo: Path) -> None:
    _index(db, git_repo)
    _git(git_repo, "checkout", "-qb", "feature")
    (git_repo / "feat.py").write_text("def feat():\n    return 7\n")
    _commit_all(git_repo)
    o2 = _index(db, git_repo)
    snap2 = _snapshot(db, o2.snapshot_id)  # type: ignore[attr-defined]
    assert snap2["git_ref"] == "feature"
    assert "feat.py" in _inv_paths(db, o2.snapshot_id)  # type: ignore[attr-defined]
    # volta pra main: snapshot 3 não vê feat.py (revision isolada)
    _git(git_repo, "checkout", "-q", "main")
    o3 = _index(db, git_repo)
    assert "feat.py" not in _inv_paths(db, o3.snapshot_id)  # type: ignore[attr-defined]


def test_detached_head_no_crash(db: sqlite3.Connection, git_repo: Path) -> None:
    sha = _git(git_repo, "rev-parse", "HEAD")
    _git(git_repo, "checkout", "-q", sha)
    g = probe_git(git_repo)
    assert g.is_repo and g.branch == "" and g.head_sha == sha
    o = _index(db, git_repo)
    assert o.status == "success"  # type: ignore[attr-defined]


def test_missing_dot_git_is_path_identity(db: sqlite3.Connection, tmp_path: Path) -> None:
    r = tmp_path / "nogit"
    r.mkdir()
    (r / "a.py").write_text("x = 1\n")
    g = probe_git(r)
    assert not g.is_repo
    o = _index(db, r)
    assert o.status == "success"  # type: ignore[attr-defined]
    row = db.execute("SELECT identity_key FROM repositories").fetchone()
    assert row["identity_key"].startswith("path:")


def test_git_worktree_dotgit_file(db: sqlite3.Connection, git_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(git_repo, "worktree", "add", "-q", str(wt))
    assert (wt / ".git").is_file()  # worktree: .git é arquivo
    g = probe_git(wt)
    assert g.is_repo and g.is_worktree
    o = _index(db, wt)
    assert o.status in ("success", "noop")  # type: ignore[attr-defined]


# --- renames / deletes ---------------------------------------------------


def test_git_mv_rename_by_hash(db: sqlite3.Connection, git_repo: Path) -> None:
    o1 = _index(db, git_repo)
    inv = _inv(db, o1.snapshot_id)  # type: ignore[attr-defined]
    _git(git_repo, "mv", "util.py", "helpers.py")
    s1 = scan(git_repo)
    cs = detect_changes(s1, inv, {}, {})
    renamed = cs.for_path("helpers.py")
    assert renamed is not None and str(renamed.kind) == "renamed"
    assert renamed.old_path == "util.py"


def test_fs_only_rename_by_hash(db: sqlite3.Connection, git_repo: Path) -> None:
    o1 = _index(db, git_repo)
    inv = _inv(db, o1.snapshot_id)  # type: ignore[attr-defined]
    (git_repo / "util.py").rename(git_repo / "helpers.py")
    s1 = scan(git_repo)
    cs = detect_changes(s1, inv, {}, {})
    renamed = cs.for_path("helpers.py")
    assert renamed is not None and renamed.old_path == "util.py"


def test_delete_detected_and_dependency_invalidated(
    db: sqlite3.Connection, git_repo: Path
) -> None:
    (git_repo / "app.py").write_text(
        "from util import helper\n\n\ndef main():\n    return helper()\n"
    )
    o1 = _index(db, git_repo)
    inv = _inv(db, o1.snapshot_id)  # type: ignore[attr-defined]
    (git_repo / "util.py").unlink()
    s1 = scan(git_repo)
    cs = detect_changes(s1, inv, {}, {})
    kinds = {c.path: str(c.kind) for c in cs.changes}
    assert kinds["util.py"] == "deleted"
    o2 = _index(db, git_repo, incremental=True)
    # importador revalidado: CALLS main→helper cai para unresolved
    rels = db.execute(
        "SELECT s2.qualified_name AS toq, e.to_unresolved"
        "  FROM relations e"
        "  LEFT JOIN symbols s2 ON s2.id = e.to_id"
        " WHERE e.snapshot_id = ? AND e.type = 'CALLS'",
        (o2.snapshot_id,),  # type: ignore[attr-defined]
    ).fetchall()
    assert any(r["to_unresolved"] and "helper" in r["to_unresolved"] for r in rels)


def test_reexport_invalidation(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Remover símbolo de barrel reexportado revalida importadores."""
    r = tmp_path / "rr"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text(
        "from .impl import thing\n"
    )
    (r / "pkg" / "impl.py").write_text("def thing():\n    return 1\n")
    (r / "use.py").write_text(
        "from pkg import thing\n\n\ndef go():\n    return thing()\n"
    )
    o1 = _index(db, r)
    assert o1.status == "success"  # type: ignore[attr-defined]
    (r / "pkg" / "__init__.py").write_text("")
    o2 = _index(db, r, incremental=True)
    assert o2.status == "success"  # type: ignore[attr-defined]
    rels = db.execute(
        "SELECT e.to_unresolved FROM relations e"
        " WHERE e.snapshot_id = ? AND e.type = 'CALLS'",
        (o2.snapshot_id,),  # type: ignore[attr-defined]
    ).fetchall()
    assert any("thing" in (x["to_unresolved"] or "") for x in rels)


@pytest.mark.skipif(sys.platform == "win32", reason="case-rename é POSIX")
def test_case_only_rename(db: sqlite3.Connection, git_repo: Path) -> None:
    """Rename só de caixa: hash idêntico → RENAMED, não delete+add."""
    o1 = _index(db, git_repo)
    inv = _inv(db, o1.snapshot_id)  # type: ignore[attr-defined]
    os.rename(git_repo / "util.py", git_repo / "Util.py")
    s1 = scan(git_repo)
    paths = {f.path for f in s1.files}
    if "Util.py" not in paths:
        pytest.skip("filesystem case-insensitive — rename é no-op")
    cs = detect_changes(s1, inv, {}, {})
    renamed = cs.for_path("Util.py")
    assert renamed is not None and renamed.old_path == "util.py"


def test_ignored_files_after_branch_switch(
    db: sqlite3.Connection, git_repo: Path
) -> None:
    """Arquivo ignorado permanece fora do inventário após branch switch."""
    _git(git_repo, "checkout", "-qb", "feat")
    (git_repo / ".gitignore").write_text("ignored.py\n")
    (git_repo / "ignored.py").write_text("secret = 1\n")
    _commit_all(git_repo)
    o = _index(db, git_repo)
    paths = _inv_paths(db, o.snapshot_id)  # type: ignore[attr-defined]
    assert "ignored.py" not in paths
    assert ".gitignore" in paths  # o próprio ignore é rastreado
    # volta pra main: `.gitignore` sai → `ignored.py` vira untracked
    # não-ignorado e aparece. Ignorado é propriedade da REVISÃO, não do
    # path — a correção é cada snapshot refletir seu próprio ruleset.
    _git(git_repo, "checkout", "-q", "main")
    o2 = _index(db, git_repo)
    paths2 = _inv_paths(db, o2.snapshot_id)  # type: ignore[attr-defined]
    assert ".gitignore" not in paths2
    assert "ignored.py" in paths2
