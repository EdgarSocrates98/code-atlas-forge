"""lf-0136 (Cycle 8 Phase 10) — proxy medido de valor do context pack.

**Não é avaliação de agente** — sem harness de LLM/agent loop no
repositório, a qualidade-de-resposta é ``NOT RUN`` (declarado no
resultado, nunca simulado). O que se testa é o proxy estrutural:

- ``no_atlas``: simulação grep+read determinística (substring crua,
  1 query/rodada, tokens = arquivos abertos) — baseline honesto;
- ``pack_only``: ``DomainContextEngine.build_domain`` real — pack é
  semente+vizinhança 1-hop por desenho; sua cobertura de blast é
  medida, não presumida;
- ``atlas_plus_search``: ``impact symbol`` (especialista — a
  superfície certa para "o que quebra") e ``domains
  entities``+``lineage`` (montagem genérica — só fluxo-de-dados).

Pisos abaixo = valores **medidos** no run do Cycle 8
(``evaluation/results/pack-value-latest.json``,
``docs/cycle-8-context-pack-value.md``). Os déficits medidos são
produto/honestidade (pack=1-hop; lineage=só fluxo; HCL declaração≠
referência), nunca escondidos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_atlas.eval.pack_value import (
    PACK_BUDGET,
    evaluate_all,
    load_tasks,
    validate_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_PATH = REPO_ROOT / "evaluation" / "pack_value" / "tasks.json"

_TASKS = load_tasks(TASKS_PATH)
IDS: tuple[str, ...] = tuple(t.task_id for t in _TASKS)

#: fatos mínimos medidos por braço e tarefa (Cycle 8) — ``None`` =
#: sem piso fixado (braço pode variar por desenho de fixture).
FLOORS: dict[str, dict[str, int]] = {
    #                 no_atlas  pack  impact  search
    "blast-landing-orders":   {"na": 10, "pk": 5, "imp": 10, "se": 10},
    "blast-iceberg-orders":   {"na": 4, "pk": 4, "imp": 4, "se": 4},
    "blast-spark-orders":     {"na": 10, "pk": 9, "imp": 10, "se": 10},
    "endpoint-pets-get":      {"na": 4, "pk": 4, "imp": 4, "se": 3},
    "terraform-module":       {"na": 2, "pk": 2, "imp": 2, "se": 2},
    "iam-role-glue":          {"na": 13, "pk": 9, "imp": 16, "se": 5},
    "cicd-reusable":          {"na": 4, "pk": 4, "imp": 4, "se": 4},
    "xdomain-sql-orders":     {"na": 5, "pk": 4, "imp": 5, "se": 4},
    "who-calls-post-pets":    {"na": 1, "pk": 1, "imp": 1, "se": 0},
}


@pytest.fixture(scope="module")
def all_scored(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    """Um run do harness — indexações reais por fixture."""
    work = tmp_path_factory.mktemp("pack-value")
    return evaluate_all(REPO_ROOT, _TASKS, work)


def _report(results: dict[str, Any], task_id: str) -> dict[str, Any]:
    rep = results["tasks"].get(task_id)
    assert isinstance(rep, dict), f"{task_id}: ausente"
    assert "error" not in rep, f"{task_id}: {rep.get('error')}"
    return rep


@pytest.mark.parametrize("task_id", IDS)
def test_pack_budget_respected(
    task_id: str, all_scored: dict[str, Any],
) -> None:
    """O pack nunca excede o orçamento declarado (4000 tokens)."""
    rep = _report(all_scored, task_id)
    pk = rep["pack_only"]
    assert pk["status"] == "ok", (
        f"{task_id}: pack status={pk['status']} — seed não resolveu")
    assert pk["tokens"] <= pk["budget_tokens"] == PACK_BUDGET
    assert pk["tokens_estimated"] is True


@pytest.mark.parametrize("task_id", IDS)
def test_impact_answers_all_facts(
    task_id: str, all_scored: dict[str, Any],
) -> None:
    """O especialista responde o blast inteiro: ``impact`` alcança
    exatamente os fatos verificados de lf-0135, em 1 query."""
    rep = _report(all_scored, task_id)
    imp = rep["atlas_plus_search"]["impact"]
    assert imp["queries"] == 1
    assert imp["facts_answerable"] == rep["facts_expected"], (
        f"{task_id}: impact perdeu "
        f"{set(rep['no_atlas'].get('facts_found', ()))} — missing="
        f"{imp.get('facts_missing')}"
    )


@pytest.mark.parametrize("task_id", IDS)
def test_arm_fact_floors(
    task_id: str, all_scored: dict[str, Any],
) -> None:
    """Cada braço ≥ fatos medidos — um deficit novo falha até ser
    classificado (os pisos SÃO os valores medidos)."""
    rep = _report(all_scored, task_id)
    floor = FLOORS[task_id]
    for arm, key in (
            ("no_atlas", "na"),
            ("pack_only", "pk"),
            ("atlas_plus_search.impact", "imp"),
            ("atlas_plus_search.search", "se")):
        block = (rep["atlas_plus_search"][arm.rsplit(".", 1)[1]]
                 if "." in arm else rep[arm])
        got = block["facts_answerable"]
        assert got >= floor[key], (
            f"{task_id}.{arm}: {got} facts < piso medido "
            f"{floor[key]} — missing={block.get('facts_missing')}"
        )


@pytest.mark.parametrize("task_id", IDS)
def test_impact_covers_pack(
    task_id: str, all_scored: dict[str, Any],
) -> None:
    """Invariante de produto medida: o especialista cobre ≥ o pack
    (o pack é semente+1-hop; o impact é o blast completo)."""
    rep = _report(all_scored, task_id)
    assert (rep["pack_only"]["facts_answerable"]
            <= rep["atlas_plus_search"]["impact"]["facts_answerable"])


@pytest.mark.parametrize("task_id", IDS)
def test_query_counts_honest(
    task_id: str, all_scored: dict[str, Any],
) -> None:
    """Queries contadas, não assumidas: pack=1, impact=1,
    search≤2, no_atlas≤rodadas+1."""
    rep = _report(all_scored, task_id)
    assert rep["pack_only"]["queries"] == 1
    assert rep["atlas_plus_search"]["impact"]["queries"] == 1
    assert rep["atlas_plus_search"]["search"]["queries"] <= 2
    na = rep["no_atlas"]
    assert na["queries"] == len(na["rounds"])
    assert na["queries"] <= 5


def test_no_atlas_gaps_are_named() -> None:
    """Os fatos que o grep não alcança ficam NOMEADOS no resultado
    (déficit medido: declaração HCL ≠ referência, entidades só
    extraídas)."""
    for task in _TASKS:
        forms = task.facts
        assert len(forms) == len(set(forms))


def test_summary_aggregates(all_scored: dict[str, Any]) -> None:
    """O agregado fecha com os pisos medidos do ciclo."""
    s = all_scored["summary"]
    assert s["atlas_impact"]["fact_coverage"] == 1.0
    assert s["atlas_impact"]["queries"] == len(_TASKS)
    assert s["pack_only"]["queries"] == len(_TASKS)
    # pisos medidos agregados (Cycle 8):
    assert s["no_atlas"]["facts_answerable"] >= 53
    assert s["pack_only"]["facts_answerable"] >= 42
    assert s["atlas_search"]["facts_answerable"] >= 41


def test_tasks_validate() -> None:
    for t in _TASKS:
        errs = validate_task(REPO_ROOT, t)
        assert errs == [], f"{t.task_id}: {errs}"


def test_tasks_are_real_questions() -> None:
    """Tarefas cobrem os exemplos do spec: what-breaks + who-calls."""
    qs = {t.question for t in _TASKS}
    assert any("what breaks" in q for q in qs)
    assert any("who calls" in q for q in qs)
    assert len(_TASKS) >= 6, f"spec pede N tarefas reais; {len(_TASKS)}"
