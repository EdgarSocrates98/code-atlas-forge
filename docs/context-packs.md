# Context Packs

Schema v1 (`ADR-0026`): manifest (snapshot+level+estimator+truncated),
budget, items, selected_symbols, relevant_files, dependencies,
unresolved_refs, expansion_refs, diagnostics (`insufficient_budget`,
`suggested_minimum_budget`). Tokens são **estimados**
(`heuristic-chars4`); seam `TokenCounter` para tokenizers reais.
