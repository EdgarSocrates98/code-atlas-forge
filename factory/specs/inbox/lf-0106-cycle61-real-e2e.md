# lf-0106 — cycle-6.1: real-e2e

**Prompt:** prompt_evo_cycle6.1.md · **Branch:** cycle-6.1-explorer-hardening
**Escopo:** Phase 13 — 28 fluxos Playwright reais, sem assertions genéricas

## Acceptance
- Sem placeholders; sem dados inventados; evidence-first; read-only; loopback-only.
- Gates do prompt correspondentes passando com execução real (não estimada).
- pytest/ruff/mypy + ng build/test (+e2e quando aplicável) verdes após a mudança.
