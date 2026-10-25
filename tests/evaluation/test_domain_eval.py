"""lf-0124 — avaliação medida de extração de domínio vs goldens.

Cada teste roda a pipeline REAL (``IndexCoordinator`` + registry de
adapters + resolver cross-domain no cenário ``crossdomain``) sobre a
fixture versionada e compara o estado **persistido** com o golden
curado em ``evaluation/golden/<domain>.json`` — contagens exatas,
inventário exaustivo de arestas, entidades proibidas e diagnósticos.

Falhas imprimem o diff legível (``missing edge``/``unexpected edge``/
``entity forbidden``/count deltas) — nunca um assert booleano nu.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas.eval.domain_eval import (
    DOMAIN_SPECS,
    GOLDEN_SCHEMA,
    diff_report,
    load_golden,
    run_domain_eval,
    spec_by_id,
)
from code_atlas.storage import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO_ROOT / "evaluation" / "golden"

EVAL_IDS = tuple(spec.eval_id for spec in DOMAIN_SPECS)


@pytest.fixture(scope="module")
def all_reports(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Um run por domínio, compartilhado entre os testes do módulo."""
    work = tmp_path_factory.mktemp("domain-eval")
    return {
        spec.eval_id: run_domain_eval(REPO_ROOT, spec, work)
        for spec in DOMAIN_SPECS
    }


def _assert_no_diffs(domain: str, report: dict, golden: dict) -> None:
    diffs = diff_report(report, golden)
    assert diffs == [], (
        f"domain {domain} diverge do golden "
        f"evaluation/golden/{domain}.json:\n  - "
        + "\n  - ".join(diffs)
    )


@pytest.mark.parametrize("eval_id", EVAL_IDS)
def test_domain_matches_golden(eval_id: str, all_reports: dict) -> None:
    """Inventário persistido == golden (set-compare, diff legível)."""
    golden = load_golden(GOLDEN_DIR, eval_id)
    assert golden is not None, f"golden ausente para {eval_id!r}"
    report = all_reports[eval_id]
    assert report["index_status"] == "success"
    _assert_no_diffs(eval_id, report, golden)


def test_goldens_have_valid_schema() -> None:
    for eval_id in EVAL_IDS:
        golden = load_golden(GOLDEN_DIR, eval_id)
        assert golden is not None
        assert golden["schema"] == GOLDEN_SCHEMA
        assert golden["fixture"].startswith("tests/fixtures/")
        assert (REPO_ROOT / str(golden["fixture"])).is_dir()
        assert golden["entities"]["total"] > 0
        assert golden["relations"]["total"] > 0
        assert len(golden["edges"]) == int(golden["relations"]["total"])


def test_every_spec_has_golden() -> None:
    for spec in DOMAIN_SPECS:
        assert load_golden(GOLDEN_DIR, spec.eval_id) is not None, (
            f"spec {spec.eval_id!r} sem golden"
        )
        for aid in spec.adapter_ids:
            assert aid in {
                "sql", "pyspark", "aws-data", "api", "terraform", "cicd",
            }


# --- honestidade de resolução (estados medidos, não inventados) --------


def test_no_relation_is_resolved_without_evidence_path(
    all_reports: dict,
) -> None:
    """Toda aresta persistida carrega resolver provenance (invariante)."""
    for domain, rep in all_reports.items():
        for e in rep["edges"]:
            assert e["resolver"], (domain, e)
            assert e["status"] in {
                "resolved", "ambiguous", "unresolved", "external",
            }


def test_crossdomain_states(all_reports: dict) -> None:
    """Cenário cross-domain: cobre os quatro estados de resolução.

    - ``resolved``: spark↔sql identidade plena + consumidor→endpoint
      (method+path literal);
    - ``ambiguous``: ``shared.metrics`` sem catálogo (dois candidatos)
      + ``fetch`` sem método (GET+POST);
    - ``unresolved``: ``/v1/missing`` e ``#/components/schemas/Pets`` —
      nunca fabricados;
    - ``external``: URL absoluta fora do snapshot.
    """
    rep = all_reports["crossdomain"]
    by_status = rep["relations"]["by_status"]  # type: ignore[index]
    assert by_status["resolved"] >= 3
    assert by_status["ambiguous"] == 3
    assert by_status["unresolved"] == 2
    assert by_status["external"] == 1

    edges = {
        (e["type"], e["from"], e["to"], e["status"])
        for e in rep["edges"]  # type: ignore[index]
    }
    assert (
        "REFERENCES", "sql:staging.orders", "pyspark:staging.orders",
        "resolved",
    ) in edges
    assert (
        "CALLS_ENDPOINT", "sym:app.client.post_pet",
        "api:openapi/spec.yaml#pet-service.POST /v1/pets", "resolved",
    ) in edges
    amb = [e for e in edges if e[3] == "ambiguous"]
    assert any("cat_a.shared.metrics" in e[2] for e in amb)
    assert any("cat_b.shared.metrics" in e[2] for e in amb)

    cross = rep["cross"]  # type: ignore[index]
    stats = cross["stats"]
    assert stats["cross_resolved"] == 3
    assert stats["cross_ambiguous"] == 3
    assert cross["applied"] == {"cross_updated": 2, "cross_inserted": 4}


def test_persisted_domain_state_survives_in_db(tmp_path: Path) -> None:
    """Sanidade: rows de domínio existem no snapshot do cenário sql."""
    rep = run_domain_eval(REPO_ROOT, spec_by_id("sql"), tmp_path)
    assert rep["entities"]["total"] == 36  # type: ignore[index]
    # o db do cenário já foi fechado pelo runner — reabrir prova que o
    # snapshot foi publicado (READY), não só escrito em transação.
    conn = connect(tmp_path / "sql.db")
    n = conn.execute(
        "SELECT COUNT(*) FROM domain_relations"
    ).fetchone()[0]
    conn.close()
    assert n == 32
