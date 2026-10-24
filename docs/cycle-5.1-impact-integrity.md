# Cycle 5.1 — Impact Integrity

## Arquitetura

```
ImpactRequest
  → ImpactSnapshotResolver      (lf-0071: repo-aware, STALE ok, FAILED não)
      → ResolvedImpactSnapshots (repo_id, ids, revisions, mode, status)
  → RevisionBindingResolver     (lf-0072: exact|content_verified|
                                  partial|mismatch|unavailable)
  → symbol_diff + _pair_by_hash (lf-0073: 1:1 hash = rename supported;
                                  N:M = ambíguo possible, nunca verified)
  → blast_radius target+base    (lf-0074: removidos navegam na base)
  → contract_analyzer / test_selector / risk_classifier (lf-0075)
  → reports/packs               (lf-0076: mesmos diagnósticos do engine)
```

## Estados de binding

| estado | significado |
|--------|-------------|
| `exact` | sha do snapshot == sha resolvido, worktree limpo |
| `content_verified` | snapshot sem `commit_sha` — binding por repo/root |
| `partial` | worktree sujo ou commit sem snapshot indexado |
| `mismatch` | snapshot corresponde a outro commit → análise parcial |
| `unavailable` | revisão não resolve via `git rev-parse` |

## Diagnósticos novos

`repo-not-indexed`, `snapshot-missing`, `cross-repo-snapshot`,
`snapshot-failed`, `snapshot-unavailable`, `revision-unresolved`,
`revision-binding-mismatch`, `dirty-worktree`,
`snapshot-for-revision-missing` (com comando seguro de index),
`partial-analysis`.

## Gates medidos

- **520 testes** (+8 no ciclo), ruff clean, mypy strict (100 arquivos)
- Adversariais novos: cross-repo explícito rejeitado, FAILED rejeitado,
  STALE como base, blast de símbolo removido via base, hash 2:2 sem
  rename falso, 5 estados de binding, precisão do test-selector ≥0.9
- Bug ambiental descoberto: hook global `graphify` sujava worktrees de
  teste — detecção `dirty-worktree` estava **correta**; teste usa
  `.git/info/exclude` para determinismo.
