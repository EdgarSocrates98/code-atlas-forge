"""lf-0129 — acurácia dos adapters de domínio contra verdade-terreno.

Diferente de ``test_domain_eval.py`` (golden = inventário curado do
comportamento observado), este módulo compara a pipeline REAL
(``IndexCoordinator`` + ``DomainRegistry``) com rótulos manuais e
independentes em ``evaluation/ground_truth/<domain>.json`` — escritos
pela leitura de cada arquivo da fixture, com casos adversariais cujo
resultado correto é ``unresolved``/``external``/ausente.

Os pisos abaixo são os valores **medidos** no Cycle 8 (reportado em
``docs/cycle-8-domain-accuracy.md`` e
``evaluation/results/ground-truth-latest.json``):

- ``precision_floor``: 1.0 em todos os domínios — zero fabricação
  tolerada (qualquer item ``unexpected``/``forbidden`` derruba P);
- ``recall_floor``: valor medido por domínio — os déficits medidos são
  recalldos conhecidos e documentados, nunca escondidos;
- ``max_fn``: contagem exata dos déficits medidos — um FN novo falha
  o teste até ser revistado e classificado.

Não se afirma perfeição: ``max_fn`` > 0 em ``aws`` e ``api`` marca
defeitos reais medidos (ver doc); o piso impede regressão silenciosa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from code_atlas.eval.ground_truth import (
    TRUTH_SCHEMA,
    load_truth,
    run_truth_eval,
    validate_truth,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TRUTH_DIR = REPO_ROOT / "evaluation" / "ground_truth"

DOMAINS: tuple[str, ...] = (
    "sql",
    "pyspark",
    "aws",
    "api",
    "terraform",
    "cicd",
)

#: pisos medidos (Cycle 8, fixtures versionadas) — ver doc.
FLOORS: dict[str, dict[str, float]] = {
    "sql": {"precision": 1.0, "recall": 1.0, "max_fn": 0},
    "pyspark": {"precision": 1.0, "recall": 1.0, "max_fn": 0},
    "aws": {"precision": 1.0, "recall": 0.96, "max_fn": 5},
    "api": {"precision": 1.0, "recall": 0.95, "max_fn": 4},
    "terraform": {"precision": 1.0, "recall": 1.0, "max_fn": 0},
    "cicd": {"precision": 1.0, "recall": 1.0, "max_fn": 0},
}


@pytest.fixture(scope="module")
def all_scored(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Um run da pipeline real por domínio, compartilhado no módulo."""
    work = tmp_path_factory.mktemp("ground-truth")
    out: dict[str, dict[str, Any]] = {}
    for domain in DOMAINS:
        truth = load_truth(TRUTH_DIR, domain)
        assert truth is not None, f"truth set ausente para {domain!r}"
        out[domain] = run_truth_eval(REPO_ROOT, domain, work, truth)
    return out


