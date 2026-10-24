"""Endurecimento de symlinks no modo git (Phase 0 / P1.1).

`git ls-files` enumera symlinks rastreados sem qualquer filtro do walk —
a checagem de escape no loop do scan precisa cobrir esse enumerador.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from code_atlas.discovery.models import WarningKind
from code_atlas.discovery.scanner import scan

GIT = shutil.which("git")


def git(repo: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run(
        [GIT, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "atlas-test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "atlas-test",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": os.environ["PATH"],
            "HOME": str(repo),
        },
    )

pytestmark = pytest.mark.skipif(
    GIT is None or sys.platform == "win32", reason="git + symlinks POSIX"
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "real.py").write_text("x = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def _commit_all(r: Path) -> None:
    git(r, "add", "-A")
    git(r, "commit", "-qm", "links")


def test_git_tracked_file_symlink_escape(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("segredo fora da raiz\n")
    os.symlink(outside, repo / "link.txt")
    _commit_all(repo)

    res = scan(repo)
    paths = {f.path for f in res.files}
    assert "link.txt" not in paths
    assert any(w.kind == WarningKind.SYMLINK_ESCAPE for w in res.warnings)


def test_git_tracked_dir_symlink_escape(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "hidden.py").write_text("y = 2\n")
    os.symlink(outside, repo / "linkdir")
    _commit_all(repo)

    res = scan(repo)
    assert not any("linkdir" in f.path for f in res.files)


def test_git_tracked_broken_symlink(repo: Path, tmp_path: Path) -> None:
    os.symlink(tmp_path / "nao_existe", repo / "broken.py")
    _commit_all(repo)

    res = scan(repo)
    assert "broken.py" not in {f.path for f in res.files}
    assert not res.partial


def test_git_symlink_inside_root_allowed(repo: Path) -> None:
    os.symlink("real.py", repo / "alias.py")
    _commit_all(repo)

    res = scan(repo)
    by_path = {f.path: f for f in res.files}
    assert "alias.py" in by_path
    assert by_path["alias.py"].sha256 == by_path["real.py"].sha256


def test_git_untracked_symlink_escape(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "out.py"
    outside.write_text("z = 3\n")
    os.symlink(outside, repo / "untracked_link.py")  # não commitado

    res = scan(repo)
    assert "untracked_link.py" not in {f.path for f in res.files}
    assert any(w.kind == WarningKind.SYMLINK_ESCAPE for w in res.warnings)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX only")
def test_fifo_never_blocks_or_hangs(repo: Path) -> None:
    # git só rastreia arquivos regulares/symlinks — fifo fica untracked
    os.mkfifo(repo / "pipe.fifo")

    res = scan(repo)  # timeout implícito do pytest pegaria um hang
    fifo = next((f for f in res.files if f.path == "pipe.fifo"), None)
    if fifo is not None:
        assert fifo.sha256 is None
