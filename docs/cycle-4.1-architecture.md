# Cycle 4.1 — Arquitetura

Fluxo retrieval→evidence→context→MCP com grounding canônico:

```
task ──> intent.classify ──> SearchEngine.hybrid (RRF k=60 + rerank opt-in)
   │                              │ lexical(FTS5 occ) + structural(substring) + graph(multi-seed, direção=intent) + vector(opt)
   │                              v
   │                        SearchHit(occurrence_id real, evidence=occ:{id}/rrf/dir)
   v                              │
ContextEngine._candidates ────────┘  (hybrid primário; fallback tipado candidate-source:*)
   │  intent: deps sobem p/ PRIORITY_CONTRACT em find-consumers/explain-dependencies/investigate-failure
   v
_enforce_payload_budget ──> render_json medido ──> despeja menor prioridade ──> manifest.tokens_payload (schema v2)
   v
ContextPack ──> MCP tools (mesmo núcleo da CLI) ──> atlas_evidence: refs canônicas via EvidenceGrounder
```

- Grounding: `search/refs.py` — refs validadas contra repo+snapshot+hash; cross-repo/snapshot rejeitados.
- Resiliência MCP: erro de tool → `-32603`, processo nunca morre; conn por call (`finally: close`).
- Eval: `eval/metrics.py` — métricas corretas, dedup por identidade, macro+micro+categoria+linguagem.
