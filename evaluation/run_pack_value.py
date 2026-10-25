#!/usr/bin/env python3
"""lf-0136 (Cycle 8 Phase 10) — valor medido do context pack (proxy).

**Não é avaliação de agente.** Não existe LLM/agent loop neste
repositório — a qualidade-de-resposta é declarada ``NOT RUN`` no
resultado, nunca simulada. O que este runner mede é o proxy
estrutural pedido pela spec: tamanho do pack em tokens vs fatos
contidos vs queries necessárias para montar a mesma informação via
busca — mais o baseline "sem atlas" (grep+read iterativo sobre a
árvore da fixture).

Braços por tarefa (todos reais, pipeline/superfície do binário):

- ``no_atlas`` — simulação determinística de grep+read (1 query por
  rodada de busca literal; tokens = conteúdo dos arquivos abertos);
- ``pack_only`` — ``DomainContextEngine.build_domain`` real,
  ``tokens_payload`` estimado pelo próprio engine;
- ``atlas_plus_search`` — ``impact symbol`` (especialista) e
  ``domains entities`` + ``domains lineage`` (montagem por busca
  estrutural genérica, só fluxo-de-dados).

Fatos = os ``ImpactCandidate.qualified_name`` verificados em lf-0135
(``evaluation/pack_value/tasks.json``) — o que uma resposta correta
precisa conter.

Uso::

    python3 evaluation/run_pack_value.py               # roda + grava
    python3 evaluation/run_pack_value.py --task iam-role-glue
    python3 evaluation/run_pack_value.py --list

Saída persistida em ``evaluation/results/pack-value-latest.json``.
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

from code_atlas.eval.pack_value import (  # noqa: E402
    evaluate_all,
    load_tasks,
    validate_task,
)

TASKS_PATH = (
    Path(__file__).resolve().parent / "pack_value" / "tasks.json"
)
RESULTS_DIR = Path(__file__).resolve().parent / "results"
LATEST = RESULTS_DIR / "pack-value-latest.json"


def _status_line(rep: dict[str, object]) -> str:
    if "error" in rep:
        return f"{rep.get('id', '?'):22s} ERRO: {rep['error']}"
    na = rep["no_atlas"]
    pk = rep["pack_only"]
    at = rep["atlas_plus_search"]
    assert isinstance(na, dict) and isinstance(pk, dict)
    assert isinstance(at, dict)
    imp = at.get("impact") or {}
    se = at.get("search") or {}
    n = rep["facts_expected"]
    return (
        f"{rep['id']:22s} facts={n:2d} | "
        f"no-atlas q{na['queries']} f{na['facts_answerable']} "
        f"t{na['tokens']:5d} | "
        f"pack f{pk['facts_answerable']} t{pk['tokens']:5d} "
        f"({pk['status']}) | "
        f"impact f{imp.get('facts_answerable', '-')} "
        f"t{imp.get('tokens', 0):5d} | "
        f"search q{se.get('queries', '-')} "
        f"f{se.get('facts_answerable', '-')} "
        f"t{se.get('tokens', 0):5d}"
    )


def _print_detail(rep: dict[str, object]) -> None:
    for arm in ("no_atlas", "pack_only"):
        block = rep.get(arm)
        if not isinstance(block, dict):
            continue
        for f in block.get("facts_missing") or ():
            print(f"    ~ {arm}.missing: {f}")
    at = rep.get("atlas_plus_search")
    if isinstance(at, dict):
        for sub in ("impact", "search"):
            block = at.get(sub)
            if isinstance(block, dict):
                for f in block.get("facts_missing") or ():
                    print(f"    ~ {sub}.missing: {f}")
                if block.get("error"):
                    print(f"    ~ {sub}.error: {block['error']}")


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
    ap.add_argument("--task", default="",
                    help="roda uma única tarefa (id em --list)")
    ap.add_argument("--list", action="store_true",
                    help="lista tarefas do corpus e sai")
    ap.add_argument("--out", default=str(LATEST),
                    help="destino do JSON de resultados")
    args = ap.parse_args()

    tasks = load_tasks(TASKS_PATH)
    if args.list:
        for t in tasks:
            print(f"{t.task_id:22s} {t.question[:52]:54s} "
                  f"{t.fixture} facts={len(t.facts)}")
        return 0

    label_errors = {
        t.task_id: validate_task(_REPO_ROOT, t) for t in tasks}
    label_errors = {k: v for k, v in label_errors.items() if v}
    if label_errors:
        for tid, errs in label_errors.items():
            print(f"{tid}: corpus inválido — {'; '.join(errs)}",
                  file=sys.stderr)
        return 2

    selected = tasks
    if args.task:
        selected = [t for t in tasks if t.task_id == args.task]
        if not selected:
            print(f"tarefa desconhecida: {args.task}", file=sys.stderr)
            return 2

    with tempfile.TemporaryDirectory(prefix="pack-value-") as wd:
        results = evaluate_all(_REPO_ROOT, selected, Path(wd))

    results.update({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "corpus": str(TASKS_PATH.relative_to(_REPO_ROOT)),
        "method": "proxy estrutural — pack real (DomainContextEngine,"
                  " tokens_payload estimado pelo engine, budget 4000)"
                  " vs impact JSON real vs domains entities+lineage"
                  " reais vs simulação grep+read determinística sobre"
                  " a árvore da fixture; fatos = qualified_names"
                  " verificados em lf-0135",
        "agent_quality": "NOT RUN — sem harness de agente/LLM neste "
                         "repositório; este runner mede apenas o "
                         "proxy estrutural (tokens, queries, fatos)",
        "tokenizer": "heuristic-chars4 (chars/4; o mesmo estimador "
                     "do engine de packs)",
    })

    print(f"pack-value ({len(selected)} tarefas)")
    for t in selected:
        rep = results["tasks"][t.task_id]
        assert isinstance(rep, dict)
        print(" ", _status_line(rep))
        _print_detail(rep)
    summary = results["summary"]
    assert isinstance(summary, dict)
    print("  agregado:")
    for arm in ("no_atlas", "pack_only", "atlas_impact",
                "atlas_search"):
        s = summary[arm]
        cov = s["fact_coverage"]
        cov_s = "  —  " if cov is None else f"{cov:5.3f}"
        print(f"    {arm:14s} queries={s['queries']:3d} "
              f"tokens={s['tokens']:6d} facts={s['facts_answerable']}"
              f"/{summary['facts_total']} coverage={cov_s}")

    _write_results(results, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
