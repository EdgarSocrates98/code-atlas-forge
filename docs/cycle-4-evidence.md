# Cycle 4 — Evidência de Homologação

Branch `cycle-4-hybrid-search-mcp`, specs lf-0035..lf-0046 (todas
arquivadas com review em `factory/reviews/`).

## SHAs por spec

| spec | entrega | sha |
|---|---|---|
| lf-0035 | proveniência + precedência por tier (ADR-0021) | `6f8b737` |
| lf-0036 | FTS5 snapshot-scoped + lexical (ADR-0024, mig. v7) | `279f9bc` |
| lf-0037 | graph retrieval BFS limitado | `87526ee` |
| lf-0038 | hybrid RRF k=60 (ADR-0025) | `e0b023b` |
| lf-0039 | vector opt-in (ADR-0022, mig. v8) | `c16bfa5` |
| lf-0040 | context packs + budget (ADR-0026/27) | `b7e3909` |
| lf-0041 | expansion + progressive disclosure | `08c2db6` |
| lf-0042 | MCP stdio ndjson (ADR-0023) | `a8ac922` |
| lf-0043 | mcp doctor + host manifests | `be395bc` |
| lf-0044 | retrieval+context eval, judgments | `1813729` |
| lf-0045 | adversarial + perf + budget clamp | `da46288` |

## Contratos

`SearchRequest/SearchHit/SearchResult/SearchDiagnostic/SearchCapability`,
`ContextRequest/ContextPack/ContextItem/PackEvidence/Budget/
PackDiagnostics/PackManifest/ExpansionRef`, `ExpansionRequest/
ExpansionResult`, `EmbeddingProvider/VectorIndex/VectorSearchResult/
EmbeddingModelInfo/EmbeddingCache`, MCP tools/resources/prompts.

## Migrações

v6 evidence (resolver_id/version/tier/hash/validation), v7
`fts_occurrences`, v8 embeddings+vector cache.

## Métricas medidas (eval-report.json → cycle4, n=3)

| estratégia | P@10 | R@10 | MRR | NDCG | ev | fp |
|---|---|---|---|---|---|---|
| structural | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 4 |
| lexical | 0.42 | 1.0 | 1.0 | 1.0 | 0.0 | 14 |
| graph | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | 68 |
| hybrid | 0.09 | 1.0 | 1.0 | 1.0 | 1.0 | 83 |

Contexto: missing-critical 0.25, 0 violações de budget. Graph R=0 e
ev=0 lexical/structural são **gaps reais medidos** — ver
cycle-4-limitations.md, não maquiados.

## Homologação — 23 testes obrigatórios

| # | item | resultado |
|---|---|---|
| 1 | Ruff | PASS (clean) |
| 2 | mypy strict | PASS (83 arquivos) |
| 3 | pytest completo | PASS — 440 testes |
| 4 | Golden corpus | PASS (tests/golden) |
| 5 | FTS5 integration | PASS (tests/search/test_lexical.py) |
| 6 | Hybrid ranking | PASS (test_hybrid.py) |
| 7 | Graph retrieval | PASS (test_graph_retrieval.py) |
| 8 | Vector opt-in | PASS (tests/search/vector/, sqlite-vec NOT RUN — ausente) |
| 9 | Token budget | PASS (tests/context/) |
| 10 | Context Pack | PASS |
| 11 | Context expansion | PASS (snapshot removido → erro explícito) |
| 12 | MCP protocol | PASS (tests/mcp/) |
| 13 | MCP real client | **NOT RUN** — nenhum host real no ambiente; fake client cobre protocolo |
| 14 | Host configuration | PASS (manifests emitidos; escrita de config: nunca) |
| 15 | Prompt injection | PASS (test_adversarial_cycle4.py) |
| 16 | Security | PASS — 0 P0/P1 (security-cycle4.md) |
| 17 | SQLite consistency | PASS (isolation/rollback suites herdadas) |
| 18 | Snapshot isolation | PASS (cross-repo tests + expansion) |
| 19 | CLI E2E | PASS (verbatim: search/context/mcp/doctor/manifest) |
| 20 | Linux/Windows CI | ver PR — checks no GitHub |
| 21 | Packaging/wheel | PASS — `python -m build` gerou wheel |
| 22 | Benchmarks | PASS — lf-0045-perf-cycle4.json (n=30) |
| 23 | Regressões 1–3.1 | PASS — 440 incluem todos os suites anteriores |

## Gates G (ciclo)

- G10 instalação/uso autônomos Linux/Windows + config verificável:
  PASS em código+testes; CI cobre o par de OS (item 20).
- G11 benchmark reproduzível + contexto documentado: PASS.
- G12 adversarial aprovada, 0 P0/P1: PASS (budget-clamp era P2,
  corrigido).
- G13 performance medida sem regressão crítica: PASS.
- G14 fluxo CLI/MCP reproduzível+documentado: PASS.

## NOT RUN declarados

- Cliente MCP real (Claude Code/Codex) — ausente no ambiente.
- sqlite-vec em runtime — pacote ausente; bruteforce cobre o caminho.
- Tarefas agenticas ponta-a-ponta — sem provider de IA.
- Repos públicos externos — offline-first, sem rede.

## Bugs reais corrigidos no ciclo

`tuple(f"..",)` iterando chars; dedup por occurrence violando dedup-
por-símbolo; `db_file()` inexistente; budget_tokens sem clamp;
testes legados de "verbos futuros" atualizados ao virar real.

## Readiness Cycle 5

Base pronta: retrieval híbrido medido, packs budgeted, MCP read-only,
eval harness com judgments. Gaps honestos listados em
cycle-4-limitations.md.
