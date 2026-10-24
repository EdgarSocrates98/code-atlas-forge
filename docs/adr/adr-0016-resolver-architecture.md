# ADR-0016 — Arquitetura de Resolvers

## Status

Aceito (lf-0016, Cycle 3).

## Contexto

Resolução sintática era embutida no `GraphBuilder`; Cycle 3 precisava
de resolvers externos (SCIP/LSP) sem duplicar a lógica de escopo e sem
misturar classes de evidência nas métricas.

## Decisão

Contrato `SemanticResolver` com `capabilities()`, `resolver_id`,
`resolver_version` e `resolve(ResolutionRequest)`. `ScopeUniverse`
extraído do builder como estrutura compartilhada. `ResolverCoordinator`
encadeia por tier de evidência (syntactic → scip → lsp). Toda aresta
carrega `resolver` + `confidence_class` + `resolution_status` na
persistência — buckets nunca agregados.

## Consequências

- Resolver novo = implementar o contrato + registrar; nenhuma mudança
  no builder/persist.
- `resolver:` versions entram na identidade do índice (lf-0023) —
  upgrade invalida relações sem re-parsear símbolos.
