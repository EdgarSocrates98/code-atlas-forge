"""Fluxo ponta a ponta: init → index → status → doctor (lf-0004)."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from code_atlas import cli

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
GIT = shutil.which("git")

needs_git = pytest.mark.skipif(GIT is None, reason="git binary absent")


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace isolado com repo git real a partir do fixture."""
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("CODE_ATLAS_ROOT", str(repo))
    return repo


@needs_git
def test_full_flow_on_git_repo(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_git = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(env), "PATH": os.environ["PATH"],
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run([GIT, "-C", str(env), *args], check=True, env=env_git,
                       capture_output=True)

    assert cli.main(["init", "--root", str(env)]) == 0
    capsys.readouterr()
    assert cli.main(["index"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["git"]["is_repo"] is True
    assert st["git"]["branch"] in ("main", "master")
    assert st["snapshot"]["status"] == "READY"
    assert st["schema_version"] >= 1
    assert cli.main(["doctor"]) == 0


def test_empty_workspace_flow(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = env / "w"
    empty.mkdir()
    assert cli.main(["doctor"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--root", str(empty)]) == 0
    out = capsys.readouterr().out
    assert "not initialized" in out
    assert cli.main(["index", str(empty)]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--root", str(empty), "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["snapshot"]["files"] == 0


def test_cli_offline_no_network_imports() -> None:
    """`src/` não importa socket/urllib/requests — operação 100% offline."""
    src = Path(__file__).resolve().parents[2] / "src"
    banned = {"socket", "urllib", "requests", "http", "ftplib", "smtplib",
              "telnetlib", "asyncio"}  # asyncio abriria sockets
    offenders: list[str] = []
    for py in src.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            offenders.extend(f"{py.name}:{n}" for n in names if n in banned)
    assert offenders == []


def test_cli_no_forge_imports() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8").lower()
        assert "import claude" not in text and "import codex" not in text
        assert "forge_" not in text or "code_atlas_forge" in py.name
