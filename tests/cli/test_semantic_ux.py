"""lf-0032 Phase 8: CLI & semantic UX — --scip, --resolver, semantic-status.

Cobre: filtro de resolvers (syntactic/scip/lsp:<nome>), erro tipado
para resolver desconhecido, --scip ad-hoc com índice inválido, e o
envelope do ``semantic-status`` lendo só o snapshot READY.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli


@pytest.fixture()
def indexed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "xdg-config"
    data = tmp_path / "xdg-data"
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(data))
    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (repo / "pkg" / "app.py").write_text(
        "from pkg.util import helper\n\n\ndef main():\n    return helper()\n"
    )
    assert cli.main(["index", str(repo), "--json"]) == 0
    return repo


def _j(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def test_semantic_status_syntactic_only(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert cli.main(["semantic-status", str(indexed), "--json"]) == 0
    p = _j(capsys)
    assert p["indexed"] is True
    assert p["snapshot_id"] is not None
    ids = {r["id"] for r in p["resolvers"]}
    assert ids == {"syntactic-scope-v1"}
    r = p["resolvers"][0]
    assert r["installed"] and r["enabled"] and r["used"]
    assert r["edges"] > 0
    cov = p["coverage"]
    assert cov["total"] == r["edges"]
    assert cov["total"] > 0


def test_semantic_status_not_indexed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d"))
    empty = tmp_path / "empty"
    empty.mkdir()
    capsys.readouterr()
    assert cli.main(["semantic-status", str(empty), "--json"]) == 0
    p = _j(capsys)
    assert p["indexed"] is False
    assert p["snapshot_id"] is None
    assert p["coverage"]["total"] == 0
    assert p["resolvers"][0]["used"] is False


def test_index_resolver_unknown_is_typed_error(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    rc = cli.main(
        ["index", str(indexed), "--resolver", "bogus", "--json"])
    assert rc == 1
    err = _j(capsys)["error"]
    assert "resolver desconhecido" in err and "bogus" in err


def test_index_resolver_syntactic_drops_external(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--resolver syntactic funciona mesmo sem nada externo configurado."""
    capsys.readouterr()
    rc = cli.main(
        ["index", str(indexed), "--resolver", "syntactic", "--json"])
    assert rc == 0
    p = _j(capsys)
    assert p["status"] in ("success", "noop")


def test_index_scip_invalid_path_reports_diagnostic(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--scip com arquivo inexistente → diagnóstico, não crash."""
    capsys.readouterr()
    rc = cli.main(
        ["index", str(indexed), "--scip",
         str(indexed / "missing.scip"), "--json"])
    assert rc == 0  # index segue — diagnóstico, nunca crash
    p = _j(capsys)
    assert any("scip" in d for d in p["diagnostics"])


def test_semantic_status_reports_scip_from_flag(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """semantic-status reflete config — flag --scip é por-invocation."""
    capsys.readouterr()
    assert cli.main(["semantic-status", str(indexed), "--json"]) == 0
    p = _j(capsys)
    assert {r["id"] for r in p["resolvers"]} == {"syntactic-scope-v1"}
    assert p["revision"]["scip_index_sha"] is None