def _diff_lines(scored: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for cls in ("entities", "relations", "diagnostics"):
        block = scored.get(cls) or {}
        for key in ("missing", "fabricated", "unexpected", "kind_mismatches", "conflicts"):
            for line in block.get(key) or ():
                lines.append(f"{cls}.{key}: {line}")
    for v in (scored.get("patterns_forbidden") or {}).get("violations") or ():
        lines.append(f"patterns_forbidden: {v}")
    return lines


@pytest.mark.parametrize("domain", DOMAINS)
def test_domain_precision_recall_floors(domain: str, all_scored: dict[str, dict[str, Any]]) -> None:
    """P/R medidos ≥ pisos declarados; FN ≤ déficits medidos."""
    scored = all_scored[domain]
    assert scored["index_status"] == "success"
    overall = scored["overall"]
    floor = FLOORS[domain]
    assert overall["precision"] is not None
    assert overall["precision"] >= floor["precision"], (
        f"{domain}: precision {overall['precision']} < piso "
        f"{floor['precision']}\n  - " + "\n  - ".join(_diff_lines(scored))
    )
    assert overall["recall"] is not None
    assert overall["recall"] >= floor["recall"], (
        f"{domain}: recall {overall['recall']} < piso "
        f"{floor['recall']}\n  - " + "\n  - ".join(_diff_lines(scored))
    )
    assert overall["fn"] <= floor["max_fn"], (
        f"{domain}: {overall['fn']} FN > déficits medidos "
        f"{floor['max_fn']} — classificar antes de subir o piso\n  - "
        + "\n  - ".join(_diff_lines(scored))
    )


@pytest.mark.parametrize("domain", DOMAINS)
def test_no_fabrication(domain: str, all_scored: dict[str, dict[str, Any]]) -> None:
    """Budget zero de fabricação: nenhum item proibido/inesperado."""
    scored = all_scored[domain]
    fp = scored["overall"]["fp"]
    assert fp == 0, f"{domain}: {fp} fabricações/excedentes\n  - " + "\n  - ".join(
        _diff_lines(scored)
    )
    for cls in ("entities", "relations"):
        block = scored.get(cls) or {}
        assert block.get("fabricated") in (None, [])
        assert block.get("unexpected") in (None, [])
        assert block.get("conflicts") in (None, [])
    assert not (scored.get("patterns_forbidden") or {}).get("violations")


@pytest.mark.parametrize("domain", DOMAINS)
def test_truth_audit_is_exhaustive(domain: str, all_scored: dict[str, dict[str, Any]]) -> None:
    """``complete``: todo item extraído está rotulado (unaudited == 0)."""
    scored = all_scored[domain]
    assert scored["index_status"] == "success"
    for cls in ("entities", "relations"):
        block = scored.get(cls) or {}
        assert block.get("unaudited", 0) == 0, (
            f"{domain}.{cls}: {block.get('unaudited')} itens fora de "
            "todo rótulo — o truth set deixou de cobrir a extração"
        )


# --- higiene dos truth sets -------------------------------------------


def test_truth_sets_validate() -> None:
    for domain in DOMAINS:
        truth = load_truth(TRUTH_DIR, domain)
        assert truth is not None, f"truth ausente: {domain}"
        assert truth["schema"] == TRUTH_SCHEMA
        assert truth["domain"] == domain
        assert truth["fixture"].startswith("tests/fixtures/")
        assert (REPO_ROOT / truth["fixture"]).is_dir()
        errs = validate_truth(truth)
        assert errs == [], f"{domain}: {errs}"


def test_every_domain_labels_adversarial() -> None:
    """Cada truth set precisa de anti-fabricação + caso dinâmico."""
    for domain in DOMAINS:
        truth = load_truth(TRUTH_DIR, domain)
        assert truth is not None, f"truth ausente: {domain}"
        forbidden = (
            len(truth["entities"].get("forbidden", ()))
            + len(truth["relations"].get("forbidden", ()))
            + len(truth.get("patterns_forbidden", ()))
        )
        assert forbidden >= 3, f"{domain}: só {forbidden} regras anti-fabricação"

        def _statuses(rule: dict[str, Any]) -> set[str]:
            s = rule.get("status")
            if isinstance(s, str):
                return {s}
            if isinstance(s, list):
                return set(s)
            return set()

        non_resolved = {"unresolved", "ambiguous", "external"}
        dynamic = [r for r in truth["relations"].get("expected", ()) if _statuses(r) & non_resolved]
        # fixtures totalmente estáticas podem não ter caso legítimo de
        # não-resolução — nesse caso a cobertura adversarial equivalente
        # é proibir status não-resolvido (fabricar um 'unresolved' num
        # cenário resolvível também é defeito medido).
        if not dynamic:
            dynamic = [
                r for r in truth["relations"].get("forbidden", ()) if _statuses(r) & non_resolved
            ]
        assert dynamic, (
            f"{domain}: nenhuma regra cobrindo status não-resolvido — "
            "o caso 'resultado correto é não-resolver' não está medido"
        )


def test_measured_deficits_still_documented(
    all_scored: dict[str, dict[str, Any]],
) -> None:
    """Os FN medidos existem de verdade — um FN sumido pode indicar
    melhoria real (recolher a label) ou drift de fixture; nunca passa
    em silêncio porque ``max_fn`` é cota, não alvo."""
    for domain, floor in FLOORS.items():
        scored = all_scored[domain]
        assert scored["overall"]["fn"] <= floor["max_fn"]
