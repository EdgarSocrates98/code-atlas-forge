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

## Cycle 3.1 — Semantic Hardening ✅ entregue
> Ver `docs/cycle-3.1-evidence.md`, `docs/cycle-3.1-architecture.md`,
> `docs/cycle-3.1-benchmarks.md`, `docs/semantic-enrichment-pipeline.md`,
> `docs/scip-revision-validation.md`, `docs/security-review-cycle31.md`,
> `docs/semantic-accuracy-report.md`, `docs/graph-fragment-storage.md`.

Sub-ciclo de homologação do Cycle 3 (fora do Blueprint original):
resolvers externos **integrados** ao pipeline (`SemanticEnricher`),
revisão de proveniência SCIP, hardening LSP, instrumentação de
pipeline, modelo de grafo incremental decidido (ADR-0020), CLI
semântico (`--scip`/`--resolver`/`semantic-status`), review de
segurança com zero P0/P1.

**Gate:** G1–G12 com evidência — ver `docs/cycle-3.1-evidence.md`.

## Cycle 4 — Search & Agent Context ✅ entregue

FTS5 snapshot-scoped (ADR-0024), graph retrieval BFS limitado,
hybrid RRF k=60 (ADR-0025), vector opt-in sqlite-vec|naïve
(ADR-0022), context packs com budget+expansão (ADR-0026/27), MCP
stdio stdlib read-only (ADR-0023), eval com judgments verificáveis,
suíte adversarial 0 P0/P1. Evidência: `docs/cycle-4-evidence.md`;
limitações honestas: `docs/cycle-4-limitations.md`.

## Cycle 5 — Impact Engine ✅ entregue (5.1 com hardening)

Escopo realizado: `ImpactEngine` (`src/code_atlas/impact/`) com diff de
símbolos entre snapshots, revision binding com estados honestos
(`exact`/`content_verified`/`partial`/`mismatch`/`unavailable` —
mismatch/unavailable bloqueiam análise semântica), blast radius com
`ImpactPath`, contract changes, test candidates com tag de revisão,
risk assessment em dois eixos (category × uncertainty), CLI
`code-atlas impact`, integração ao MCP e à API `/impact/analyze`.

**Gate:** precision/recall auditável e distinção known/possible —
evidência em `docs/cycle-5-impact-engine.md`,
`docs/cycle-5.1-impact-integrity.md`, `docs/cycle-5.1-evidence.md`;
limitações em `docs/cycle-5.1-limitations.md`.

## Cycle 6 — Explorer ✅ entregue (6.1 com hardening)

Escopo realizado (branches `cycle-6-angular-explorer` +
`cycle-6.1-explorer-hardening`): API HTTP loopback `/api/v1` em stdlib
read-only (ADR-0009, `src/code_atlas/api/`), workbench Angular 18 com
10 rotas lazy (`explorer/`), grafo Sigma/WebGL bounded com layout
congelado (`docs/graph-layout-stability.md`), busca híbrida, impacto e
diff de revisões com banners de análise parcial, context packs, central
de diagnósticos, `code-atlas explorer` empacotando API + dist.

Hardening 6.1 entregue: control center de repositórios
(`docs/repository-control-center.md`), contexto global com deep links
`?repo=&snapshot=` (`docs/global-context-navigation.md`), DTO de aresta
com prova (`evidence_ids`, `confidence_class`, `resolver`,
`to_unresolved` — `docs/graph-evidence-contracts.md`), endpoint real de
drill-down `GET /files/{id}/occurrences`, rate limit 300/min + timeout
30 s + CSP/XFO (`docs/explorer-security-v2.md`), câmera/seleção que
sobrevive à navegação (`GraphStateService`) e correção medida do canvas
congelado pós-relayout. Docs: `docs/cycle-6.1-architecture.md`,
`docs/cycle-6.1-evidence.md`, `docs/cycle-6.1-benchmarks.md`,
`docs/cycle-6.1-limitations.md`, `docs/cycle-6.1-dependency-audit.md`,
`docs/explorer-cross-platform.md`, `docs/explorer-ux-review-v2.md`.

**Gate:** testes acessibilidade/E2E/performance, sem câmera que
desaparece — **PASS com ressalvas**: 102 testes Playwright verdes
(flows + security + a11y + scale + screenshots, job CI `explorer-e2e`),
axe WCAG 2.2 AA 0 violações em 24 scans
(`docs/explorer-accessibility-report.md`), benchmarks reais 100–5000
arquivos (`docs/cycle-6.1-benchmarks.md`). Ressalvas honestas: sem 10k
nós, sem arestas medidas, mobile overflow fixado com re-verificação
pendente, Windows/macOS interativo NOT RUN — inventário em
`docs/cycle-6.1-limitations.md`.

## Cycle 7 — Domain Intelligence & Federation ✅ entregue

> Branch `cycle-7-domain-intelligence-federation`, specs lf-0109–lf-0126.
> Ver `docs/cycle-7-architecture.md`, `docs/cycle-7-homologation.md`,
> `docs/cycle-7-limitations.md`, `docs/cycle-7-evidence.md`.

