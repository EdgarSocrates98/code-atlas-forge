"""lf-0135 (Cycle 8 Phase 9) — blast de impacto vs corpus rotulado.

Diferente dos goldens de extração (``test_ground_truth.py`` — o que os
adapters EXTRAEM), aqui o que se mede é o que o impacto ALCANÇA: o
pipeline real roda via ``cli.main`` (``index`` + ``impact symbol
--format json`` em modo de domínio) contra os rótulos manuais de
``evaluation/impact_truth/scenarios.json`` e o conjunto alcançado é
comparado item a item.

Invariantes duras (orçamento zero — spec lf-0135):

- ``possible→verified`` leaks == 0 em todo candidato emitido;
- nenhum item ``absent`` alcançado (anti-alucinação do blast);
- evidência válida: todo ``edge_refs``/ocorrência do candidato existe
  no snapshot alvo;
- truncamento honesto nas duas sondas (cap exato → declara; folga →
  não declara e emite tudo).

Os pisos abaixo são os valores **medidos** no run do Cycle 8
(``evaluation/results/impact-truth-latest.json`` e
``docs/cycle-8-impact-validation.md``): P=R=1.000 em todos os
cenários não-vazios, 63 TP agregados, 0 FP/FN. O cenário
``pyspark-job-change`` tem blast rotulado vazio por semântica de
direção — cobertura honesta, P/R indefinidos (nunca fabricados).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_atlas.eval.impact_truth import (
    evaluate_all,
    load_scenarios,
    validate_scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_PATH = REPO_ROOT / "evaluation" / "impact_truth" / "scenarios.json"

_SCENARIOS = load_scenarios(SCENARIOS_PATH)
IDS: tuple[str, ...] = tuple(s.scenario_id for s in _SCENARIOS)

#: pisos medidos (Cycle 8) — recall/precision esperados por cenário;
#: ``expected_empty`` marca o blast vazio rotulado (direção honesta).
FLOORS: dict[str, dict[str, Any]] = {
    "sql-table-change": {"precision": 1.0, "recall": 1.0},
    "iceberg-table-change": {"precision": 1.0, "recall": 1.0},
    "pyspark-job-change": {"expected_empty": True},
    "pyspark-table-change": {"precision": 1.0, "recall": 1.0},
    "api-endpoint-removal": {"precision": 1.0, "recall": 1.0},
    "api-contract-endpoint-removal": {"precision": 1.0, "recall": 1.0},
    "openapi-spec-change": {"precision": 1.0, "recall": 1.0},
    "terraform-module-change": {"precision": 1.0, "recall": 1.0},
    "iam-role-removal": {"precision": 1.0, "recall": 1.0},
    "gh-actions-change": {"precision": 1.0, "recall": 1.0},
    "cross-repo-dependency": {"precision": 1.0, "recall": 1.0},
    "api-consumer-call": {"precision": 1.0, "recall": 1.0},
}


@pytest.fixture(scope="module")
def all_scored(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    """Um run completo do harness — indexações reais compartilhadas
    por fixture, todos os impacts re-rodados contra elas."""
    work = tmp_path_factory.mktemp("impact-truth")
    return evaluate_all(REPO_ROOT, _SCENARIOS, work)


def _report(results: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    rep = results["scenarios"].get(scenario_id)
    assert isinstance(rep, dict), f"{scenario_id}: ausente"
    assert "error" not in rep, f"{scenario_id}: {rep.get('error')}"
    return rep


def _diff_lines(rep: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for cls in ("direct", "transitive", "verified"):
        block = rep.get(cls) or {}
        for key in ("missing", "unexpected", "class_mismatches"):
            for line in block.get(key) or ():
                lines.append(f"{cls}.{key}: {line}")
    for line in rep.get("absent_violations") or ():
        lines.append(f"absent_violation: {line}")
    fc = rep.get("false_confidence") or {}
    for line in fc.get("leaks") or ():
        lines.append(f"false_confidence: {line}")
    for line in rep.get("evidence_errors") or ():
        lines.append(f"evidence: {line}")
    return lines


@pytest.mark.parametrize("scenario_id", IDS)
def test_scenario_precision_recall(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """P/R do conjunto verificado ≥ pisos medidos (ou blast vazio
    rotulado — honesto, não deficit)."""
    rep = _report(all_scored, scenario_id)
    floor = FLOORS[scenario_id]
    if floor.get("expected_empty"):
        assert rep["candidates"]["unique"] == 0, (
            f"{scenario_id}: blast rotulado vazio mas "
            f"{rep['candidates']['unique']} nós alcançados\n  - "
            + "\n  - ".join(_diff_lines(rep))
        )
        return
    ver = rep["verified"]
    assert ver["precision"] is not None
    assert ver["precision"] >= floor["precision"], (
        f"{scenario_id}: precision {ver['precision']} < piso "
        f"{floor['precision']}\n  - " + "\n  - ".join(_diff_lines(rep))
    )
    assert ver["recall"] is not None
    assert ver["recall"] >= floor["recall"], (
        f"{scenario_id}: recall {ver['recall']} < piso "
        f"{floor['recall']}\n  - " + "\n  - ".join(_diff_lines(rep))
    )


@pytest.mark.parametrize("scenario_id", IDS)
def test_per_class_recall(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """Recall por classe (direct/transitive) — nenhum esperado some
    e nenhum esperado muda de classe silenciosamente."""
    rep = _report(all_scored, scenario_id)
    for cls in ("direct", "transitive"):
        block = rep[cls]
        if rep["expected"][cls] == 0:
            assert block["tp"] == 0 and block["fp"] == 0
            continue
        assert block["fn"] == 0, (
            f"{scenario_id}.{cls}: {block['fn']} esperados não "
            f"alcançados: {block['missing']}"
        )
        assert not block["class_mismatches"], (
            f"{scenario_id}.{cls}: alcançados na classe errada: "
            f"{block['class_mismatches']}"
        )


@pytest.mark.parametrize("scenario_id", IDS)
def test_no_false_confidence_leaks(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """ORÇAMENTO ZERO: nenhum candidato promove possible→verified;
    todo ``verified`` tem path com todos os hops ``resolved``."""
    rep = _report(all_scored, scenario_id)
    fc = rep["false_confidence"]
    assert fc["count"] == 0, (
        f"{scenario_id}: {fc['count']} leak(s) de confiança\n  - "
        + "\n  - ".join(fc["leaks"])
    )
    assert fc["rate"] == 0.0


@pytest.mark.parametrize("scenario_id", IDS)
def test_no_absent_violations(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """Anti-alucinação: nenhum nó rotulado ``absent`` é alcançado."""
    rep = _report(all_scored, scenario_id)
    assert rep["absent_violations"] == [], (
        f"{scenario_id}: {len(rep['absent_violations'])} nós "
        "proibidos alcançados\n  - "
        + "\n  - ".join(rep["absent_violations"])
    )


@pytest.mark.parametrize("scenario_id", IDS)
def test_evidence_validity(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """Todo candidato carrega prova real: path íntegro, edge_refs
    resolvíveis em rows do snapshot alvo, ocorrência existente."""
    rep = _report(all_scored, scenario_id)
    assert rep["evidence_errors"] == [], (
        f"{scenario_id}: {len(rep['evidence_errors'])} erro(s) de "
        "evidência\n  - " + "\n  - ".join(rep["evidence_errors"])
    )


@pytest.mark.parametrize("scenario_id", IDS)
def test_truncation_honesty(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """Sondas de truncamento: no cap exato declara; com folga não
    declara e emite o conjunto completo."""
    rep = _report(all_scored, scenario_id)
    assert rep["truncation"].get("honest") is True, (
        f"{scenario_id}: truncamento desonesto — "
        f"{rep['truncation']}"
    )


@pytest.mark.parametrize("scenario_id", IDS)
def test_seed_resolved(
    scenario_id: str, all_scored: dict[str, Any],
) -> None:
    """O seed casou ≥1 entidade — um blast vazio por seed não
    resolvida seria medição inválida, não recall."""
    rep = _report(all_scored, scenario_id)
    chk = rep["index_independent_check"]
    assert chk["seed_entity_rows"] >= 1, (
        f"{scenario_id}: seed {rep['seed']!r} não casou nenhuma "
        "entidade — cenário inválido"
    )


def test_corpus_covers_prompt_classes() -> None:
    """O corpus cobre as classes de tarefa pedidas pelo spec."""
    classes = {sc.prompt_class for sc in _SCENARIOS}
    for wanted in ("SQL table change", "Iceberg table change",
                   "API endpoint removal", "OpenAPI schema change",
                   "Terraform module change", "IAM role removal",
                   "GH Actions change", "PySpark job change"):
        assert any(wanted in c for c in classes), (
            f"classe '{wanted}' sem cenário")
    assert len(_SCENARIOS) >= 8, (
        f"spec pede ~8 cenários; corpus tem {len(_SCENARIOS)}")


def test_corpus_labels_validate() -> None:
    for sc in _SCENARIOS:
        errs = validate_scenario(REPO_ROOT, sc)
        assert errs == [], f"{sc.scenario_id}: {errs}"


def test_every_scenario_labels_absent() -> None:
    """Cada cenário precisa de rótulos anti-alucinação (``absent``)
    — sem eles, precision não é medida, é assumida."""
    for sc in _SCENARIOS:
        assert len(sc.expected_absent) >= 2, (
            f"{sc.scenario_id}: só {len(sc.expected_absent)} "
            "rótulos absent — anti-alucinação submedida")


def test_summary_aggregates(all_scored: dict[str, Any]) -> None:
    """O micro-agregado fecha: zero FP, zero leaks, zero erros de
    evidência, zero violações de absent, truncamento limpo."""
    s = all_scored["summary"]
    micro = s["verified_micro"]
    assert micro["fp"] == 0, f"FP agregado: {micro['fp']}"
    assert micro["fn"] == 0, (
        f"FN agregado: {micro['fn']} — revisar cenários"
    )
    assert s["false_confidence_leaks"] == 0
    assert s["evidence_errors"] == 0
    assert s["absent_violations"] == 0
    assert s["truncation_dishonest"] == []


def test_unknown_coverage_is_surfaced(
    all_scored: dict[str, Any],
) -> None:
    """Os cenários com deps externos/não-resolvidos reportam
    ``unknown_coverage`` — a incerteza é contabilizada, não omitida."""
    flagged = {
        sid for sid in IDS
        if (all_scored["scenarios"][sid].get("unknown_coverage"))
    }
    # medido: api-contract (unresolved dep), iam-role e gh-actions
    # (external deps) — três cenários carregam cobertura desconhecida.
    assert flagged >= {
        "api-contract-endpoint-removal",
        "iam-role-removal",
        "gh-actions-change",
    }, f"unknown_coverage sumiu: {flagged}"
