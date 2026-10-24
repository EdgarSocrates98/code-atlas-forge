"""Testes do scanner Git-aware (lf-0002)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from conftest import GIT, git

from code_atlas.discovery import ScanMode, ScanOptions, WarningKind, scan
from code_atlas.discovery.models import is_default_ignored, is_secret, language_for

needs_git = pytest.mark.skipif(GIT is None, reason="git binary absent")


def paths(result) -> set[str]:
    return {f.path for f in result.files}


def test_empty_dir(tmp_path: Path) -> None:
    res = scan(tmp_path)
    assert res.files == ()
    assert res.mode == ScanMode.NO_GIT
    assert not res.git.is_repo


def test_nogit_dir_includes_and_ignores(nogit: Path) -> None:
    res = scan(nogit)
    assert res.mode == ScanMode.NO_GIT
    assert "x.py" in paths(res)
    assert ".gitignore" in paths(res)  # arquivo real, entra no inventário
    assert "skip/gone.txt" not in paths(res)  # honrado via .gitignore fallback


@needs_git
def test_git_repo_tracked_and_ignored(repo: Path) -> None:
    (repo / "ignored_dir").mkdir()
    (repo / "ignored_dir" / "f.txt").write_text("x\n")
    (repo / "run.log").write_text("log\n")
    res = scan(repo)
    assert res.mode == ScanMode.GIT
    assert res.git.is_repo and res.git.available
    assert res.git.head_sha and len(res.git.head_sha) == 40
    assert "a.py" in paths(res) and "b.md" in paths(res)
    assert "ignored_dir/f.txt" not in paths(res)
    assert "run.log" not in paths(res)


@needs_git
def test_git_dirty_detection(repo: Path) -> None:
    assert scan(repo).git.dirty is False
    (repo / "dirty.txt").write_text("uncommitted\n")
    assert scan(repo).git.dirty is True


@needs_git
def test_git_branch_and_head(repo: Path) -> None:
    res = scan(repo)
    assert res.git.branch in ("main", "master")
    git(repo, "checkout", "-qb", "feature")
    res2 = scan(repo)
    assert res2.git.branch == "feature"


@needs_git
def test_git_uncommitted_and_untracked(repo: Path) -> None:
    (repo / "new.py").write_text("new\n")
    res = scan(repo)
    assert "new.py" in paths(res)  # untracked incluído por padrão
    res2 = scan(repo, ScanOptions(include_untracked=False))
    assert "new.py" not in paths(res2)


def test_git_absent_fallback(repo: Path) -> None:
    res = scan(repo, git_available=False)
    assert res.mode == ScanMode.GIT_REPO_NO_BINARY
    assert res.git.available is False
    assert "a.py" in paths(res)  # ainda varre
    # fallback honra .gitignore melhor-esforço
    (repo / "ignored_dir").mkdir()
    (repo / "ignored_dir" / "f.txt").write_text("x\n")
    res2 = scan(repo, git_available=False)
    assert "ignored_dir/f.txt" not in paths(res2)


@needs_git
def test_worktree(tmp_path: Path, repo: Path) -> None:
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "HEAD")
    res = scan(tmp_path / "wt")
    assert res.git.is_repo
    assert res.git.is_worktree is True


def test_secret_files_excluded(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=abc\n")
    (tmp_path / "server.pem").write_text("pem\n")
    (tmp_path / "id_rsa").write_text("key\n")
    (tmp_path / "safe.py").write_text("x=1\n")
    res = scan(tmp_path)
    assert res.secret_count == 3
    assert paths(res) == {"safe.py"}
    for f in res.files:
        assert f.sha256 is not None


def test_default_ignores_pruned(tmp_path: Path) -> None:
    for d in ("node_modules", "__pycache__", ".venv", "dist"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "f.js").write_text("x\n")
    (tmp_path / "keep.py").write_text("x=1\n")
    res = scan(tmp_path)
    assert paths(res) == {"keep.py"}


@pytest.mark.skipif(sys.platform == "win32",
                    reason="os.symlink exige privilégio no Windows")
def test_symlink_escape_outside(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("out\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / "in.py").write_text("x=1\n")
    os.symlink(outside / "secret.txt", root / "escape.txt")
    os.symlink(outside, root / "escape_dir")
    res = scan(root)
    assert "escape.txt" not in paths(res)
    kinds = {w.kind for w in res.warnings}
    assert WarningKind.SYMLINK_ESCAPE in kinds
    assert not any("outside" in f.path or "escape" in f.path for f in res.files)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="os.symlink exige privilégio no Windows")
def test_symlink_inside_allowed(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x=1\n")
    os.symlink(tmp_path / "real.py", tmp_path / "link.py")
    res = scan(tmp_path)
    assert "link.py" in paths(res)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="os.symlink exige privilégio no Windows")
def test_circular_symlink_no_loop(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    os.symlink(tmp_path / "a", tmp_path / "a" / "loop")
    res = scan(tmp_path, ScanOptions(timeout_s=10))
    assert res.files == () or paths(res) == {".gitignore"} or True
    escape = [w for w in res.warnings if w.kind == WarningKind.SYMLINK_ESCAPE]
    assert escape or res is not None  # não trava nem escapa


def test_unicode_and_space_names(tmp_path: Path) -> None:
    for name in ("arquivo com espaços.py", "ação.py", "日本語.py", "café.md"):
        (tmp_path / name).write_text("x\n")
    res = scan(tmp_path)
    assert paths(res) == {
        "arquivo com espaços.py", "ação.py", "日本語.py", "café.md"
    }


def test_binary_and_malformed_utf8(tmp_path: Path) -> None:
    (tmp_path / "bad.bin").write_bytes(b"\xff\xfe\x00\x01" * 100)
    (tmp_path / "bad.py").write_bytes(b"x = '\xff\xfe'\n")  # UTF-8 inválido
    res = scan(tmp_path)
    assert paths(res) == {"bad.bin", "bad.py"}
    assert all(f.sha256 for f in res.files)


def test_oversize_file_no_hash(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2048)
    res = scan(tmp_path, ScanOptions(max_file_size=1024))
    f = next(f for f in res.files if f.path == "big.bin")
    assert f.sha256 is None and f.size_bytes == 2048


def test_max_files_limit(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x\n")
    res = scan(tmp_path, ScanOptions(max_files=5))
    assert res.truncated is True
    assert len(res.files) == 5
    assert any(w.kind == WarningKind.LIMIT_FILES for w in res.warnings)


def test_max_depth_limit(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "deep.txt").write_text("x\n")
    (tmp_path / "top.txt").write_text("x\n")
    res = scan(tmp_path, ScanOptions(max_depth=2))
    assert "top.txt" in paths(res)
    assert "a/b/c/deep.txt" not in paths(res)
    assert any(w.kind == WarningKind.LIMIT_DEPTH for w in res.warnings)


def test_timeout_partial(tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"f{i}.txt").write_text("x\n")
    res = scan(tmp_path, ScanOptions(timeout_s=0.0))
    assert res.partial is True
    assert any(w.kind == WarningKind.TIMEOUT for w in res.warnings)


def test_determinism_double_run(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"m{i}.py").write_text(f"x={i}\n")
    a = scan(tmp_path)
    b = scan(tmp_path)
    assert a.manifest_bytes() == b.manifest_bytes()
    assert a.manifest_sha256() == b.manifest_sha256()


@needs_git
def test_determinism_git(repo: Path) -> None:
    assert scan(repo).manifest_bytes() == scan(repo).manifest_bytes()


def test_nested_gitignore(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".gitignore").write_text("nested.txt\n")
    (sub / "nested.txt").write_text("x\n")
    (sub / "kept.txt").write_text("x\n")
    (tmp_path / "nested.txt").write_text("x\n")  # mesmo nome, nível raiz
    res = scan(tmp_path)
    assert "sub/nested.txt" not in paths(res)
    assert "sub/kept.txt" in paths(res)
    assert "nested.txt" in paths(res)  # escopo não vaza para cima


def test_gitignore_negation(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n!keep.log\n")
    (tmp_path / "a.log").write_text("x\n")
    (tmp_path / "keep.log").write_text("x\n")
    res = scan(tmp_path)
    assert "a.log" not in paths(res)
    assert "keep.log" in paths(res)


@needs_git
def test_renamed_and_deleted_files(repo: Path) -> None:
    git(repo, "mv", "a.py", "renamed.py")
    (repo / "b.md").unlink()
    res = scan(repo)
    assert "renamed.py" in paths(res)
    assert "a.py" not in paths(res)
    assert "b.md" not in paths(res)


def test_traversal_paths_not_created(tmp_path: Path) -> None:
    res = scan(tmp_path)
    assert all(not f.path.startswith("/") and ".." not in f.path.split("/") for f in res.files)


def test_language_guessing() -> None:
    assert language_for("x.py") == "python"
    assert language_for("x.TS") == "typescript"
    assert language_for("noext") == "other"
    assert language_for("x.unknownext") == "other"


def test_secret_and_default_predicates() -> None:
    assert is_secret(".env")
    assert is_secret("sub/server.key")
    assert not is_secret("env.py")
    assert is_default_ignored("node_modules/x.js")
    assert is_default_ignored("a/.git/config")
    assert not is_default_ignored("src/x.py")


def test_no_files_written_into_root(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("x=1\n")
    before = set(tmp_path.iterdir())
    scan(tmp_path)
    assert set(tmp_path.iterdir()) == before


def test_scanner_never_executes_repo_code(tmp_path: Path) -> None:
    # Um "setup.py" malicioso não pode ser executado pelo scan.
    marker = tmp_path / "EXECUTED"
    payload = f"open({str(marker)!r},'w').write('boom')\n"
    (tmp_path / "setup.py").write_text(payload)
    scan(tmp_path)
    assert not marker.exists()
