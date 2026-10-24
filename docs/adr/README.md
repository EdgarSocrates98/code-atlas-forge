# ADRs — Code Atlas Forge

Blueprint §2 lista ADR-001 a ADR-014. Este diretório contém um arquivo por
decisão **implementada** no Cycle 1; as demais permanecem *propostas* e só
ganham arquivo quando o ciclo correspondente as tornar reais.

## Decididas e implementadas (Cycle 1)

| ADR | Título | Arquivo |
|---|---|---|
| 001 | Local-first / offline-first | `adr-0001-local-first.md` |
| 002 | Monorepo modular Python | `adr-0002-monorepo-python.md` |
| 006 | SQLite WAL + FTS5 | `adr-0006-sqlite-wal-fts5.md` |
| 007 | Indexação incremental com snapshots | `adr-0007-incremental-snapshots.md` |
| 013 | Config XDG/AppData + segredos | `adr-0013-config-secrets.md` |
| 014 | Segurança de conteúdo, sem execução | `adr-0014-content-security.md` |

## Decididas e implementadas (Cycle 2)

| ADR | Título | Arquivo |
|---|---|---|
| 003 | Tree-sitter adapters | implementada como contrato `ParserAdapter` + adaptadores py/ts/tsx (docs/parser-adapters.md) |
| 005 | Grafo tipado versionado | implementada como grafo canônico persistido (docs/graph-schema.md) |
| 015 | Tree-sitter como dep de runtime pinned (emenda ADR-001) | `adr-0015-tree-sitter-runtime-deps.md` |

## Decididas e implementadas (Cycle 3)

| ADR | Título | Arquivo |
|---|---|---|
| 004 | SCIP/LSP opcionais com fallbacks marcados | implementada como `resolvers/` (docs/semantic-resolution.md, scip-integration.md, lsp-adapters.md) |
| 012 | Evidência em primeiro lugar | parcialmente no schema C1/C2; completa em Cycle 3 (provenance por aresta) |
| 016 | Arquitetura de resolvers | `adr-0016-resolver-architecture.md` |
| 017 | Decoder SCIP em stdlib | `adr-0017-scip-stdlib-decoder.md` |
| 018 | Modelo de validade incremental | `adr-0018-incremental-validity-model.md` |
| 019 | Cache de ParseResult (schema v4) | `adr-0019-parse-result-cache.md` |

## Decididas e implementadas (Cycle 3.1)

| ADR | Título | Arquivo |
|---|---|---|
| 020 | Modelo de grafo incremental: snapshots completos | `adr-0020-incremental-graph-model.md` |

## Decididas e implementadas (Cycle 4)

| ADR | Título | Arquivo |
|---|---|---|
| 021 | Política de precedência de evidência (verificação por tier superior) | `adr-0021-evidence-precedence.md` |
| 022 | Backend vetorial: sqlite-vec opt-in + bruteforce stdlib | `adr-0022-vector-backend.md` |
| 023 | Transporte MCP: JSON-RPC 2.0 ndjson stdio, stdlib-only | `adr-0023-mcp-transport.md` |
| 024 | Arquitetura FTS: `fts_occurrences` regular, snapshot-scoped | `adr-0024-fts-architecture.md` |
| 025 | Hybrid ranking: RRF k=60, dedup por símbolo | `adr-0025-hybrid-ranking.md` |
| 026 | Context pack schema: envelope versionado + IDs estáveis | `adr-0026-context-pack-schema.md` |
| 027 | Token budget: reservas 5/10/5% + clamp 64..1M | `adr-0027-token-budget.md` |
| 028 | Evidência de retrieval: matched_fields+evidence por hit | `adr-0028-retrieval-evidence.md` |

## Propostas (ciclos futuros — não implementadas)

| ADR | Título | Ciclo |
|---|---|---|
| 008 | Context packs com budget e redaction | 4 |
| 009 | REST + MCP com contratos versionados | 4 |
| 010 | Explorer Angular + Sigma.js | 6 |
| 011 | Federação opt-in entre Forjas | 7 |
