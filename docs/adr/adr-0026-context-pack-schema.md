# ADR-0026 — Context Pack Schema (lf-0040)

## Decisão
Envelope versionado `ContextPack{manifest, budget, items,
selected_symbols, relevant_files, dependencies, unresolved_refs,
expansion_refs, diagnostics}`. `ContextItem` com `item_id` estável
(`sym:`, `file:`, `nb:`), prioridade, `est_tokens`, evidências.
Níveis `orientation|focus|deep`. Renderers JSON/Markdown/human.

## Por quê
- IDs estáveis derivados de snapshot+seleção (não memória) —
  necessário p/ expansão/dedup reproduzível (lf-0041).
- Conteúdo indexado vai só a `body`/`title` — nunca a campos de
  controle (segurança, security-cycle4).
- `tokens_estimated=True` declarado: estimador heurístico, nunca
  contagem exata fingida.
