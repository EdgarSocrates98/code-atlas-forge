# Context Packs

Schema v1 (`ADR-0026`): manifest (snapshot+level+estimator+truncated),
budget, items, selected_symbols, relevant_files, dependencies,
unresolved_refs, expansion_refs, diagnostics (`insufficient_budget`,
`suggested_minimum_budget`). Tokens são **estimados**
(`heuristic-chars4`); seam `TokenCounter` para tokenizers reais.

## Domain packs (lf-0121 / Cycle 7 Ph13)

`DomainContextEngine.build_domain(DomainPackRequest)` produz um
`ContextPack` v2 com a seção aditiva `domain` (schema próprio v1):
cartão da entidade-semente, vizinhos top-N de `domain_relations` por
confiança, contagens honestas (inclusive `dynamic:`/unresolved),
`unresolved_refs` populadas, `expansion_refs` com refs re-consultáveis
(`dom:`/`sym:`) e contrato de re-query documentado no próprio pack.
Detalhes: `docs/cycle-7-domain-context-packs.md`.
