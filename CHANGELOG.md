# Changelog

Todas as mudanças notáveis deste projeto serão documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

## [1.0.0] - 2026-10-25

Primeiro release estável (GA) do code-atlas-forge. Promoção de
`1.0.0rc1` → `1.0.0` após o ciclo 8.1 fechar todos os gates de GA:
scorecard **31/32** (`docs/ga-readiness-scorecard.md`), zero vulns de
produção, builds reproduzíveis, pipeline OIDC preparado. Tag e
publicação PyPI ficam com o mantenedor via
`.github/workflows/release.yml`.

### Adicionado — Cycle 8.1 (branch `cycle-8.1-ga-readiness`)

- **Explorer embutido no wheel** (lf-0148, Phase 3): o dist Angular é
  empacotado como dados de pacote em `code_atlas/explorer_dist/`
  (`[tool.setuptools.package-data]`, gerado por
  `scripts/build_release.sh` a partir de
  `explorer/dist/explorer/browser/`). `pip install` →
  `code-atlas explorer` serve a SPA completa; sem os assets o wheel sai
  API-only e avisa, como antes. Resolução em runtime:
  `explorer_static_root()` em `api/server.py` — packaged via
  `importlib.resources` primeiro, fallback para o checkout em dev.
  Verificado: `tests/packaging/test_wheel_explorer.py` instala o wheel
  numa venv limpa e faz smoke HTTP real (`/`, `/graph`, `/domains`,
  asset hashed, `/api/v1/health` — todos 200).
- **Builds reproduzíveis** (lf-0148, Phase 4): `scripts/build_release.sh`
  fixa `SOURCE_DATE_EPOCH` (env ou timestamp do HEAD). Wheel
  byte-idêntico entre builds; sdist normalizado (mtimes + gzip header)
  também byte-idêntico. Números medidos em `docs/RELEASING.md`.
- **Migração Angular 18.2.13 → 22.2.3** (lf-0147, Phase 2): 4 steps
  sequenciais `ng update`; TypeScript 5.5.4→6.0.3, zone.js
  0.14.10→0.15.1, builders `@angular/build`; removidos
  `@angular/animations` e `platform-browser-dynamic` (não usados).
  `npm audit --omit=dev`: **8 vulns → 0**. `ng test` 238/238,
  Playwright 179/179. Detalhes: `docs/angular-migration.md`.
- **CSP endurecida** (lf-0147): removido `script-src-attr
  'unsafe-inline'`; adicionados `worker-src 'self'`, `object-src
  'none'`, `base-uri 'self'`, `form-action 'self'`. Produção com
  `optimization.styles.inlineCritical=false` (Beasties emitia inline
  `onload=` incompatível com `script-src 'self'`).
- **Truncamento de impacto explícito** (lf-0151):
  `ImpactResult.truncation_reasons` + probes `+1` distinguem fit
  exato de overflow real; Markdown/CLI/JSON/eval concordam.
- **Integridade de migrações** (lf-0151): versões desconhecidas/futuras
  agora levantam `MigrationError` (antes aceitas silenciosamente);
  checksum por migração aplicada; cadeia v1→v12 lossless testada.
- **Manifesto de superfície pública** (lf-0155): 63 testes fixam 19
  subcomandos CLI, 15 tools MCP, rotas API e superfícies de versão —
  drift de contrato falha alto.
- **Homologação multi-plataforma** (lf-0149): fixes Git Bash
  (venv `Scripts/`+`.exe`, `python3→python→py`, `mktemp`→`$TMPDIR`,
  `sha256sum→shasum`); cross-browser Chromium+Firefox+WebKit 9/9
  (`ATLAS_E2E_XBROWSER=1`); Windows CI 6/6 verde em main.
- **Revisão de segurança final** (lf-0153): 6 findings novos
  corrigidos — injeção ANSI/newline em logs, `ValueError`→500 em
  request-target hostil (agora 400), stat-gate antes de ler assets,
  fingerprint `Server:` reduzido, flood de traceback em RST,
  validação de `SOURCE_DATE_EPOCH`/`VER` no build. 7 findings do
  cycle-8 re-verificados. 147 testes de segurança.
