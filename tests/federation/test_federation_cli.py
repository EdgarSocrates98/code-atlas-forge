"""lf-0118 Phase 10 — ``code-atlas federation`` (superfície CLI).

As mutações (create/add/pin) vivem SÓ na CLI — a API é read-only.
``add`` aceita path ou id, mas só resolve para repo já indexado:
nunca auto-discovery de diretório externo.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas import cli


@pytest.fixture()
def fed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _index_two(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "repo_a"
    b = tmp_path / "repo_b"
    a.mkdir()
    b.mkdir()
    (a / "mod.py").write_text("def f():\n    return 1\n")
    (b / "mod.py").write_text("def g():\n    return 2\n")
    assert cli.main(["index", str(a), "--json"]) == 0
    assert cli.main(["index", str(b), "--json"]) == 0
    return a, b


def _ids() -> tuple[int, int, int, int]:
    conn = sqlite3.connect(str(cli.db_path()))
    conn.row_factory = sqlite3.Row
    try:
        repos = [
            r["id"]
            for r in conn.execute("SELECT id FROM repositories ORDER BY id")
        ]
        snaps = [
            (r["repository_id"], r["id"])
            for r in conn.execute(
                "SELECT repository_id, id FROM snapshots"
                " WHERE status='READY' ORDER BY repository_id")
        ]
        assert len(repos) == 2 and len(snaps) == 2
        return (
            int(repos[0]), int(repos[1]),
            int(snaps[0][1]), int(snaps[1][1]),
        )
    finally:
        conn.close()


def _out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


class TestFederationCli:
    def test_create_add_pin_view_flow(
        self, fed_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root_a, _root_b = _index_two(fed_env)
        capsys.readouterr()  # descarta a saída JSON dos dois `index`
        repo_a, repo_b, snap_a, snap_b = _ids()

        assert cli.main(["federation", "create", "ws1", "--json"]) == 0
        out = _out(capsys)
        assert out["workspace"] == "ws1" and out["created"] is True

        # add por path (repo já indexado)
        assert (
            cli.main(["federation", "add", "ws1", str(root_a), "--json"])
            == 0
        )
        assert _out(capsys)["repository_id"] == repo_a
        # add por id
        assert (
            cli.main(["federation", "add", "ws1", str(repo_b), "--json"])
            == 0
        )
        assert _out(capsys)["repository_id"] == repo_b

        assert cli.main(["federation", "view", "ws1", "--json"]) == 0
        view = _out(capsys)
        assert view["view_id"].startswith("fedview:")
        assert {
            m["repository_id"]: m["snapshot_id"]
            for m in view["members"]
        } == {repo_a: snap_a, repo_b: snap_b}

        # pin explícito — view_id muda somente se o snapshot mudar
        assert (
            cli.main(["federation", "pin", "ws1", str(repo_a),
                      str(snap_a), "--json"]) == 0
        )
        out = _out(capsys)
        assert out["pinned_snapshot_id"] == snap_a
        assert cli.main(["federation", "view", "ws1", "--json"]) == 0
        assert _out(capsys)["view_id"] == view["view_id"]  # mesmo mapa

    def test_add_rejects_unindexed_path(
        self, fed_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _index_two(fed_env)
        capsys.readouterr()
        assert cli.main(["federation", "create", "w", "--json"]) == 0
        capsys.readouterr()
        assert (
            cli.main(["federation", "add", "w",
                      str(fed_env / "never_indexed"), "--json"]) != 0
        )

    def test_view_requires_two_members(
        self, fed_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root_a, _ = _index_two(fed_env)
        capsys.readouterr()
        assert cli.main(["federation", "create", "thin", "--json"]) == 0
        capsys.readouterr()
        assert cli.main(["federation", "add", "thin", str(root_a)]) == 0
        capsys.readouterr()
        assert cli.main(["federation", "view", "thin"]) != 0

    def test_pin_validation_errors(
        self, fed_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root_a, _ = _index_two(fed_env)
        capsys.readouterr()
        repo_a, repo_b, _snap_a, snap_b = _ids()
        assert cli.main(["federation", "create", "w", "--json"]) == 0
        capsys.readouterr()
        assert cli.main(["federation", "add", "w", str(root_a)]) == 0
        assert cli.main(["federation", "add", "w", str(repo_b)]) == 0
        capsys.readouterr()
        # snapshot estranho ao repo → erro tipado, não 0
        assert (
            cli.main(["federation", "pin", "w", str(repo_a),
                      str(snap_b)]) != 0
        )
        capsys.readouterr()
        # snapshot de outro workspace via texto não-numérico → erro
        assert (
            cli.main(["federation", "pin", "w", str(repo_a), "abc"])
            != 0
        )
        capsys.readouterr()
        # pin em repo não-membro → erro
        assert (
            cli.main(["federation", "pin", "w", "424242", "1"]) != 0
        )

    def test_unknown_workspace_and_missing_args(
        self, fed_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _index_two(fed_env)
        capsys.readouterr()
        assert cli.main(["federation", "view", "ghost"]) != 0
        capsys.readouterr()
        assert cli.main(["federation", "add", "ws1"]) != 0  # falta repo
