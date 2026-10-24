# Arquitetura — Cycle 3

Sobre o núcleo do Cycle 2 (scan → parse → graph → persist), o Cycle 3
adiciona três camadas: **integridade de ponta a ponta**, **resolução
semântica pluggable** e **indexação incremental com reuso transacional**.

## Visão de camadas

```
scan (discovery)          → manifesto content-hash + proveniência git
  ↓ integridade (lf-0015): bytes parseados ≡ bytes hasheados
parse (tree-sitter)       → ParseResult sintático puro
  ↓ cache (lf-0020): ParseResult versionado, JSON canônico
graph builder             → universo de escopo (ScopeUniverse)
  ↓ resolvers (lf-0016+): syntactic → scip → lsp (precedência)
persist (lf-0023 batch)   → snapshot READY atômico (BEGIN IMMEDIATE)
query (lf-0022)           → search/symbol/references/definitions/
                            dependencies/index-plan — envelope v2
```

## Resolvers (`src/code_atlas/resolvers/`)

Contrato `SemanticResolver` (capabilities, tier de evidência,
resolve por `ResolutionRequest`). Precedência por tier: syntactic
(sempre ativo, dono do `ScopeUniverse`) → SCIP (opt-in por path)
→ LSP (opt-in por config). Resultados carregam `ResolverProvenance`
(resolver_id, version, evidence_tier) — nunca misturados nas métricas.

## Incremental (`src/code_atlas/indexing/`)

`change_detector` difa manifesto × inventário anterior →
`dependency_planner` produz `IndexPlan` inspecionável (drop/reparse/
revalidate-relations/reuse) → REUSE servido do `parse_cache` versionado
(cache.py) → builder re-resolve **todas** as relações sobre o universo
novo (relações nunca são copiadas prontas) → persist re-escreve o
snapshot inteiro (isolamento lógico).

## Identidade lógica de repositório (lf-0021)

`repositories.identity_key` UNIQUE: `git-remote:<url>` →
`git-root:<root-commit>` → `path:<root>`. Relocation/clone/worktree
preservam identidade; `snapshots.dirty` registra divergência de
conteúdo vs `commit_sha`.

## Snapshot consistency

Estados `BUILDING → READY | FAILED | STALE`; publicação e validação
(`foreign_key_check`, contagens) dentro da mesma transação; escritor
único via `BEGIN IMMEDIATE`; falha honesta sempre deixa rastro
(job + snapshot FAILED).
