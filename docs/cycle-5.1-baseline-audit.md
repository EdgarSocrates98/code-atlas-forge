# Cycle 5.1 — Baseline Audit (Phase 0)

Baseline: `2a132dd` (main). Escopo: integridade semântica do Impact
Engine — sem funcionalidade nova.

## Achados confirmados (PASS=corrigido, refutado onde aplicável)

| # | achado | sev | estado |
|---|--------|-----|--------|
| 1 | `_snapshots()`/`base` selecionavam snapshots GLOBAIS sem `repository_id` — banco multi-repo podia fazer diff de outro projeto silenciosamente | P0 | **PASS** — `ImpactSnapshotResolver` (lf-0071) |
| 2 | Snapshot explícito de outro repo aceito | P0 | **PASS** — `cross-repo-snapshot` |
| 3 | Snapshot FAILED aceito como fonte | P1 | **PASS** — `snapshot-failed` |
| 4 | Revisão pedida não era ligada ao snapshot escolhido — newest READY representava commit arbitrário | P0 | **PASS** — `RevisionBindingResolver` (lf-0072) |
| 5 | Worktree sujo equivalia a HEAD limpo | P1 | **PASS** — binding `partial` + `dirty-worktree` |
| 6 | Rename por `content_hash` sem checar cardinalidade — 2:2 gerava rename falso | P1 | **PASS** — `_pair_by_hash` exige 1:1; ambíguo → `possible` |
| 7 | Símbolo REMOVIDO não gerava blast (ocorrência atual inexistente) | P1 | **PASS** — blast na base com sufixo de razão |
| 8 | `revision` coluna inexistente no schema | — | refutado: schema usa `commit_sha`/`git_ref` |
| 9 | Risco confundido com certeza | — | já correto: eixos separados + `formula_version` |
| 10 | Selector alegava cobertura por proximidade | — | já correto: `heuristic` marcado; TESTS edges consultadas |

## NOT RUN (honesto)

- Merge-base binding em branches divergentes reais (coberto por
  `snapshot-for-revision-missing`); PR-provider publishing (fora de
  escopo local); execução de testes (proibida por contrato).
