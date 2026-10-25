#!/usr/bin/env python3
"""lf-0124 — runner da avaliação medida de extração de domínio.

Uso::

    python3 evaluation/run_eval.py            # roda + grava results/
    python3 evaluation/run_eval.py --check    # idem; exit 1 se diff
    python3 evaluation/run_eval.py --bless    # regrava goldens (revisar!)

A pipeline real roda por fixture: ``IndexCoordinator`` com o registry
de adapters do cenário (+ ``CrossDomainResolver`` no cenário
``crossdomain``). O estado PERSISTIDO é comparado contra
``evaluation/golden/*.json`` — inventários exaustivos curados.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from code_atlas.eval.domain_eval import (  # noqa: E402
    DOMAIN_SPECS,
    diff_report,
    evaluate_all,
    report_to_golden,
    run_domain_eval,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATEST = RESULTS_DIR / "domain-eval-latest.json"


def _status_line(domain: str, report: dict) -> str:
    if "relations" not in report:
        return f"{domain:12s} index={report.get('index_status')} {report.get('error', '')}"
    rel = report["relations"]
    ent = report["entities"]
    st = rel["by_status"]
    chk = report.get("check", {})
    mark = "OK " if chk.get("pass") else ("DIFF" if chk.get("golden") else "—  ")
    return (
        f"{domain:12s} ent={ent['total']:3d} rel={rel['total']:3d} "
        f"res={st.get('resolved', 0):3d} unr={st.get('unresolved', 0):3d} "
        f"amb={st.get('ambiguous', 0):3d} ext={st.get('external', 0):3d} "
        f"diag={len(report['diagnostics']):2d} {mark}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 quando algum cenário diverge do golden")
    ap.add_argument("--bless", action="store_true",
                    help="regrava evaluation/golden/*.json com o estado "
                         "medido atual (REVISAR diff antes de commitar)")
    ap.add_argument("--domain", default="",
                    help="roda um único cenário (id em --list)")
    ap.add_argument("--list", action="store_true",
                    help="lista cenários e sai")
    ap.add_argument("--out", default=str(LATEST),
                    help="destino do JSON de resultados")
    args = ap.parse_args()

    if args.list:
        for spec in DOMAIN_SPECS:
            print(f"{spec.eval_id:12s} {spec.fixture} "
                  f"adapters={','.join(spec.adapter_ids)}"
                  f"{' +cross' if spec.cross else ''}")
        return 0

    if args.bless:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        specs = (
            [s for s in DOMAIN_SPECS if s.eval_id == args.domain]
            if args.domain else list(DOMAIN_SPECS)
        )
        with tempfile.TemporaryDirectory(prefix="domain-eval-bless-") as wd:
            for spec in specs:
                report = run_domain_eval(_REPO_ROOT, spec, Path(wd))
                golden = report_to_golden(report)
                out = GOLDEN_DIR / f"{spec.eval_id}.json"
                out.write_text(
                    json.dumps(golden, indent=2, ensure_ascii=False,
                               sort_keys=False) + "\n",
                    encoding="utf-8",
                )
                n = len(report.get("edges", ()))
                print(f"blessed {out.relative_to(_REPO_ROOT)} "
                      f"({n} edges) — REVISAR contra a fixture")
        return 0

    with tempfile.TemporaryDirectory(prefix="domain-eval-") as wd:
        if args.domain:
            from code_atlas.eval.domain_eval import spec_by_id
            spec = spec_by_id(args.domain)
            report = run_domain_eval(_REPO_ROOT, spec, Path(wd))
            golden = None
            gpath = GOLDEN_DIR / f"{spec.eval_id}.json"
            if gpath.is_file():
                golden = json.loads(gpath.read_text(encoding="utf-8"))
            diffs = diff_report(report, golden) if golden else []
            report["check"] = {
                "golden": golden is not None, "diffs": diffs,
                "pass": not diffs,
            }
            results = {
                "schema": 1,
                "generated_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds"),
                "domains": {spec.eval_id: report},
                "summary": {"scenarios": 1,
                            "golden_compared": int(golden is not None),
                            "all_pass": not diffs},
            }
        else:
            results = evaluate_all(
                _REPO_ROOT, GOLDEN_DIR, work_dir=Path(wd)
            )
            results["generated_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        out_disp = out_path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        out_disp = out_path

    print(f"domain-eval ({results['summary']['scenarios']} cenários)")
    for domain, report in results["domains"].items():
        print(" ", _status_line(domain, report))
        for d in (report.get("check") or {}).get("diffs", []):
            print(f"    ! {d}")
    print(f"results -> {out_disp}")
    if args.check and not results["summary"]["all_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
