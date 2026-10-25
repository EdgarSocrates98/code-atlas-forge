# lf-0131 — cycle-8: graph-hardening

**Prompt:** prompt_evo_cycle8.md · **Branch:** cycle-8-production-quality
**Escopo:** Phase 4+5 — engine+worker em produção, E2E obrigatórios

## Acceptance
- Números medidos, nunca inventados; NOT RUN declarado quando aplicável.
- Sem execução de código/jobs/terraform/pipelines dos repos indexados.
- Snapshot isolation; contratos existentes preservados; adapters opt-in.
- Golden datasets nunca alterados para fazer teste passar.
