# Cycle 6.1 — Baseline Audit (Phase 0)

**Data:** 2026-10-25 · **SHA auditado:** `8f850de` (main pós-merge PR #10)
**Branch:** `cycle-6.1-explorer-hardening`
**Ambiente:** Linux (Ubuntu), Python 3.12+, Node 22, Chrome Headless 156, Playwright Chromium 141

## Gates medidos (execução real neste ambiente)

| gate | comando | resultado |
|---|---|---|
| pytest | `pytest tests/ -q` | **539 passed in 37.46s** |
| ruff | `ruff check src tests` | All checks passed |
| mypy | `mypy src` | Success, 103 source files |
| wheel | `python -m build` | sdist + whl OK |
| npm ci | `npm ci` | OK, lockfile reproduzível |
| ng build | `npm run build` | OK, output `dist/explorer` |
| ng test | `npm test -- --watch=false --browsers=ChromeHeadless` | **95/95 SUCCESS** |
| Playwright E2E | `npm run e2e` | 18/18 (última execução pré-merge, CI job `explorer-e2e` agora verde 1m22s) |
| CI PR #10 | `gh pr checks` | 8/8 pass |

## Gaps confirmados (reproduzidos / inspecionados)

| # | gap | prioridade | evidência |
|---|---|---|---|
| 1 | `RepositoriesComponent` é placeholder — não lista repos reais | P0 | fonte + docs cycle-6 |
| 2 | Sem contexto global repo/snapshot — features não compartilham seleção | P0 | `api.config.ts`, rotas isoladas |
| 3 | Sem deep-linking (repo/snapshot/símbolo/busca na URL) | P1 | rotas sem query params |
| 4 | `/graph/neighbors` sem payload completo de aresta (relation_id, resolver, confidence, occurrences) | P0 | `handlers.py` SELECT |
| 5 | Drill-down usa busca por nome de arquivo, não endpoint dedicado | P0 | graph feature, heuristic |
| 6 | Sem `GET /files/{id}/occurrences` snapshot/repo-scoped | P0 | handlers ausentes |
| 7 | Sem export JSON/MD para blast radius e context pack na UI | P1 | impact/context components |
| 8 | expansion_refs do Context Pack não acionáveis | P1 | context component |
| 9 | Sem painel de diagnóstico de grafo (nodes/edges/zoom/FPS/hidden) | P2 | diagnostics feature |
| 10 | axe/Lighthouse formal: NOT RUN | P1 | homologação cycle-6 |
| 11 | Benchmarks 1k/3k/10k/25k nós reais: NOT RUN | P1 | cap 500 testado só em unit |
| 12 | Windows packaging: NOT RUN | P1 | ciclo 6 |
| 13 | Command palette, breadcrumbs, sidebar colapsável: ausentes | P2 | shell.component |
| 14 | Rate limiting local, timeout/concurrency caps: ausentes | P1 | `server.py` |
| 15 | Navegação grafo→símbolo→grafo não preserva câmera | P1 | sem state service |

## Já corrigidos no cycle-6 final (não reabrir)

- CSP/XFO headers, `do_HEAD`, Host/Origin allowlist, evidence_ids no neighbors
- same-origin API routing; nav completa (9 itens)
- Playwright 1.56.1 + CI jobs explorer/E2E/audit verdes
- Contraste de chip a11y corrigido (opacity 0.45 → text-muted)

## Definido NOT RUN nesta baseline

- axe-core formal, Lighthouse, benchmarks de escala real, Windows smoke — serão medidos nas fases 5/11/14, não estimados aqui.
