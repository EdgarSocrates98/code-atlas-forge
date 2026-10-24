# ADR-0020 — Modelo de grafo incremental: snapshots completos, invalidação precisa

- Status: accepted (lf-0030)
- Decisores: spec owner + implementação

## Contexto

O cache de `ParseResult` (lf-0020) elimina re-parse; persistência
continua O(snapshot) — incremental no 5k economiza ~15-20% (medido,
lf-0023). A spec pede análise de structural sharing/fragments antes
de alterar o modelo (análise completa:
`docs/incremental-graph-reuse.md`).

## Decisão

**Snapshots completos por build** permanecem o modelo de
persistência. Rejeitamos structural sharing de occurrences/relations
entre snapshots porque:

- relações são fatos sobre o **universo** (todos os nós), não sobre
  um arquivo — compartilhá-las exigiria prova de equivalência de
  universo, que custa o próprio rebuild;
- isolamento por snapshot é o que garante `incremental ≡ full`
  canonicamente *grátis*;
- o gargalo real era round-trips por linha, já reduzido 96% em
  lf-0023 sem alterar o modelo.

Em compensação, fortalecemos a **invalidação** (Phase 6):
`resolver:scip=<sha16>` entra em `analyzer_versions` — índice SCIP
atualizado invalida relações conservadoramente, junto aos sinais já
existentes (dependências transitivas, imports não-resolvidos, parse
incompleto, bump de resolver/parser, rename/delete, dirty worktree).

## Consequências

- GC trivial (`ON DELETE CASCADE`); sem refcount, sem dedup de linhas.
- `index-plan` emite `reason` por arquivo — justificativa rastreável.
- Custo de persist O(snapshot) é aceito conscientemente; revisitar se
  corpora >50k arquivos aparecerem.
