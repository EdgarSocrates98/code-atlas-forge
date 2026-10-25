# Changelog

Todas as mudanças notáveis deste projeto serão documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

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

### Corrigido — Cycle 8

- `import_require_clause` (TS) emitia `IMPORTS` com `to_text` vazio →
  `validate_graph` (fail-closed) derrubava o snapshot inteiro do axios.
- Resolver Python: `from .api import x` em `__init__.py` agora resolve
  relativo ao pacote (`pkg → pkg.api`).

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
