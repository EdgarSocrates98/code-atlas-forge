# Arquitetura — Cycle 4

Pipeline de retrieval→contexto→MCP sobre os snapshots do Cycle 3.1:

```
index (snapshots isolados, migrações v6–v8)
  └─ search (src/code_atlas/search/)
       ├─ structural — SQL sobre occurrences (lf-0022)
       ├─ lexical    — FTS5 fts_occurrences, snapshot-scoped (ADR-0024)
       ├─ graph      — BFS limitado em relations (lf-0037)
       ├─ vector     — opt-in: naïve stdlib | sqlite-vec (ADR-0022)
       └─ hybrid     — RRF k=60, dedup por símbolo (ADR-0025)
  └─ context (src/code_atlas/context/)
       ├─ packs — budget com reservas, níveis, renderers (ADR-0026/27)
       └─ expand — mesmo snapshot, dedup por entregue (lf-0041)
  └─ mcp (src/code_atlas/mcp.py)
       └─ JSON-RPC 2.0 ndjson stdio: 8 tools, resources, prompts
          (ADR-0023) — read-only, validado, limitado
```

Contratos estáveis: `SearchRequest/Result/Hit/Diagnostic`,
`ContextRequest/Pack/Item`, `ExpansionRequest/Result`. Toda saída
carrega `snapshot_id` — nunca fallback silencioso.
