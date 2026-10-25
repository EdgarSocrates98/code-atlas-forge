#!/usr/bin/env python3
"""lf-0129 — runner da avaliação de acurácia contra verdade-terreno.

Diferente do ``run_eval.py`` (golden = comportamento observado curado),
este runner compara a extração real com rótulos **manuais e
independentes** em ``evaluation/ground_truth/<domain>.json`` — fatos
escritos a partir da leitura de cada arquivo da fixture, incluindo
casos adversariais cujo resultado correto é ``unresolved``/``external``
e listas ``forbidden`` anti-fabricação.

Uso::

    python3 evaluation/run_ground_truth.py             # roda + grava
    python3 evaluation/run_ground_truth.py --domain sql
    python3 evaluation/run_ground_truth.py --list

Para cada um dos 6 adapters (``crossdomain`` não é adapter — fica de
fora por escopo) a pipeline real roda via ``run_truth_eval``
(``IndexCoordinator`` + ``DomainRegistry``, snapshot isolado num db
temporário por domínio); o diff rótulo↔extração produz TP/FP/FN e
precision/recall/F1 por classe e por domínio. Saída persistida em
``evaluation/results/ground-truth-latest.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from code_atlas.eval.domain_eval import DOMAIN_SPECS  # noqa: E402
from code_atlas.eval.ground_truth import (  # noqa: E402
    load_truth,
    run_truth_eval,
    validate_truth,
)

TRUTH_DIR = Path(__file__).resolve().parent / "ground_truth"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATEST = RESULTS_DIR / "ground-truth-latest.json"

#: cenários com truth set — os 6 adapters de domínio (cross-domain é
#: resolver, não adapter; sua medição vive no harness golden).
GROUND_TRUTH_DOMAINS: tuple[str, ...] = (
    "sql",
    "pyspark",
    "aws",
    "api",
    "terraform",
    "cicd",
)


def evaluate_domain(eval_id: str, work_dir: Path) -> dict[str, object]:
    """Valida o truth set e roda a pipeline real + diff."""
    truth = load_truth(TRUTH_DIR, eval_id)
    if truth is None:
        return {"domain": eval_id, "error": "truth set ausente", "index_status": "not-run"}
    errors = validate_truth(truth)
    if errors:
        return {
            "domain": eval_id,
            "index_status": "not-run",
            "error": "truth inválido: " + "; ".join(errors),
        }
    return run_truth_eval(_REPO_ROOT, eval_id, work_dir, truth)


def _fmt_metric(v: float | None) -> str:
    return "  —  " if v is None else f"{v:5.3f}"


def _status_line(scored: dict) -> str:
    if "overall" not in scored:
        return (
            f"{scored.get('domain', '?'):10s} "
            f"{scored.get('index_status', '?'):>8s} {scored.get('error', '')}"
        )
    ov = scored["overall"]
    ent = scored["entities"]
    rel = scored["relations"]
    return (
        f"{scored['domain']:10s} P={_fmt_metric(ov['precision'])} "
        f"R={_fmt_metric(ov['recall'])} F1={_fmt_metric(ov['f1'])} "
        f"tp={ov['tp']:3d} fp={ov['fp']:2d} fn={ov['fn']:2d} "
        f"[ent {ent['tp']}/{ent['extracted']} unaud={ent['unaudited']}"
        f" | rel {rel['tp']}/{rel['extracted']} unaud={rel['unaudited']}]"
    )


def _list_domains() -> None:
    for spec in DOMAIN_SPECS:
        if spec.eval_id in GROUND_TRUTH_DOMAINS:
            has = (TRUTH_DIR / f"{spec.eval_id}.json").is_file()
            mark = "truth" if has else "SEM TRUTH"
            print(f"{spec.eval_id:10s} {spec.fixture} [{mark}]")


def _print_detail(scored: dict[str, object]) -> None:
    for cls in ("entities", "relations", "diagnostics"):
        block = scored.get(cls)
        if not isinstance(block, dict):
            continue
        for key in ("missing", "fabricated", "unexpected", "kind_mismatches", "conflicts"):
            for line in block.get(key) or ():
                print(f"    ! {cls}.{key}: {line}")
    pats = scored.get("patterns_forbidden")
    if isinstance(pats, dict):
        for v in pats.get("violations") or ():
            print(f"    ! patterns_forbidden: {v}")


def _write_results(results: dict[str, object], out: str) -> None:
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        out_disp = out_path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        out_disp = out_path
    print(f"results -> {out_disp}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", default="", help="roda um único domínio (id em --list)")
    ap.add_argument("--list", action="store_true", help="lista domínios com truth set e sai")
    ap.add_argument("--out", default=str(LATEST), help="destino do JSON de resultados")
    args = ap.parse_args()

    if args.list:
        _list_domains()
        return 0

    domains = [args.domain] if args.domain else list(GROUND_TRUTH_DOMAINS)
    results: dict[str, object] = {
        "schema": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "hand-labeled truth sets vs real IndexCoordinator "
        "extraction; TP=covered+expected, FP=forbidden/over/unlabeled"
        "(complete), FN=expected missing; precision/recall/F1 por "
        "classe e micro por domínio",
        "domains": {},
    }
    out_domains = results["domains"]
    assert isinstance(out_domains, dict)

    with tempfile.TemporaryDirectory(prefix="ground-truth-") as wd:
        work = Path(wd)
        for eval_id in domains:
            out_domains[eval_id] = evaluate_domain(eval_id, work)

    print(f"ground-truth ({len(domains)} domínios)")
    for eval_id in domains:
        scored = out_domains[eval_id]
        assert isinstance(scored, dict)
        print(" ", _status_line(scored))
        _print_detail(scored)

    _write_results(results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