- **SBOM determinístico** (lf-0154): `scripts/generate_sbom.py`
  CycloneDX 1.5 stdlib-only (UUID5, sem timestamps, byte-idêntico);
  564 componentes; gate de licenças PASS (24/24 permissivas);
  freshness travado por `test_sbom_freshness.py`.
- **Jornadas E2E de produto** (lf-0156): 8 jornadas Playwright +
  1 jornada CLI real (`index→search→impact→context→status→doctor`),
  todas verdes.
- **Pipeline de release OIDC** (lf-0158): `.github/workflows/release.yml`
  — PyPI Trusted Publishing, `id-token: write` só no job de publish,
  build separado, checksums verificados, ambiente protegido, nunca
  publica a partir de PR. Setup do mantenedor documentado em
  `docs/release-pipeline-oidc.md`.
- **A11y GA** (lf-0150): re-verificação pós-Angular 22 — axe 48 scans
  0 violações, Lighthouse a11y/best-practices 100, focus-ring pinado
  por teste novo.

## [1.0.0rc1] - 2026-10-25

Primeiro release candidato do code-atlas-forge. Nenhuma tag anterior
existia (`git tag -l` vazio) — este é o primeiro corte de versão do
repositório. Decisão de versionamento: `docs/RELEASING.md`.

### Adicionado — Cycle 8 (branch `cycle-8-production-quality`)

- **Índices SQLite de domínio** (schema v11, lf-0132): 4 índices
  `(snapshot_id, endpoint)` em `domain_relations` + `ANALYZE`
  escopo-domínio no publish. Medido no fixture mid-size (~12.5k
  entidades): `via_relation` virou UNION de probes indexados
  4.4–6.8 ms → **0.016 ms**; ego `IN (…)` 2.79→0.08 ms; hidratação
  `json_each` 1.0→0.03 ms; `entity_detail` 1.99→0.03 ms.
- **FTS5 de domínio** (schema v12, lf-0133): `domain_fts` standalone
  (rowid = ocorrência), `unicode61` com case-fold + remoção de
  diacríticos, coluna `terms` com fragmentos de identificador, BM25
  (name 10 > qname 8 > terms 6 > path 1) dentro do teto de scan.
  Fallback honesto para LIKE bounded (`domain-fts-fallback` /
  `domain-fts-error` declarados); `Store.rebuild_domain_fts` para
  backfill idempotente.
- **Matriz de compatibilidade de dialetos de parser** (lf-0130):
  `import x = require('m')`, `export * from 'm'`, `export =`,
  `require()` CJS, `import()` literal, `namespace`/`declare module`,
  decorators, `.cts`/`.mts`, re-exports — todos extraídos com
  diagnósticos novos (`import-source-missing`,
  `dynamic-import-nonliteral`, `empty-edge-target`, `engine-crash`).
  Nenhuma aresta sai com alvo vazio (guarda no extractor + no
  `GraphBuilder`); crash de engine isolado por arquivo no coordinator.
  Verificado no axios real (`aa54a5e`): **503/503 arquivos, status
  `success`, zero arestas vazias** — antes: snapshot inteiro falhava
  (`graph integrity violated`).
- **Benchmark real-world** (lf-0128): corpus de 5 repos OSS pinados por
  SHA (jaffle_shop, terraform-aws-s3-bucket, requests, click, axios),
  index via subprocesso real + latência de API medida (25 req,
  3 warmups). Resultados verbatim em
  `benchmarks/results/realworld-latest.json`.
- **Acurácia medida dos adapters de domínio** (lf-0129): ground truth
  rotulada à mão, auditoria exaustiva (`unaudited=0`) —
  **precision = 1.0 em todos os 6 domínios, recall ≥ 0.95**
  (aws 0.96, api 0.95, demais 1.0). Pisos fixados em
  `tests/evaluation/test_ground_truth.py`.
