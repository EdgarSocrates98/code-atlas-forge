"""Fixtures compartilhadas dos testes de discovery."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GIT = shutil.which("git")


def git(repo: Path, *args: str) -> None:
    assert GIT is not None
    subprocess.run(
        [GIT, "-C", str(repo), *args], check=True, capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "atlas-test", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "atlas-test", "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ["PATH"],
            "HOME": str(repo),  # isola user gitconfig
        },
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Repositório git básico com 1 commit e 2 arquivos."""
    r = tmp_path / "repo"
    r.mkdir()
    if GIT:
        git(r, "init", "-q")
        (r / "a.py").write_text("print('a')\n")
        (r / "b.md").write_text("# b\n")
        (r / ".gitignore").write_text("ignored_dir/\n*.log\n")
        git(r, "add", "-A")
        git(r, "commit", "-qm", "init")
    return r


@pytest.fixture()
def nogit(tmp_path: Path) -> Path:
    """Diretório sem git."""
    d = tmp_path / "plain"
    d.mkdir()
    (d / "x.py").write_text("x = 1\n")
    (d / ".gitignore").write_text("skip/\n")
    (d / "skip").mkdir()
    (d / "skip" / "gone.txt").write_text("nope\n")
    return d
