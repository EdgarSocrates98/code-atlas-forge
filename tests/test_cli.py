"""Testes do vertical slice mínimo do CLI (lf-0001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import __version__, cli


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert f"code-atlas {__version__}" in capsys.readouterr().out


def test_help_lists_only_real_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "doctor" in out
    assert "init" in out
    for real in ("search", "symbol", "references", "graph"):
        assert real in out
    for fake in ("impact", "mcp", "serve", "ui"):
        assert fake not in out


def test_doctor_human_output_ok(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    for probe in ("python", "sqlite", "fts5", "git", "config", "data_dir"):
        assert probe in out


def test_doctor_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tool"] == "code-atlas"
    assert isinstance(payload["healthy"], bool)
    names = {c["name"] for c in payload["checks"]}
    assert {"python", "sqlite", "fts5", "git", "config", "data_dir"} <= names


def test_init_creates_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(data_dir))

    rc = cli.main(["init", "--root", str(ws)])
    assert rc == 0
    assert (cfg_dir / "config.toml").exists()
    assert data_dir.exists()


def test_init_refuses_to_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODE_ATLAS_ROOT", str(tmp_path))

    assert cli.main(["init"]) == 0
    assert cli.main(["init"]) == 1  # exists, no --force
    capsys.readouterr()
    assert cli.main(["init", "--force"]) == 0


def test_init_rejects_nonexistent_root(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["init", "--root", "/nonexistent/atlas-path-xyz"])
    assert rc == 3  # EXIT_UNSAFE
    assert "not a directory" in capsys.readouterr().err


def test_init_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODE_ATLAS_ROOT", str(tmp_path))
    rc = cli.main(["init", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert Path(payload["config"]).exists()


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2  # argparse usage error


def test_no_subcommand_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
