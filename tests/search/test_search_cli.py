"""E2E dos comandos de busca estrutural (lf-0012 / Phase 6).

Indexa um fixture real multi-linguagem e verifica saída JSON estável,
filtros, paginação, seleção de snapshot e códigos de saída.
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
    (repo / "pkg" / "util.py").write_text(
        "class Base: pass\n\ndef helper(): return 1\n"
    )
    (repo / "pkg" / "core.py").write_text(
        "from pkg.util import helper, Base\n\n"
        "class Engine(Base):\n"
        "    def run(self): return helper()\n"
    )
    (repo / "app.ts").write_text(
        "export class Api { ping() {} }\n"
    )
    assert cli.main(["index", str(repo), "--json"]) == 0
    return repo


def _j(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def test_search_finds_real_symbols(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["search", "helper", "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["schema_version"] == "1"
    assert out["snapshot_id"] >= 1
    names = [r["qualified_name"] for r in out["results"]]
    assert "pkg.util.helper" in names
    assert all("helper" in n for n in names)


def test_search_filters_language_and_kind(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(
        ["search", "", "--language", "python", "--kind", "class",
         "--root", str(indexed), "--json"]
    )
    out = _j(capsys)
    assert rc == 0
    assert {r["language"] for r in out["results"]} == {"python"}
    assert {r["kind"] for r in out["results"]} == {"class"}
    # Api é typescript — excluída pelo filtro de linguagem
    assert not any("Api" in r["qualified_name"] for r in out["results"])


def test_search_limit_and_pagination(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["search", "", "--limit", "1", "--root", str(indexed),
                   "--json"])
    out = _j(capsys)
    assert rc == 0
    assert len(out["results"]) == 1
    assert out["total"] > 1
    assert out["truncated"] is True
    rc2 = cli.main(["search", "", "--limit", "1", "--offset", "999",
                    "--root", str(indexed), "--json"])
    out2 = _j(capsys)
    assert rc2 == 0
    assert out2["results"] == []  # offset além do fim → vazio, não erro


def test_symbol_detail_and_references(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["search", "helper", "--root", str(indexed), "--json"])
    sym = _j(capsys)["results"][0]

    rc = cli.main(["symbol", str(sym["symbol_id"]), "--root",
                   str(indexed), "--json"])
    det = _j(capsys)
    assert rc == 0
    assert det["result"]["qualified_name"] == "pkg.util.helper"
    assert det["result"]["occurrences"][0]["path"] == "pkg/util.py"

    # por qualified_name também funciona
    rc = cli.main(["symbol", "pkg.core.Engine", "--root", str(indexed),
                   "--json"])
    det = _j(capsys)
    assert rc == 0 and det["result"]["kind"] == "class"

    rc = cli.main(["references", "pkg.util.helper", "--root",
                   str(indexed), "--json"])
    refs = _j(capsys)
    assert rc == 0
    assert any(r["type"] == "CALLS" and r["to"] is not None
               and r["to"]["symbol"] == "pkg.util.helper"
               for r in refs["results"])
    # filtro por tipo
    rc = cli.main(["references", "pkg.util.helper",
                   "--relation-type", "EXTENDS", "--root",
                   str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert all(r["type"] == "EXTENDS" for r in out["results"])


def test_graph_dump_counts(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["graph", "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["counts"]["nodes"] > 0 and out["counts"]["edges"] > 0
    kinds = {n["kind"] for n in out["nodes"]}
    assert "class" in kinds and "function" in kinds
    assert all(e["type"] for e in out["edges"])
    # deterministico: segunda execução produz bytes idênticos
    rc = cli.main(["graph", "--root", str(indexed), "--json"])
    out2 = _j(capsys)
    assert out == out2


def test_snapshot_selection_and_errors(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["search", "x", "--root", str(indexed), "--json"])
    sid = _j(capsys)["snapshot_id"]

    # --snapshot explícito no READY funciona
    rc = cli.main(["search", "helper", "--snapshot", str(sid),
                   "--root", str(indexed), "--json"])
    assert rc == 0 and _j(capsys)["snapshot_id"] == sid

    # snapshot inexistente → exit 1
    rc = cli.main(["search", "x", "--snapshot", "99999",
                   "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 1 and "not found" in out["error"]

    # repo nunca indexado → exit 1
    other = indexed.parent / "never-indexed"
    other.mkdir()
    rc = cli.main(["search", "x", "--root", str(other), "--json"])
    assert rc == 1


def test_cycle1_commands_unchanged(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["status", "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["snapshot"]["status"] == "READY"
    rc = cli.main(["doctor", "--json"])
    assert rc == 0
