# Resolução Semântica

O Atlas resolve referências **sintaticamente por padrão** — sem LLM,
sem type-checker, sem rede. Resolvers externos são opt-in e rotulados.

## Tiers de evidência

| tier | resolver | o que resolve | o que NÃO resolve |
|---|---|---|---|
| syntactic | `syntactic-scope-v1` (sempre ativo; arestas estruturais do parser aparecem como `tree-sitter-*`) | imports locais, nomes bound, chamadas de símbolo visível | tipos, overloads, barrel reexports, `import *` members, receiver-type (`d.speak()`) |
| scip | `scip` (opt-in, `.scip` file — validado por `assess_revision`) | refs do índice externo correlacionadas por span | docs fora do índice, repo errado (rejeitado), índice stale → degraded |
| lsp | `lsp:<server>` (opt-in, config) | definition/references/documentSymbol do server | server ausente → erro tipado, nunca fallback silencioso |

Integração (Cycle 3.1): arestas não-resolvidas do baseline são
re-submetidas aos resolvers externos pelo `SemanticEnricher` —
promoção só com alvo persistível deste build. Detalhes:
`docs/semantic-enrichment-pipeline.md`.

## Desfechos honestos

`resolution_status` ∈ `resolved | ambiguous | unresolved | external`:

- **resolved**: alvo único provado.
- **ambiguous**: múltiplos candidatos plausíveis (ex.: nome definido
  localmente E importado — sem ordem textual não há desfecho seguro).
- **unresolved**: nenhum candidato — inclui `from m import *` members
  e barrel reexports TS (o resolver não segue `export {X}`).
- **external**: alvo fora do snapshot (stdlib/site-packages).

`confidence_class` ∈ `resolved | syntactic | heuristic | inferred` —
rótulo de evidência da aresta, separado do desfecho.

## Regras invioláveis

- Resolver nunca inventa `resolved` para link plausível-mas-falso
  (fixtures adversariais em `tests/golden/test_semantic.py`).
- Shadowing sem ordem textual → `ambiguous`, nunca resolved errado.
- Proveniência completa por aresta: `resolver`, `confidence_class`,
  `resolution_status`, `evidence_ids`.

## Enriquecimento no pipeline (lf-0027)

Resolvers opt-in são integrados à indexação via **enrichment stage**
entre `GraphBuilder.build` e `persist_graph` — `SemanticEnricher`
re-tenta arestas não-resolvidas via `ResolverCoordinator` em ordem de
tier (scip → lsp). Regras:

- Aresta já `resolved` pelo syntactic **nunca** é reaberta.
- Resultado `resolved` com `target_id` persistível promove a aresta
  (`to_id`, `resolution_status`, `confidence_class`, `resolver` +
  nova evidência `resolver:<id>`).
- `ambiguous`/`unresolved`/`external` preservam a aresta verbatim.
- Exceção de resolver ou factory → `ParseDiagnostic`, nunca crash.
- Resolvers com lifecycle (LSP) fecham ao fim do estágio.

Ativação via `config.toml` (ausente = nenhum resolver externo):

```toml
[resolvers.scip]
index_path = "index.scip"            # relativo ao workspace ou absoluto
allow_revision_mismatch = false      # opt-in explícito p/ repo divergente

[resolvers.lsp.servers.pyright]
command = ["pyright-langserver", "--stdio"]
languages = ["python"]
enabled = true                       # ausente = desabilitado
```

O `command` vem exclusivamente da config — nada é descoberto ou
executado a partir do conteúdo indexado. Erros de bootstrap (scip
inválido, spec malformado) viram `diagnostics` no payload do `index`.

Resolvers ativos entram na identidade do índice
(`resolver:external` em `analyzer_versions`): habilitar/desabilitar
invalida relações no próximo run incremental.

Stats por execução em `index --json .stats`:

| campo | significado |
|---|---|
| `semantic_attempted` | arestas elegíveis consultadas |
| `semantic_enriched` | arestas promovidas a `resolved` |
| `semantic_diagnostics` | diagnósticos ≥ warning dos resolvers |
