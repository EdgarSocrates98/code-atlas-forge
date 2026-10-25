"""Jornada CLI ponta a ponta — Cycle 8.1 Phase 14 (lf-0156).

Um único fluxo real de usuário dirigido pela superfície do CLI — o mesmo
transporte in-process que ``evaluation/run_impact_validation.py`` usa
(``code_atlas.eval.impact_truth.run_cli``: argparse → run_* → stdout com
``CODE_ATLAS_DATA_DIR``/``CODE_ATLAS_CONFIG_DIR`` isolados num workspace
temporário). Nada de mocks, nada executado do repo indexado.

A jornada, em ordem — cada passo consome o estado deixado pelo anterior:

1. ``index <fixture> --json`` — pipeline real, snapshot ancorado;
2. ``search hello --root <fixture> --json`` — hits reais do índice;
3. ``impact symbol src.util.util --format json`` — raio de impacto com
   evidência (snapshot-bound: sem git no workspace, base/target resolvem
   para ``snapshot:<id>`` — semântica honesta do engine, zero
   ``invalid-revision``);
4. ``context <task> --format json`` — pack com manifesto schema 2;
5. ``status --json`` + ``doctor --json`` — diagnóstico do workspace;
6. honestidade terminal — ``impact`` num root não indexado produz o
   diagnóstico tipado ``repo-not-indexed``, nunca sucesso fabricado.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from code_atlas.eval.impact_truth import false_confidence_leaks, run_cli

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"


def _json(run_stdout: str) -> dict[str, Any]:
    """stdout do CLI --json/--format json deve ser um objeto parseável."""
    payload = json.loads(run_stdout.strip())
    assert isinstance(payload, dict), "payload do CLI não é objeto JSON"
    return payload


@pytest.fixture()
def workspace(tmp_path: Path) -> dict[str, Path]:
    """Workspace temporário: cópia do fixture + data/config isolados."""
    repo = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE, repo)
    return {"repo": repo, "data": tmp_path / "data", "cfg": tmp_path / "cfg"}


def test_cli_journey_index_search_impact_context_diagnostics(
    workspace: dict[str, Path],
) -> None:
    repo, data, cfg = workspace["repo"], workspace["data"], workspace["cfg"]

    # --- 1. index fixture -------------------------------------------------
    run = run_cli(["index", str(repo), "--json"], data_dir=data,
                  config_dir=cfg)
    assert run.exit_code == 0, f"index falhou: {run.stderr}"
    idx = _json(run.stdout)
    assert idx["status"] == "success"
    assert idx["files_indexed"] == 5
    assert idx["stats"]["symbols_new"] == 9
    assert idx["stats"]["evidence"] == 2
    snapshot_id = int(idx["snapshot_id"])
    assert snapshot_id >= 1

    # --- 2. search ---------------------------------------------------------
    run = run_cli(["search", "hello", "--root", str(repo), "--json"],
                  data_dir=data, config_dir=cfg)
    assert run.exit_code == 0, f"search falhou: {run.stderr}"
    found = _json(run.stdout)
    assert found["total"] == 3
    assert found["snapshot_id"] == snapshot_id
    assert found["repository"]["root_handle"] == str(repo)
    qnames = {r["qualified_name"] for r in found["results"]}
    assert {"src.hello", "src.hello.hello", "src/hello.py"} == qnames
    for hit in found["results"]:
        assert hit["occurrence_id"] > 0
        assert hit["path"] == "src/hello.py"

    # --- 3. impact ---------------------------------------------------------
    run = run_cli(
        ["impact", "symbol", "src.util.util", "--root", str(repo),
         "--format", "json"],
        data_dir=data, config_dir=cfg)
    assert run.exit_code == 0, f"impact falhou: {run.stderr}"
    imp = _json(run.stdout)
    # Workspace sem git: revisões ancoram no snapshot indexado — o engine
    # declara isso explicitamente em vez de inventar um HEAD.
    assert imp["base_revision"] == f"snapshot:{snapshot_id}"
    assert imp["target_revision"] == f"snapshot:{snapshot_id}"
    assert imp["base_snapshot_id"] == snapshot_id
    assert imp["diagnostics"] == []
    blast = {c["qualified_name"]: c for c in imp["impacts"]}
    # Blast medido: módulo direto (DEFINES), arquivo transitivo (CONTAINS).
    assert blast["src.util"]["impact"] == "direct"
    assert blast["src.util"]["strength"] == "verified"
    assert blast["src/util.py"]["impact"] == "transitive"
    for cand in imp["impacts"]:
        refs = cand["path"]["edge_refs"]
        assert refs and all(r.startswith("rel:") for r in refs)
        assert cand["path"]["hops"] == len(refs)
    # Invariante do produto (lf-0135): zero falso-confidence.
    assert false_confidence_leaks(imp) == []
    assert imp["risk"]["category"] in (
        "low", "moderate", "high", "critical")

    # --- 4. context pack ---------------------------------------------------
    run = run_cli(
        ["context", "investigar util.py e quem o consome",
         "--root", str(repo), "--format", "json"],
        data_dir=data, config_dir=cfg)
    assert run.exit_code == 0, f"context falhou: {run.stderr}"
    pack = _json(run.stdout)
    assert pack["manifest"]["schema_version"] == 2
    assert pack["manifest"]["selected"] == len(pack["items"])
    assert pack["manifest"]["selected"] > 0
    assert "src/util.py" in pack["relevant_files"]
    assert any(
        s.startswith("src.util") for s in pack["selected_symbols"]), (
        "pack sem o símbolo da tarefa")

    # --- 5. diagnostics ----------------------------------------------------
    run = run_cli(["status", "--root", str(repo), "--json"],
                  data_dir=data, config_dir=cfg)
    assert run.exit_code == 0, f"status falhou: {run.stderr}"
    st = _json(run.stdout)
    assert st["initialized"] is True
    assert st["repository_id"] >= 1
    assert st["counts"]["source_files"] == 5
    assert st["counts"]["snapshots"] == 1
    assert st["snapshot"]["id"] == snapshot_id
    assert st["snapshot"]["status"] == "READY"

    run = run_cli(["doctor", "--json"], data_dir=data, config_dir=cfg)
    assert run.exit_code == 0, f"doctor falhou: {run.stderr}"
    doc = _json(run.stdout)
    assert doc["healthy"] is True
    checks = {c["name"]: c for c in doc["checks"]}
    assert {"python", "sqlite", "fts5", "git"} <= set(checks)
    assert all(c["ok"] for c in checks.values())

    # --- 6. honestidade terminal -------------------------------------------
    # O mesmo comando num root nunca indexado declara o bloqueio tipado —
    # nunca um blast vazio disfarçado de sucesso.
    missing = workspace["repo"].parent / "never-indexed"
    missing.mkdir()
    run = run_cli(
        ["impact", "symbol", "src.util.util", "--root", str(missing),
         "--format", "json"],
        data_dir=data, config_dir=cfg)
    assert run.exit_code == 0
    blocked = _json(run.stdout)
    codes = {d["code"] for d in blocked["diagnostics"]}
    assert "repo-not-indexed" in codes
    assert blocked["impacts"] == []
