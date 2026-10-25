# lf-0155 — cycle-8.1: compat-contracts

**Prompt:** prompt_evo_cycle8.1.md · **Branch:** cycle-8.1-ga-readiness
**Escopo:** Phase 12+13 — CLI/MCP/API compat + migrations integrity

## Acceptance
- Evidência medida real; NOT RUN declarado honestamente.
- Sem execução de código/jobs/terraform/pipelines dos repos indexados.
- Contratos e compatibilidade preservados; nenhuma feature removida para passar em gate.
- GA bloqueia em vuln prod unresolved sem justificativa documentada.

## Facts (backpropagation — medido)

- Phase 12: ``tests/contracts/test_public_surface.py`` — manifest
  resolve nomes pela máquina real (``build_parser``, ``tools/list``
  MCP, ``handlers.handle``): 19 subcomandos CLI, **15 tools MCP**,
  rotas GET/POST do ``handlers.py``; versão ``1.0.0rc1`` em
  ``--version``, MCP initialize e ``/diagnostics`` (``/health`` e
  ``/capabilities`` expõem API version ``1`` — distinção registrada).
- Phase 13: ``tests/storage/test_migration_chain.py`` — banco v1
  genuíno (SQL real do catálogo, checksum idêntico) com dados migra
  até head (v12 FTS5 / v11) sem perda; backfills v5/v6 verificados;
  dados de domínio v9 sobrevivem v10/v11/v12; ``sqlite_stat1``
  populado; ``domain_fts`` nasce vazio p/ snapshot pré-v12 e
  ``rebuild_domain_fts`` backfilla (idempotente, MATCH encontra).
- **Fix real**: ``migrate()`` aceitava ``schema_migrations`` com
  versão desconhecida (v99 → silêncio, retornava 99). Agora recusa
  ``MigrationError`` — versões fora do catálogo completo
  (``fts5=True`` superset) falham honesto; banco v12 em build sem
  FTS5 segue sendo caminho projetado (LIKE fallback), não "unknown".
  Checksum de rows FTS-gated agora validado contra o catálogo.
- Medido: suite completa 1589 pass / 1 skip; ruff+mypy clean.
