# Semantic Accuracy Report — Cycle 3.1

> Fonte autoritativa: `tests/golden/eval-report.json` (regenerável via
> `tests/golden/gen_eval_report.py`). Métricas computadas de runs
> reais; modo `subset` nunca reporta `false_positive` (regra do
> Cycle 3).

## Precision/Recall por corpus (relations, modo `all`)

| corpus | symbols P/R | relations P/R | resolved | ambiguous | unresolved | external | FP |
|---|---|---|---|---|---|---|---|
| edge | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 | 0 | 0 | 0 | 0 |
| py_core | 1.00 / 1.00 | 1.00 / 1.00 | 0.905 | 0 | 2 | 0 | 0 |
| py_advanced | 1.00 / 1.00 | 1.00 / 1.00 | 0.933 | 1 | 2 | 0 | 0 |
| ts_app | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 | 0 | 0 | 0 | 0 |
| ts_advanced | 1.00 / 1.00 | 1.00 / 1.00 | 0.846 | 0 | 4 | 0 | 0 |
| large (subset) | recall 1.00 | recall 1.00 | 1.00 | 0 | 0 | 0 | n/a |

**Zero false positives** em todos os corpora modo `all` — o resolver
sintático nunca rotula link plausível-mas-falso como `resolved`
(shadowing → `ambiguous`; receiver desconhecido → `unresolved`).

## Métricas de pipeline (bloco `cycle31` do eval-report)

| métrica | valor |
|---|---|
| stale-index rejection | 4 casos (matching/mismatched/truncated/garbage) — **all_correct** |
| invalidation correctness | `incremental_matches_full` = true; `reuse_symbol_ids_stable` = true; plan vs executed consistente |
| graph equivalence | incremental ≡ cold full (canonical nodes+edges) |
| cache correctness | hit real (`files_reused=1`); blob corrompido → miss |
| snapshot isolation | 0 relações cross-snapshot; STALE invisível |

## Limitações conhecidas (não disfarçadas)

- Resolver sintático **não** infere tipo de receiver: `d.speak()` em
  instância → `unresolved` (pinado em `test_semantic_gaps`).
- `export { X }` de barrel TS: membros reexportados não viram
  `resolved` por coincidência de nome.
- Dynamic imports (`import()`/`importlib`) não produzem aresta — gap
  honesto, não falso vínculo.
- Métricas SCIP/LSP: por resolver via `resolver_metrics` no report;
  SCIP exercitado em `test_scip`/`test_semantic_enrichment`; LSP só
  com `fake_lsp` (server real ausente — `not_run`).
