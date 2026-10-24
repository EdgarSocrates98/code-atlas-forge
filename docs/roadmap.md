# Roadmap — Cycles 2–8

> Ciclo 1 (Foundation & Contracts) entregue — ver `docs/cycle-1-evidence.md`.
> **Ciclo 2 (Structural Intelligence) entregue** — ver
> `docs/cycle-2-evidence.md`.
> Gates abaixo copiados fielmente do Blueprint §12 — não editar sem ADR.

## Cycle 2 — Structural Intelligence ✅ entregue

Escopo realizado: Tree-sitter real via adapters Python/TypeScript/TSX,
grafo file/module/symbol canônico persistido com proveniência,
snapshots determinísticos, busca estrutural (`search`/`symbol`/
`references`/`graph`), golden corpus + eval P/R, benchmarks.

**Gate:** golden fixtures com AST e grafo corretos; erros de parsing
rastreáveis — **PASS** (`tests/golden/`, 4 corpora com expected.json).

## Cycle 3 — Semantic & Incremental ✅ entregue
> Ver `docs/cycle-3-evidence.md`, `docs/cycle-3-architecture.md`,
> `docs/cycle-3-benchmarks.md`, `docs/semantic-resolution.md`,
> `docs/scip-integration.md`, `docs/lsp-adapters.md`,
> `docs/cache-and-invalidation.md`, `docs/snapshot-consistency.md`.

Escopo: SCIP/LSP opcional, invalidação dependency-aware, builds
transacionais, snapshots prontos, rename/delete/branch.

**Gate:** no-op reindex sem parse; consistência sob falhas e concorrência.

## Cycle 4 — Search & Agent Context

Escopo: FTS+graph+vector opt-in, MCP stdio e context packs, budget,
evidence.

**Gate:** métricas e tokens versus baseline, nenhum símbolo fictício.

## Cycle 5 — Impact Engine

Escopo: blast radius, diff, tests, contracts, impact report, integração PR
opcional.

**Gate:** precision/recall auditável e distinção known/possible.

## Cycle 6 — Explorer

Escopo: Angular e grafo; arquitetura progressiva, impact graph, zoom/filtros
e UX.

**Gate:** testes acessibilidade/E2E/performance, sem câmera que desaparece.

## Cycle 7 — Domain Adapters / Federation

Escopo: SQL/PySpark/Iceberg/Glue, APIs, Terraform, CI, multi-repo,
integrações Forge opcionais.

**Gate:** fixtures cross-repo e domínio; independência entre pacotes.

## Cycle 8 — Production Quality

Escopo: hardening, benchmark competitivo, otimizador, observabilidade,
compatibilidade, release, SBOM.

**Gate:** releases reproduzíveis, roadmap/documentação e revisão externa de
achados.
