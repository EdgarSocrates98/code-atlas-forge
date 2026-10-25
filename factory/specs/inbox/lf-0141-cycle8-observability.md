# lf-0141 — cycle-8: observability

**Prompt:** prompt_evo_cycle8.md · **Branch:** cycle-8-production-quality
**Escopo:** Phase 15 — diagnósticos, health, métricas

## Acceptance
- Números medidos, nunca inventados; NOT RUN declarado quando aplicável.
- Sem execução de código/jobs/terraform/pipelines dos repos indexados.
- Snapshot isolation; contratos existentes preservados; adapters opt-in.
- Golden datasets nunca alterados para fazer teste passar.
