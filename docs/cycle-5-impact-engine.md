# Cycle 5 — Impact Engine

`src/code_atlas/impact/` — análise determinística de impacto entre
revisões/snapshots. Subsistema independente do motor de busca: reusa
`Store`, grafos e FTS persistidos; não duplica retrieval.

## Arquitetura

```
ImpactRequest → diff_reader (git read-only, -z, -M)
             → symbol_diff (snapshot×snapshot, identidade lang:kind:qname)
             → contract_analyzer (formas de assinatura, não type-check)
             → blast_radius (BFS reverso limitado, ImpactPath auditável)
             → test_selector (arestas + convenções de nome)
             → risk_classifier (regras explicáveis, risco≠incerteza)
             → report (JSON schema v1 / Markdown / human / PR comment)
             → packs (budget-aware, prioridades 1–7 do prompt)
```

## Dois eixos obrigatórios

- `FindingKind`: o que o achado é (observed_change | dependency |
  compatibility_risk | test_relevance | unknown).
- `EvidenceStrength`: verified | supported | possible | unknown.
  Uma aresta não-resolvida rebaixa o caminho para `possible` —
  conectividade nunca é promovida a certeza de quebra.

## Superfícies

- CLI: `code-atlas impact diff|worktree|symbol|tests|contract|report`
  (`--base/--target/--depth/--max-paths/--known-only/--format`).
- MCP read-only: `atlas_impact`, `atlas_diff`, `atlas_test_impact`
  (mesmo `ImpactEngine`; max_paths clampado).
- PR comment: `to_pr_comment` gera o corpo localmente — publicação é
  seam futura, desabilitada por padrão.

## Gates medidos (fixtures golden)

- symbol diff: added/removed/renamed(hash)/signature/body — verificado
  em `tests/impact/test_engine.py`.
- contratos: required-parameter-added → potential-risk;
  optional-added → likely-compatible; removed → potential-risk.
- perf: analyze p50 < 2s no fixture (medido, `test_pack_eval.py`).
- limites: `max_paths`/`max_changes` truncam com flag explícita.

## NOT RUN / limitações honestas

- Publicação em PR (gh/gitlab): sem token/ambiente — seam apenas.
- Worktree diff → re-parse on demand: ChangeSet cobre git; o diff
  semântico compara snapshots indexados (worktree exige index prévio —
  `snapshot-unavailable` explícito se ausente).
- TS overloads/generics finos: forma de assinatura apenas, não é
  type-checker — classificação `indeterminate` quando ambígua.
- Renome sem evidência (hash diverso): vira remove+add separados —
  nunca "parecido" promovido a rename.
