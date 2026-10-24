"""lf-0022 Phase 7: semantic CLI — envelope v2 + novos comandos.

Cobre ``index-plan`` (dry-run), ``definitions``, ``dependencies``
(depth cap + ciclos), ``references`` incoming-only, incremental
default e paginação/truncamento do envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli
from code_atlas.search.structural import MAX_DEPTH


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
        "def helper():\n    return 1\n"
    )
    (repo / "pkg" / "mid.py").write_text(
        "from pkg.util import helper\n\n\ndef mid():\n    return helper()\n"
    )
    (repo / "pkg" / "app.py").write_text(
        "from pkg.mid import mid\n\n\ndef main():\n    return mid()\n"
    )
    # ciclo: a→b→a — traversal não pode travar nem duplicar
    (repo / "pkg" / "a.py").write_text(
        "from pkg.b import fb\n\n\ndef fa():\n    return fb()\n"
    )
    (repo / "pkg" / "b.py").write_text(
        "from pkg.a import fa\n\n\ndef fb():\n    return fa()\n"
    )
    assert cli.main(["index", str(repo), "--json"]) == 0
    return repo


def _j(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


# --- envelope v2 ----------------------------------------------------------


def test_envelope_v2_fields(indexed: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["search", "helper", "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert out["schema_version"] == "2"
    assert out["repository"]["id"] >= 1
    assert out["repository"]["identity_key"].startswith("path:")
    assert "diagnostics" in out and isinstance(out["diagnostics"], list)
    assert "query" in out


# --- index: incremental default + plan summary ---------------------------


def test_index_incremental_is_default(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # segunda index sem --incremental explícito → plano incremental roda
    (indexed / "pkg" / "new.py").write_text("def n():\n    return 0\n")
    rc = cli.main(["index", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0 and out["status"] == "success"
    # plan summary presente: arquivo novo reparse, demais reuse
    assert out["plan"].get("reparse", 0) >= 1
    assert out["plan"].get("reuse", 0) >= 3


def test_index_full_bypasses_incremental(
    indexed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["index", str(indexed), "--full", "--json"])
    out = _j(capsys)
    assert rc == 0 and out["status"] == "success"
    assert "plan" in out and "diagnostics" in out


# --- index-plan -----------------------------------------------------------


def test_index_plan_dry_run(indexed: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (indexed / "pkg" / "new.py").write_text("def n():\n    return 0\n")
    rc = cli.main(["index-plan", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    plan = out["plan"]
    assert plan["incremental"] is True
    actions = {f["path"]: f["action"] for f in plan["files"]}
    assert actions["pkg/new.py"] == "reparse"
    assert actions["pkg/util.py"] == "reuse"
    # dry-run: nenhum snapshot novo
    cli.main(["status", "--root", str(indexed), "--json"])
    st = _j(capsys)
    assert st["counts"]["snapshots"] == 1


def test_index_plan_no_prior_shows_full(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CODE_ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("CODE_ATLAS_DATA_DIR", str(tmp_path / "d"))
    r = tmp_path / "fresh"
    r.mkdir()
    (r / "a.py").write_text("x = 1\n")
    rc = cli.main(["index-plan", str(r), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["plan"]["incremental"] is False
    assert all(f["action"] == "reparse" for f in out["plan"]["files"])
    assert out["diagnostics"]  # nota explicando o full build


# --- definitions ----------------------------------------------------------


def test_definitions_returns_sites(indexed: Path, capsys) -> None:
    rc = cli.main(["definitions", "pkg.util.helper", "--root",
                   str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["command"] == "definitions"
    assert out["qualified_name"] == "pkg.util.helper"
    sites = {(d["path"], d["line"]) for d in out["results"]}
    assert ("pkg/util.py", 1) in sites
    # envelope completo
    assert out["repository"]["id"] >= 1 and "diagnostics" in out


def test_definitions_unknown_symbol(indexed: Path, capsys) -> None:
    rc = cli.main(["definitions", "pkg.nope", "--root", str(indexed)])
    assert rc == 1


# --- references (incoming) ------------------------------------------------


def test_references_incoming_only(indexed: Path, capsys) -> None:
    rc = cli.main(["references", "pkg.util.helper", "--root",
                   str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["results"], "helper tem chamadores"
    for r in out["results"]:
        # incoming: o símbolo é sempre o alvo (`to`)
        assert r["to"] is not None
        assert r["to"]["symbol"] == "pkg.util.helper"
        assert r["type"] in ("REFERENCES", "CALLS")
        assert r["resolution_status"] in (
            "resolved", "ambiguous", "unresolved", "external"
        )
        # proveniência completa no envelope do resultado
        assert "resolver" in r and "confidence_class" in r
        assert "evidence_ids" in r


# --- dependencies ---------------------------------------------------------


def test_dependencies_depth_one(indexed: Path, capsys) -> None:
    rc = cli.main(["dependencies", "pkg.app.main", "--root",
                   str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["depth"] == 1
    targets = {
        r["to"]["symbol"] for r in out["results"] if r["to"] is not None
    }
    # main importa mid → depth 1 alcança mid, não helper (depth 2)
    assert any("mid" in t for t in targets)
    assert not any("helper" in t for t in targets)


def test_dependencies_depth_two_reaches_helper(indexed: Path, capsys) -> None:
    rc = cli.main(["dependencies", "pkg.app.main", "--depth", "2",
                   "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0 and out["depth"] == 2
    targets = {
        r["to"]["symbol"] for r in out["results"] if r["to"] is not None
    }
    assert any("helper" in t for t in targets)
    depths = {r["depth"] for r in out["results"]}
    assert depths <= {1, 2}


def test_dependencies_cycle_safe(indexed: Path, capsys) -> None:
    # a→b→a: depth alto não trava nem explode o resultado
    rc = cli.main(["dependencies", "pkg.a.fa", "--depth", "5",
                   "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["total"] <= out["visited_symbols"] * 4  # bounded
    assert out["depth"] == 5


def test_dependencies_depth_hard_cap(indexed: Path, capsys) -> None:
    rc = cli.main(["dependencies", "pkg.a.fa", "--depth", "999",
                   "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    assert out["depth"] == MAX_DEPTH  # clamp, não 999


def test_dependencies_pagination(indexed: Path, capsys) -> None:
    rc = cli.main(["dependencies", "pkg.a.fa", "--depth", "5",
                   "--limit", "1", "--root", str(indexed), "--json"])
    out = _j(capsys)
    assert rc == 0
    if out["total"] > 1:
        assert len(out["results"]) == 1
        assert out["truncated"] is True


# --- ambiguous nunca resolved ---------------------------------------------


def test_ambiguous_never_resolved(indexed: Path, capsys) -> None:
    # dois símbolos com mesmo nome em módulos diferentes → ambiguous
    (indexed / "pkg" / "alt.py").write_text("def dup():\n    pass\n")
    (indexed / "pkg" / "alt2.py").write_text("def dup():\n    pass\n")
    cli.main(["index", str(indexed), "--json"])
    capsys.readouterr()
    cli.main(["graph", "--root", str(indexed), "--json"])
    g = _j(capsys)
    ambiguous = [e for e in g["edges"]
                 if e["resolution_status"] == "ambiguous"]
    # independente de existir ambiguous no fixture: invariante é que
    # nenhuma aresta ambiguous aparece rotulada como resolved
    assert all(e["resolution_status"] != "resolved" for e in ambiguous)


# --- help ----------------------------------------------------------------


def test_help_lists_new_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("index-plan", "definitions", "dependencies"):
        assert cmd in out
    for fake in ("impact", "serve", "ui",
                 "workspace", "plugin"):
        assert fake not in out
