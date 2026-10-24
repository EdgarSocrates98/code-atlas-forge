# Baseline Audit — Cycle 3.1 (Phase 0)

- SHA base: `c55f713` (main pós-merge Cycle 3, PR #4 squash `233ff7f`)
- Branch: `cycle-3.1-semantic-hardening`
- Data: 2026-10-24

## Ambiente reproduzido

| check | resultado | evidência |
|---|---|---|
| `pytest -q` | **313 passed** (13.5-14.4s) | suíte completa na branch |
| `ruff check src tests` | clean | idem |
| `mypy src` | clean, 62 arquivos | idem |
| `bench_index.py --sizes 100` | 0.107s total, 560 símbolos, 940 relações | saída JSON |
| CI main | verde | run 40106186505 (push c55f713) |
| wheel install + CLI smoke | verde | job CI "Build wheel"/"Install wheel"/"CLI smoke" |
| Language servers no ambiente | apenas `rust-analyzer` (inútil para py/ts); node v22+npm presentes | `command -v` — pyright/ts-server **ausentes** |

## Matriz: implementado × efetivamente usado

| capacidade | implementada | usada no pipeline de index() | evidência |
|---|---|---|---|
| Tree-sitter py/ts/tsx | sim | **sim** | `parsers/`, fixtures golden |
| SyntacticResolver | sim | **sim** | `graph/builder.py` delega `resolve_import` |
| ResolverCoordinator (tier chain) | sim | **NÃO** | só referências dentro de `resolvers/` + tests — nenhum caller em `indexing/`/`cli`/`search` |
| ScipResolver + load_index + decoder | sim | **NÃO** | mesmos callers; zero uso no index() |
| LspResolver/LspClient/make_client | sim | **NÃO** | idem; lifecycle testado só com fake |
| Persist de `resolver`/`resolution_status`/`confidence_class`/`resolver_version` por relação | sim | **parcial** — colunas existem, só `syntactic` as popula | `migrations.py:102-106,165` |
| `--scip`, `--resolver`, `semantic-status` CLI | **não** | n/a | zero matches em `cli.py` (proposta Phase 8) |
| ParseResult cache (lf-0020) | sim | **sim** | `indexing/cache.py`, 14 testes |
| Invalidation planner + index-plan | sim | **sim** | `dependency_planner.py` + `index-plan` CLI |
| index-plan com justificativa rastreável por sinal | parcial | parcial | labels fixos ("unchanged and unaffected") — sem justificativa por sinal de invalidação |
| Reuso de subgrafo/fragmento entre snapshots | **não** | n/a | só `ParseResult` reusado; grafo+persist O(snapshot) — confirmado pelo bench (incremental ~5s no 5k) |
| identity_key git-aware + dirty flag | sim | **sim** | `discovery/identity.py`, migration v5 |
| Proveniência de índice externo (index_sha256, producer, revision, worktree_digest) | **não** | n/a | só `project_root` validado (`validation.py`) |
| Revision binding além de project_root (stale, files removidos, cross-workspace) | **não** | n/a | Phase 2 gap |

## Hipóteses do prompt — confirmadas/refutadas

- **CONFIRMADO**: módulos SCIP/LSP existem mas não participam do
  pipeline real — nem CLI aceita índice, nem index() os consulta.
  Aceite G1/G2/G3/G4/G9 atualmente **FALHÁVEL**.
- **CONFIRMADO**: cache reduz só parsing; grafo+persist reescritos por
  snapshot (P1 declarado no Cycle 3).
- **CONFIRMADO**: `index-plan` não emite justificativa por sinal.
- **REFUTADO parcialmente**: "não há infra de proveniência" — colunas
  de persistência e contratos de resolver existem; o que falta é
  wiring + revision-safety de índice externo.
- **CONFIRMADO**: ambiente sem server py/ts → E2E LSP real não
  executável sem instalação autorizada (degradado conforme grill).

## Gap register → specs

| gap | severidade | spec |
|---|---|---|
| Resolvers externos não integrados ao index() | P0 funcional | lf-0027 |
| Sem `--scip`/`--resolver`/`semantic-status` | P1 UX | lf-0032 |
| Revision safety de SCIP além de project_root | P0 integridade | lf-0028 |
| LSP: documentSymbol/diagnostics/recovery não provados | P1 | lf-0029 |
| Grafo+persist O(snapshot) no incremental | P1 perf | lf-0030 + lf-0031 |
| index-plan sem justificativa por sinal | P2 | lf-0030 |
| Cobertura de testes dos novos cenários | P1 | lf-0033 |
| Docs Cycle 3.1 + homologação G1–G12 | — | lf-0034 |

## Riscos carregados

- Windows: transport LSP usa `os.read` bloqueante + reader leak
  controlado (`_LEAKED_READERS`) — revisitar em lf-0029.
- `from m import *`, barrel reexports, overloads → unresolved honestos
  (documentado); métricas devem mantê-los fora de FP.