Escopo realizado: seis adapters de domínio opt-in stdlib-only
(`sql`, `pyspark`, `aws-data`, `api`, `terraform`, `cicd`) com grafo de
domínio snapshot-scoped (schema v9: `domain_entities`,
`domain_entity_occurrences`, `domain_relations`), identidade posicional
`dom:…`, resolução cross-domain por identidade qualificada (opt-in,
mesma transação de publicação), federação multi-repo read-only (schema
v10: workspaces, pins READY/STALE por membro, `view_id` hash, joins por
`shared_key`, arestas `shared_entity` computadas), faceta `domain` +
busca federada no SearchEngine, blast misto símbolo+domínio no Impact
Engine, context packs de domínio, cinco views novas no Explorer
(`/lineage`, `/contracts`, `/infra`, `/pipelines`, `/federation`), FA2
em Web Worker (RAF gap máx. 83 ms, era ~2300 ms), benchmark de grafo
denso que achou — e o ciclo corrigiu — dois bugs P0 de canvas, suíte de
segurança de domínio (segredos, zero rede/exec, contenção de path) e
avaliação por goldens curados com 7/7 cenários casando 100%.

**Gate:** fixtures cross-repo e domínio; independência entre pacotes —
**PASS com ressalvas** (`docs/cycle-7-homologation.md`: G0–G20 = 20
PASS / 1 PARTIAL; Phase 22 = 29 PASS / 2 PARTIAL). Verificado:
pytest 1188 passed / 1 skipped, ruff limpo, mypy strict 163 arquivos,
`ng test` 219, e2e verdes. Ressalvas honestas em
`docs/cycle-7-limitations.md`: sem 10k nós medidos, sem Firefox/Safari/
screen reader/Windows interativo, sem FTS de domínio (LIKE), mutação
de domínio só via CLI, arestas cross-repo computadas-não-persistidas,
sem integração AWS real (estático por design).

## Cycle 8.1 — GA Readiness ✅ (1.0.0)

> Branch `cycle-8.1-ga-readiness`, specs lf-0146–lf-0161. Versão
> `1.0.0` (GA — `docs/RELEASING.md`, tag/publish pelo mantenedor via
> OIDC). Scorecard: `docs/ga-readiness-scorecard.md` (**31/32**).

Escopo realizado (evidência medida commitada por spec): baseline
(lf-0146), **Angular 18→22.2.3 + CSP estrita** (lf-0147 — audit prod
8→0), Explorer embutido no wheel + builds byte-idênticos (lf-0148),
homologação Linux/Windows/Git-Bash + cross-browser 9/9 (lf-0149),
a11y GA axe 48×0 + Lighthouse 100/100 (lf-0150), truncamento de
impacto explícito + cadeia de migrações fail-safe (lf-0151), densidade
de grafo 5k@60FPS + sessão longa sem leak (lf-0152), security final
6 findings corrigidos (lf-0153), SBOM determinístico + license gate
(lf-0154), manifesto de superfície pública 63 testes (lf-0155),
jornadas E2E 8+1 (lf-0156), gates de release (lf-0157), pipeline OIDC
trusted publishing (lf-0158), versioning GA (lf-0159), docs/DX
(lf-0160), delivery (lf-0161).

## Cycle 8 — Production Quality ✅ (RC cortado, merged `d5d620a`)

> Branch `cycle-8-production-quality`, specs lf-0127–lf-0145. Versão
> `1.0.0rc1` (primeiro corte do repo — `docs/RELEASING.md`).
> Scorecard consolidado: `docs/cycle-8-scorecard.md` (**22/32**).

Escopo realizado (todas as fases com evidência medida commitada):
baseline audit (lf-0127), benchmark real-world de 5 repos OSS
(lf-0128), acurácia de adapters vs ground truth — precision 1.0 nos 6
(lf-0129), compat de dialetos de parser + fix do P1 axios
(lf-0130), hardening do grafo/worker do Explorer (lf-0131), índices
SQLite v11 + FTS5 de domínio v12 (lf-0132/0133), `view_id` v2 + escala
de federação 5 membros (lf-0134), validação de impacto vs corpus
rotulado P/R/F1=1.0 (lf-0135), valor medido de context packs
(lf-0136), security audit com 7 achados corrigidos + 71 testes
adversariais (lf-0137), supply chain: SBOM CycloneDX + audits +
dependabot npm (lf-0138), observabilidade `/api/v1/diagnostics` +
`--verbose` + slow-guard (lf-0141), release eng: RC + gate de 14 itens
(lf-0142), benchmark competitivo honesto vs rg/ferramentas
(lf-0143), scorecard + DX docs (lf-0144).

**Gate:** releases reproduzíveis, roadmap/documentação e revisão
externa de achados — **PARCIAL**. Reproduzibilidade entregue
(`scripts/release_check.sh`, wheel inventariado, clean-venv smoke,
SBOM); docs/roadmap atualizados. Ressalvas honestas consolidadas no
scorecard: **Phases 13 (cross-platform) e 14 (a11y-visual) NOT RUN**;
Angular 18 EOL com 8 vulns de prod (XSS alcançável) aberto e fora do
escopo do ciclo; sem tag git ainda (delivery = lf-0145).