- **Graph hardening do Explorer** (lf-0131): fix de worker zumbi por
  layout terminado e de arestas filtradas que seguiam renderizadas;
  `ChunkedFa2Engine` + contrato de degradação do worker
  (`worker-api-unavailable`/`worker-spawn-refused`/`worker-startup-error`
  → fallback honesto); telemetria `window.__atlasGraph`. Medido: 0
  workers/sigmas vivos após 20 ciclos destroy + ×10 nav stress.
- **Baseline audit** (lf-0127): ambiente e gates medidos — pytest
  1188 passed/1 skipped, ruff clean, mypy 163 arquivos, ng test
  219/219, CI verde em `main`. Ambiente e verbatim em
  `docs/cycle-8-baseline.md`.
- **`view_id` de federação formato 2** (lf-0134): a chave do cache de
  view passa a cobrir `identity_key` por membro, digest de
  `analyzer_versions` do snapshot resolvido, resolver e versão de
  schema — antes, re-bind ou bump de adapter sob o mesmo `repo_id` era
  invisível. Escala medida de 5 membros e isolamento de pin
  (`pinned_s2_rows: 0`) em `docs/cycle-8-federation-scale.md`.
- **Validação de impacto vs. verdade-terreno** (lf-0135): corpus
  rotulado de 12 cenários cross-domain — micro-agregado
  **P=R=F1=1.000** (63 TP, 0 FP, 0 FN), 0 leaks
  `possible→verified`, truncamento honesto verificado por sondas.
  `docs/cycle-8-impact-validation.md`.
- **Valor medido de context packs** (lf-0136): 9 tarefas reais, 4
  braços determinísticos — `impact` cobre 56/56 fatos com 1 query;
  pack 75% (grounding, não blast); grep baseline 94.6% mas sem prova.
  `docs/cycle-8-context-pack-value.md`.
- **Security audit** (lf-0137): 7 achados reais corrigidos (2 high —
  contenção de estático por prefixo, alias symlink→`.env`), 71 testes
  adversariais novos, sweeps AST de execução insegura em CI.
  `docs/cycle-8-security-audit.md`.
- **Supply chain** (lf-0138): SBOM CycloneDX 1.5 (`sbom.json`, 1140
  componentes), `pip-audit` limpo nas 3 deps de runtime, pinning
  auditado, dependabot cobrindo `npm /explorer`, zero copyleft.
  `docs/cycle-8-supply-chain.md`.
- **Observabilidade** (lf-0141): `GET /api/v1/diagnostics` (schema,
  FTS real, `last_job.stage_ms`, adapters), `index --verbose` com
  progresso por estágio em stderr, slow-query guard
  `CODE_ATLAS_SLOW_MS`. `docs/cycle-8-observability.md`.
- **Release engineering** (lf-0142): `scripts/release_check.sh` com
  14 gates, política SemVer/PEP440 em `docs/RELEASING.md`, checklist
  em `docs/RELEASE-CHECKLIST.md`, wheel `1.0.0rc1` inventariado.
- **Benchmark competitivo** (lf-0143): matriz vs 7 classes de
  ferramentas + medições reais (engine structural 0.13–2.7 ms; CLI
  one-shot 14–22× mais lento que rg — declarado); auditoria de claims
  do README. `docs/cycle-8-competitive.md`.
- **Scorecard de production readiness** (lf-0144): agregação das
  evidências medidas do ciclo em 8 dimensões — total **22/32**,
  registro NOT RUN consolidado. `docs/cycle-8-scorecard.md`.

### Corrigido — Cycle 8

- `import_require_clause` (TS) emitia `IMPORTS` com `to_text` vazio →
  `validate_graph` (fail-closed) derrubava o snapshot inteiro do axios.
- Resolver Python: `from .api import x` em `__init__.py` agora resolve
  relativo ao pacote (`pkg → pkg.api`).
- `embedding_cache` não persistia via CLI one-shot (`put()` sem
  commit → rollback no close) — achado do competitive bench, fix em
  `155e658`.
