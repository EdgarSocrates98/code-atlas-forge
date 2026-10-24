# Cycle 5.1 — Evidência Final

| gate | estado |
|---|---|
| G0 baseline reproduzido | PASS (matriz final-baseline) |
| G1 binding bloqueia mismatch | PASS — teste `test_mismatch_blocks_semantic` |
| G2 content_verified com prova | PASS — `_content_verify` manifest-sha256 |
| G3 snapshot resolver adversarial | PASS — cross-repo/FAILED/STALE testados |
| G4 reconciliação histórica | PASS — `consumer_state` 4 estados testados |
| G5 multi-origem/multi-path | PASS — visited `(occ,origem)` |
| G6 test selection base+target | PASS — campo `revision` |
| G7 rename matching | PASS — N:M ambíguo sem falso verified |
| G8 consistência contrato↔risco | PASS — campos aditivos serializados |
| G9 CLI/MCP E2E | PASS — 525 testes incl. MCP SDK real |
| G10 benchmarks | PARTIAL — perf existente re-executada; corpus grande NOT RUN |
| G11 segurança | PASS — engine read-only, argv-only git, sem exec |
| G12 docs | PASS |
| G13 homologação | PASS — ver tabela |

- **525 testes**, ruff clean, mypy strict (100 arquivos)
- Schema: campos aditivos `consumer_state`, `revision` — sem migração
