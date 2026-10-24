# Context Engine v2 (Phases 5+6)

- **Hybrid primário**: `_candidates` usa `SearchEngine(strategy=hybrid)`
  — linguagem natural recupera símbolos que substring não alcançava.
- **Fallback tipado**: `candidate-source:structural-fallback` e
  `candidate-source:empty` em diagnostics — nunca silencioso.
- **Intent-aware**: direção do grafo = `classify(task)`; intents de
  investigação sobem deps comprovadas para prioridade de contrato.
- **Budget do payload**: `_enforce_payload_budget` mede
  `render_json(pack)` (envelope+itens+metadados+diags) — despeja
  menor prioridade até caber; `payload-budget-enforced` /
  `payload-over-budget` declarados; `manifest.tokens_payload` (v2).
- **Cache** FIFO-32 por (snapshot, task, level, budget, max_symbols).
- Evidência por item: occurrence + ranking + intent.
