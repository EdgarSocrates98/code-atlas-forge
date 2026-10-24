"""Testes do vertical slice do CLI (lf-0004)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli


def test_help_clean_of_future_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("doctor", "init", "index", "status",
                "search", "symbol", "references", "graph",
                "index-plan", "definitions", "dependencies"):
        assert cmd in out
    for fake in ("serve", "ui", "workspace", "plugin"):
        assert fake not in out


def test_each_subcommand_help(capsys: pytest.CaptureFixture[str]) -> None:
    for cmd in ("doctor", "init", "index", "status",
                "search", "symbol", "references", "graph",
                "index-plan", "definitions", "dependencies"):
        with pytest.raises(SystemExit) as exc:
            cli.main([cmd, "--help"])
        assert exc.value.code == 0
        assert cmd in capsys.readouterr().out.lower()


def test_init_then_repeat(ws: Path) -> None:
    rc1 = cli.main(["init", "--root", str(ws)])
    rc2 = cli.main(["init", "--root", str(ws)])
    rc3 = cli.main(["init", "--root", str(ws), "--force"])
    assert rc1 == 0
    assert rc2 == 1  # recusa clobber
    assert rc3 == 0  # --force ok


def test_init_json(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init", "--root", str(ws), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repository_id"] >= 1
    assert Path(payload["config"]).exists()
    assert Path(payload["database"]).parent.name == "xdg-data"


def test_status_pre_index(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws.mkdir(exist_ok=True)
    rc = cli.main(["status", "--root", str(ws), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["initialized"] is False or payload["snapshot"] is None
    # nunca traceback — mensagem útil


def test_index_and_status_flow(ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["index", str(sample), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "success"
    assert payload["snapshot_id"] >= 1
    assert payload["secrets_skipped"] == 1  # .env excluído
    # inventory, não símbolos
    assert "symbols" not in payload

    rc = cli.main(["status", "--root", str(sample), "--json"])
    st = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert st["snapshot"]["id"] == payload["snapshot_id"]
    assert st["snapshot"]["status"] == "READY"
    assert st["counts"]["snapshots"] == 1


def test_index_twice_noop(ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["index", str(sample), "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["index", str(sample), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "noop"
    assert payload["noop"] is True


def test_index_full_forces_rescan(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["index", str(sample)]) == 0
    capsys.readouterr()
    assert cli.main(["index", str(sample), "--full", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"  # novo snapshot mesmo sem mudança


def test_index_empty_dir(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = ws / "empty"
    empty.mkdir()
    rc = cli.main(["index", str(empty), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "success"
    assert payload["files_indexed"] == 0


def test_index_path_with_spaces_unicode(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    weird = ws / "caminho com espaços ção"
    weird.mkdir()
    (weird / "a.py").write_text("x=1\n")
    rc = cli.main(["index", str(weird), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files_indexed"] == 1


def test_index_nonexistent(ws: Path) -> None:
    rc = cli.main(["index", str(ws / "nope-xyz")])
    assert rc == 3  # EXIT_UNSAFE


def test_unknown_command_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["frobnicate"])
    assert exc.value.code == 2


def test_nothing_written_inside_indexed_repo(ws: Path, sample: Path) -> None:
    before = {p.name for p in sample.iterdir()}
    cli.main(["index", str(sample)])
    after = {p.name for p in sample.iterdir()}
    assert before == after


def test_index_ignored_files_excluded(
    ws: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["index", str(sample), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["files_indexed"] >= 4  # src/ docs/ .gitignore package.json .env→secret


def test_exit_code_documented_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "exit codes" in capsys.readouterr().out
