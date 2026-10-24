# Resolução Semântica

O Atlas resolve referências **sintaticamente por padrão** — sem LLM,
sem type-checker, sem rede. Resolvers externos são opt-in e rotulados.

## Tiers de evidência

| tier | resolver | o que resolve | o que NÃO resolve |
|---|---|---|---|
| syntactic | `syntactic` (sempre ativo) | imports locais, nomes bound, chamadas de símbolo visível | tipos, overloads, barrel reexports, `import *` members |
| scip | `scip` (opt-in, `.scip` file) | refs do índice externo correlacionadas por span | docs fora do índice, repo errado (rejeitado) |
| lsp | `lsp` (opt-in, config) | definition/references do server | server ausente → erro tipado, nunca fallback silencioso |

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
