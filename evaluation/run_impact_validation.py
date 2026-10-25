#!/usr/bin/env python3
"""lf-0135 (Cycle 8 Phase 9) — validação do blast de impacto contra
corpus rotulado.

Para cada cenário de ``evaluation/impact_truth/scenarios.json`` este
runner executa o pipeline REAL via a mesma superfície do binário
``code-atlas`` (``cli.main`` in-process, isolado por
``CODE_ATLAS_DATA_DIR``/``CODE_ATLAS_CONFIG_DIR`` num workspace
temporário — sem executar código/jobs/terraform dos repos indexados):

1. ``index <fixture> --domains ... [--cross-domain]`` — indexação real
   com adapters opt-in;
2. ``impact symbol <seed> --root <fixture> --format json`` — análise
   de impacto em modo de domínio (seed de entidade);
3. diff conjunto-alcançado vs rótulo manual
   (``direct``/``transitive``/``absent``) → precision/recall por classe
   e micro agregado;
4. invariantes duras: vazamentos possible→verified = 0, evidência
   válida por candidato, honestidade de truncamento via re-run com
   ``--max-paths 1``.

Uso::

    python3 evaluation/run_impact_validation.py              # roda + grava
    python3 evaluation/run_impact_validation.py --scenario sql-table-change
    python3 evaluation/run_impact_validation.py --list

Saída persistida em ``evaluation/results/impact-truth-latest.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from code_atlas.eval.impact_truth import (  # noqa: E402
    evaluate_all,
    load_scenarios,
    validate_scenario,
)

SCENARIOS_PATH = (
    Path(__file__).resolve().parent / "impact_truth" / "scenarios.json"
)
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATEST = RESULTS_DIR / "impact-truth-latest.json"


def _fmt(v: float | None) -> str:
    return "  —  " if v is None else f"{v:5.3f}"


def _status_line(rep: dict[str, Any]) -> str:
    if "error" in rep:
        return f"{rep.get('id', '?'):28s} ERRO: {rep['error']}"
    ver = rep["verified"]
    assert isinstance(ver, dict)
    d = rep["direct"]
    t = rep["transitive"]
    assert isinstance(d, dict) and isinstance(t, dict)
    fc = rep["false_confidence"]
    assert isinstance(fc, dict)
    cand = rep["candidates"]
    assert isinstance(cand, dict)
    by = cand["by_best_impact"]
    assert isinstance(by, dict)
    trunc = rep["truncation"]
    assert isinstance(trunc, dict)
    honest = "ok" if trunc.get("honest") else "FALHOU"
    return (
        f"{rep['id']:28s} P={_fmt(ver['precision'])} "
        f"R={_fmt(ver['recall'])} F1={_fmt(ver['f1'])} "
        f"tp={ver['tp']:2d} fp={ver['fp']:2d} fn={ver['fn']:2d} "
        f"[d {d['tp']}/{rep['expected']['direct']} "
        f"t {t['tp']}/{rep['expected']['transitive']} "
        f"?={by['possible_or_unknown']} "
        f"leaks={fc['count']} ev={len(rep['evidence_errors'])} "
        f"trunc={honest}]"
    )


def _print_detail(rep: dict[str, Any]) -> None:
    fc = rep.get("false_confidence")
    lists: list[tuple[str, Any]] = [
        (f"{cls}.{key}", (block or {}).get(key))
        for cls in ("direct", "transitive", "verified")
        for key in ("missing", "unexpected", "class_mismatches")
        for block in (rep.get(cls) if isinstance(rep.get(cls), dict)
                      else {},)]
    lists += [("absent_violation", rep.get("absent_violations")),
              ("false_confidence", fc.get("leaks")
               if isinstance(fc, dict) else ()),
              ("evidence", rep.get("evidence_errors"))]
    for name, lines in lists:
        for line in lines or ():
            print(f"    ! {name}: {line}")
    for name in ("unknown_coverage", "possible_actual"):
        vals = rep.get(name) or ()
        if vals:
            print(f"    ~ {name}: {', '.join(str(v) for v in vals)}")
    for d in rep.get("diagnostics") or ():
        if isinstance(d, dict):
            print(f"    ~ diagnostic {d.get('code')}: {d.get('message')}")


def _write_results(results: dict[str, Any], out: str) -> None:
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
    ap.add_argument("--scenario", default="",
                    help="roda um único cenário (id em --list)")
    ap.add_argument("--list", action="store_true",
                    help="lista cenários do corpus e sai")
    ap.add_argument("--out", default=str(LATEST),
                    help="destino do JSON de resultados")
    args = ap.parse_args()

    scenarios = load_scenarios(SCENARIOS_PATH)
    if args.list:
        for sc in scenarios:
            print(f"{sc.scenario_id:28s} {sc.prompt_class:38s} "
                  f"{sc.fixture} seed={sc.seed}")
        return 0

    label_errors: dict[str, list[str]] = {}
    for sc in scenarios:
        errs = validate_scenario(_REPO_ROOT, sc)
        if errs:
            label_errors[sc.scenario_id] = errs
    if label_errors:
        for sid, errs in label_errors.items():
            print(f"{sid}: corpus inválido — {'; '.join(errs)}",
                  file=sys.stderr)
        return 2

    selected = scenarios
    if args.scenario:
        selected = [s for s in scenarios
                    if s.scenario_id == args.scenario]
        if not selected:
            print(f"cenário desconhecido: {args.scenario}",
                  file=sys.stderr)
            return 2

    with tempfile.TemporaryDirectory(prefix="impact-truth-") as wd:
        results = evaluate_all(_REPO_ROOT, selected, Path(wd))

    results.update({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus": str(SCENARIOS_PATH.relative_to(_REPO_ROOT)),
        "method": "labeled blast-radius truth vs real `code-atlas "
                  "index` + `impact symbol --format json` (domain mode, "
                  "entity seeds); precision/recall por classe "
                  "(direct/transitive) e micro; false-confidence "
                  "possible->verified leaks = orçamento zero; evidência "
                  "validada contra rows reais do snapshot alvo; "
                  "truncamento verificado via re-run --max-paths 1",
    })

    print(f"impact-truth ({len(selected)} cenários)")
    for sc in selected:
        rep = results["scenarios"][sc.scenario_id]
        assert isinstance(rep, dict)
        print(" ", _status_line(rep))
        _print_detail(rep)
    summary = results["summary"]
    assert isinstance(summary, dict)
    ver = summary["verified_micro"]
    assert isinstance(ver, dict)
    print(
        f"  micro: P={_fmt(ver['precision'])} "
        f"R={_fmt(ver['recall'])} F1={_fmt(ver['f1'])} "
        f"tp={ver['tp']} fp={ver['fp']} fn={ver['fn']} | "
        f"leaks={summary['false_confidence_leaks']} "
        f"evidence={summary['evidence_errors']} "
        f"absent={summary['absent_violations']} "
        f"trunc_bad={len(summary['truncation_dishonest'])}"
    )

    _write_results(results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
