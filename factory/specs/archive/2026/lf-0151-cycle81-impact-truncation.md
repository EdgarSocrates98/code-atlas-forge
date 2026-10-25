# lf-0151 — cycle-8.1: impact-truncation

**Prompt:** prompt_evo_cycle8.1.md · **Branch:** cycle-8.1-ga-readiness
**Escopo:** Phase 8 — truncation integrity no Impact Engine

## Acceptance
- Evidência medida real; NOT RUN declarado honestamente.
- Sem execução de código/jobs/terraform/pipelines dos repos indexados.
- Contratos e compatibilidade preservados; nenhuma feature removida para passar em gate.
- GA bloqueia em vuln prod unresolved sem justificativa documentada.

## Facts (backpropagation — medido)

- Gap real confirmado e corrigido: ``max_nodes`` (scan por nó,
  ``ORDER BY id LIMIT``) e ``max_depth`` (fronteira BFS) truncavam
  silenciosamente — scorecard §4 / ``cycle-8-impact-validation.md``.
- ``ImpactResult.truncation_reasons`` (aditivo, schema v1) carrega o
  vocabulário: ``max-paths``, ``max-changes``,
  ``max-nodes-edge-scan``, ``max-depth-frontier``,
  ``symbol-seed-limit``, ``test-candidates``; ``truncated`` é a união.
  Sonda +1 distingue fit exato de overflow (sem falso positivo).
- Razões renderizadas em JSON (campo novo), Markdown e CLI — paridade.
- Determinismo: prefixo truncado é prefixo do run sem cap.
- ``max_edges``/``query_budget`` não existem como bounds — não
  inventados.
- Doc: ``docs/impact-truncation-integrity.md``.
- Medido: ``pytest tests/impact tests/contracts tests/storage
  tests/evaluation/test_impact_truth.py tests/search/`` → 327 pass;
  suite completa 1589 pass / 1 skip (preexistente); ruff+mypy clean.