- `eval/impact_truth.py` violava o sweep `import os` — env override
  via `patched_env` (`e5c0ffb`).
- Worker zumbi por layout terminado e arestas filtradas renderizando
  no canvas do Explorer (lf-0131) — ver
  `docs/cycle-8-graph-hardening.md`.

### Adicionado — Cycle 7 (branch `cycle-7-domain-intelligence-federation`)

- **Adapters de domínio opt-in** (ADR-0010, schema v9): `sql`,
  `pyspark`, `aws-data`, `api`, `terraform`, `cicd` — extração estática
  stdlib-only, zero dependências novas, nada executado/conectado.
- **Grafo de domínio persistido**: `domain_entities`,
  `domain_entity_occurrences`, `domain_relations` com identidade
  posicional `dom:…` e estados honestos
  (`resolved`/`ambiguous`/`unresolved`/`external`).
- **Resolução cross-domain** (`--cross-domain`): updates + arestas por
  concordância de identidade qualificada, evidência dos dois lados ou
  nada, `ambiguous` nunca promovido.
- **Federação multi-repo** (schema v10): workspaces, pins de snapshot
  READY/STALE por membro, `view_id` determinístico, joins por
  `shared_key`, arestas federadas computadas (não persistidas), HTTP
  read-only — mutação apenas via `code-atlas federation`.
- **Busca**: faceta `domain` no SearchEngine + `FederatedSearchEngine`
  (`GET /search?workspace=`, `/federation/workspaces/{id}/search`).
- **Impacto**: blast radius misto símbolo+domínio no `ImpactEngine`
  com hops incertos rebaixando para `possible`.
- **Context packs de domínio**: seção `domain` aditiva (schema v1) no
  envelope v2 via `DomainContextEngine`.
- **API**: rotas `/domains*` (entities, neighbors, relations, lineage)
  e `/federation/workspaces*` — todas GET read-only.
- **CLI**: `index --domains/--cross-domain`, `domains
  list|entities|entity|lineage`, `federation create|add|pin|view`.
- **MCP**: `atlas_domains`, `atlas_domain_entity`,
  `atlas_domain_lineage`, `atlas_federation_view` (15 tools total).
- **Explorer**: rotas `/lineage`, `/contracts`, `/infra`, `/pipelines`,
  `/federation`; FA2 em Web Worker (RAF gap máx. 83 ms, era ~2300 ms).
- **Avaliação por goldens** (`evaluation/run_eval.py`): 7 cenários,
  inventário exaustivo de arestas + entidades proibidas.
- **Fixes**: crash de repaint de arestas no Sigma e tipo de
  `occurrence_id` no drill-down (encontrados pelo benchmark denso).

### Adicionado — Cycle 6 (branches `cycle-6-angular-explorer` + `cycle-6.1-explorer-hardening`)

- **API HTTP local** `/api/v1` em stdlib, read-only, bind loopback
  (ADR-0009, `src/code_atlas/api/`).
- **Explorer** — workbench Angular 18 (`explorer/`) com 10 rotas lazy:
  grafo Sigma/WebGL bounded com layout congelado, busca híbrida,
  impacto e diff de revisões com banners de análise parcial, context
  packs, central de diagnósticos.
- **`code-atlas explorer`** — serve API + dist compilado (degrada
  honesto para API-only quando o build Angular não existe).
- **Hardening 6.1**: control center de repositórios, contexto global
  com deep links `?repo=&snapshot=`, DTO de aresta consistente, E2E
  real (Playwright contra API + SPA compilada, sem mocks).

### Adicionado

- Scaffold do pacote `code-atlas-forge` (src layout, Python >=3.12, zero deps de runtime).
- CLI `code-atlas` com `--version`, `doctor` e `init` (spec lf-0001).
- Configuração por precedência `--root` > `CODE_ATLAS_*` > XDG/APPDATA.
- Licença Apache-2.0, NOTICE, README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.
- Specs Loop Factory do Cycle 1 em `factory/specs/`.
