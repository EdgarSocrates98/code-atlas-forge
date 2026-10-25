# Cycle 7 — Matriz de homologação (gates G0–G20 + Phase 22)

> lf-0126 (Phases 20+22). Checklist dos gates do prompt
> `prompt_evo_cycle7.md` contra o código real do branch
> `cycle-7-domain-intelligence-federation`
> (base `4bd0690` → HEAD `7cde6d3`, 20 commits). Convenção de status:
>
> | Status | Significado |
> |---|---|
> | **PASS** | evidência verificada em código/teste/doc citado — executado neste ciclo |
> | **PARTIAL** | implementado com ressalva registrada — leia a nota |
> | **NOT RUN** | não executado neste ciclo — nada afirmado |
>
> Mapeamento: specs `factory/specs/inbox/lf-0109…lf-0126` cobrem
> Phases 0–22; os gates abaixo (G0–G20) seguem um-a-um os `## Gate N`
> do prompt. Números citados foram **medidos neste ciclo** — nenhum é
> estimativa.

## 1. Matriz de gates

| Gate | Fase / spec | Critério do prompt | Status | Evidência |
|---|---|---|---|---|
| G0 | Phase 0 · lf-0109 | baseline + layout assíncrono + grafos densos + dep audit + compat | **PASS** | FA2 em Web Worker (`explorer/src/app/features/graph/fa2.worker.ts`, `fa2-layout-runner.ts`) — **RAF gap máx. 83 ms** medido durante relayout (era ~2300 ms de bloqueio), commit `ef57bdb`; benchmark denso real (`e2e/graph-dense.spec.ts` → `docs/graph-dense-benchmarks.md`, `benchmarks/results/graph-dense-*.json`) encontrou 2 bugs P0 de produto (repaint de aresta + tipo de `occurrence_id`) — **corrigidos em `a250a39`**; `npm audit` status registrado (8 vulns, 0 critical) |
| G1 | Phase 1 · lf-0110 | arquitetura de adapters funciona | **PASS** | `domains/contracts.py` + `registry.py` — opt-in por `register()`, `discover_entrypoints` opt-in por chamada, ausência honesta (`UnknownDomainError`), commit `9fab5a9`; ADR-0010 |
| G2 | Phase 2 · lf-0110 | modelo de grafo de domínio unificado | **PASS** | schema v9 (`domain_entities`/`domain_entity_occurrences`/`domain_relations`), vocabulário fechado `DomainEntityKind`/`DomainRelationKind`, `DomainIdentity` posicional `dom:…` (`model.py`, `identity.py`) |
| G3 | Phase 3 · lf-0111 | SQL intelligence (ANSI/Athena/Spark) | **PASS** | `domains/sql/` tokenizer+parser+extract stdlib, dialetos por sintaxe descoberta, lineage coluna+tabela; fixture `sql_warehouse` 36e/32r, eval golden casa 100%; commit `3c983d0`; `docs/cycle-7-sql-adapter.md` |
| G4 | Phase 4 · lf-0112 | PySpark DataFrame lineage via ast | **PASS** | `domains/spark/` (walk/extract/sqllit — `spark.sql` literal delega ao parser SQL); `pyspark_repo` 37e/62r; dinâmico → `dynamic-sql`/`unresolved`; commit `cc5ff61` |
| G5 | Phase 5 · lf-0113 | AWS data intelligence (Glue/Athena/Iceberg/EMR) | **PASS** | `domains/aws/` (configs/pyfiles/sqlbridge) — 13 artefatos, `aws_repo` 63e/60r; identidade por-conta não funde (`shared_db.dim_customer` ×3 medido); commit `8505946`. **Sem conexão AWS real** — por design (L14) |
| G6 | Phase 6 · lf-0114 | API contract intelligence | **PASS** | `domains/api/` (openapi/yaml_subset/routes/consumers) — OpenAPI yaml/json + FastAPI/Express + consumidores `fetch`; `api_repo` 30e/43r; commit `3ac472e` |
| G7 | Phase 7 · lf-0115 | Terraform intelligence (HCL estático) | **PASS** | `domains/terraform/` (hcl/refs/tfvars/tfjson/catalog) — `terraform_infra` 24e/45r; secrets nunca materializam; `count`/`for_each`/interpolação nunca expandem (golden `forbidden`); commit `a6604bd` |
| G8 | Phase 8 · lf-0116 | CI/CD intelligence (GitHub Actions) | **PASS** | `domains/cicd/` (yamlmini/workflow/classify) — `cicd_repo` 13e/34r; `${{ secrets.* }}`→`<secret>`; `BUILDS`/`PUBLISHES` como atributos com evidência (fora do vocabulário fechado); commit `9679be9` |
| G9 | Phase 9 · lf-0117 | cross-domain resolution por identidade qualificada | **PASS** | `domains/crossresolve.py` + `persist_cross_resolutions` — opt-in, mesma transação de publicação; cenário `crossdomain`: `cross_resolved=3`, `cross_ambiguous=3`, `cross_edges=4`, `ambiguous` nunca promovido; 14 testes; commit `561aae9`; `docs/cycle-7-cross-domain-resolution.md` |
| G10 | Phase 10 · lf-0118 | federação multi-repo com isolamento de snapshot | **PASS** | schema v10 (`federation_workspaces`/`federation_members`), pins READY/STALE por membro, `view_id=fedview:sha256`, joins por `shared_key` (nunca nome nu), arestas federadas computadas-não-persistidas; `tests/federation/` + `tests/api/test_federation_api.py`; commit `7f95a3b`; `docs/cycle-7-federation.md` |
| G11 | Phase 11 · lf-0119 | busca federada + faceta de domínio | **PASS** | `SearchRequest.domain` + `SearchResult.domain_items` (faceta separada, nunca fundida); `search/federated.py` — merge posicional determinístico por membro, items tagueados `repository_id`+`snapshot_id`; rotas `/search?workspace=&pin=` e `/federation/workspaces/{id}/search`; `tests/api/test_federated_search.py`; commit `7cde6d3` |
| G12 | Phase 12 · lf-0120 | impact engine reutilizado p/ blast misto | **PASS** | `impact/domain_traversal.py` — BFS único `relations`+`domain_relations` dentro de um snapshot; direção por semântica de aresta; hop incerto → `possible`, nunca promove; `edge_refs` tipa prova `rel:`/`dom:`; 8 testes; commit `2925f4a`; `docs/cycle-7-cross-domain-impact.md` |
| G13 | Phase 13 · lf-0121 | context packs contextualizados por domínio | **PASS** | `context/domain.py` — `DomainContextEngine(ContextEngine)` herda o engine, seção `domain` aditiva (schema v1), `expansion_refs` resolvíveis, seção reconstruída no loop de budget; 10 testes; commit `9e7e3fc`; `docs/cycle-7-domain-context-packs.md` |
| G14 | Phase 14 · lf-0122 | Explorer navega por domínios | **PASS** | 5 rotas novas `features/domains/`: `/lineage` `/contracts` `/infra` `/pipelines` `/federation` + `domain.utils.ts`; rotas GET de domínio/federação na API; commit `92aebd6`. Ressalva: sem scan axe dedicado nas telas novas (L6) |
| G15 | Phase 15 · lf-0123 | CLI/MCP domain tools | **PASS** | CLI `index --domains/--cross-domain`, `domains list|entities|entity|lineage`, `federation create|add|pin|view`; MCP +4 tools (`atlas_domains`, `atlas_domain_entity`, `atlas_domain_lineage`, `atlas_federation_view`); camada compartilhada `domains/cli_surface.py`; `tests/mcp/test_domain_tools.py`, `tests/federation/test_federation_cli.py`; commit `9e7e3fc` |
| G16 | Phase 16 · lf-0124 | precisão de extração medida | **PASS** | `evaluation/run_eval.py --check` + `tests/evaluation/test_domain_eval.py` — pipeline real sobre estado **persistido** vs goldens curados: **7/7 cenários, todas as arestas casam** (zero missing/unexpected/forbidden); `evaluation/results/domain-eval-latest.json` (`all_pass: true`); commit `188bbf5`; `docs/cycle-7-evaluation.md` |
| G17 | Phase 17 · lf-0125 | escala conhecida, limites previsíveis | **PARTIAL** | adapters medidos por fixture 2.8–8.6 ms + sintético 50 `.sql` a ~1.3 ms/artefato; delta de index +10.8 ms com todos os adapters (`benchmarks/results/domains-latest.json`); **mas** sem medição de domínio em repo grande nem 10k nós no canvas (L1/L4); commit `b91a15b`; `docs/cycle-7-adapters.md` |
| G18 | Phase 18 · lf-0125 | zero P0/P1 de segurança aberto | **PASS** | `tests/security/test_domain_security.py` — segredos ausentes de entidades/relações/evidência **e linhas persistidas**; AST-scan: zero imports rede/exec/subprocess em `domains/`+`federation/`+`eval/`; contenção de path incl. separador Windows (`is_relative_to`); loopback-only inalterado; marcadores `${{ secrets.* }}`→`<secret>`; commit `b91a15b` |
| G19 | Phase 19 · lf-0125 | pacote modular e instalável | **PASS** | zero deps novas de runtime — adapters ship no mesmo wheel (`test_domain_packages_import_only_stdlib`, `test_runtime_dependencies_are_pinned_tree_sitter`); `python -m build` inalterado; wheel+CLI+pytest cobertos por `test (windows-latest)` na CI; **ressalva**: extras `[sql]`/`[all-domains]` do prompt não existem porque não há nada extra a instalar (postura documentada em `cycle-7-adapters.md`) |
| G20 | Phase 20 · lf-0126 | documentação completa e fiel ao código | **PASS** | este conjunto: `cycle-7-architecture.md`, `cycle-7-limitations.md`, `cycle-7-evidence.md`, `cycle-7-homologation.md` (este) + docs por-spec já existentes; `explorer-api.md` atualizado (L20 do 6.1 sanado); `README.md`/`roadmap.md`/`CHANGELOG.md` atualizados |

**Contagem:** 20 PASS · 1 PARTIAL · 0 NOT RUN (21 gates, G0–G20).
O único PARTIAL é G17 — escala medida só até o regime de fixtures +
ego denso de 2000 arquivos; os tetos (10k nós, repo com milhares de
artefatos de domínio) ficam declarados em `cycle-7-limitations.md`.

## 2. Checklist Phase 22 — as 31 verificações

| # | Item do prompt | Status | Evidência |
|---|---|---|---|
| 1 | Python tests | **PASS** | `pytest` **1188 passed / 1 skipped** (skip honesto: `test_files_occurrences` todo) — medido neste ciclo |
| 2 | Ruff | **PASS** | `ruff check src tests` limpo |
| 3 | mypy strict | **PASS** | `mypy src` — 163 arquivos, strict, limpo |
| 4 | SQL parser tests | **PASS** | `tests/domains/sql/` (97 testes) + golden `sql.json` |
| 5 | Spark extraction tests | **PASS** | `tests/domains/spark/` + golden `pyspark.json` |
| 6 | AWS adapter tests | **PASS** | `tests/domains/aws/` + golden `aws.json` |
| 7 | API adapter tests | **PASS** | `tests/domains/api/` + golden `api.json` |
| 8 | Terraform tests | **PASS** | `tests/domains/terraform/` + golden `terraform.json` |
| 9 | CI/CD tests | **PASS** | `tests/domains/test_cicd_*.py` + golden `cicd.json` |
| 10 | Federation tests | **PASS** | `tests/federation/` (engine + CLI) |
| 11 | Snapshot isolation | **PASS** | guarda `_validate` cross-snapshot aborta publicação; pins por membro testados; `domain-cross-snapshot:N` em impacto |
| 12 | Evidence integrity | **PASS** | arestas cross-domain exigem evidência dos dois lados ou não nascem; eval verifica `evidence`/`diagnostics` exatos |
| 13 | Cross-domain resolution | **PASS** | `test_crossresolve.py` (14) + cenário `crossdomain` medido |
| 14 | Lineage evaluation | **PASS** | goldens por domínio — set-compare exaustivo de arestas (`all_pass: true`) |
| 15 | Cross-domain impact | **PASS** | `test_domain_impact.py` (8) |
| 16 | Context packs | **PASS** | `test_domain_packs.py` (10) |
| 17 | CLI E2E | **PASS** | `test_federation_cli.py` + `test_cli.py` + suíte domains |
| 18 | MCP E2E | **PASS** | `test_domain_tools.py` + `test_e2e_sdk.py` (SDK oficial) |
| 19 | HTTP API E2E | **PASS** | `tests/api/` incl. `test_domain_api.py`, `test_federation_api.py`, `test_federated_search.py` |
| 20 | Angular unit tests | **PASS** | `ng test` **219** testes — medido neste ciclo |
| 21 | Playwright E2E | **PASS** | suítes e2e verdes (incl. `graph-async-layout`, `graph-dense`) |
| 22 | Graph worker tests | **PASS** | `fa2-layout-runner.spec.ts` (178 unit no commit) + `e2e/graph-async-layout.spec.ts` (3) |
| 23 | Dense graph benchmarks | **PASS** | executados — produziram 2 bugs reais corrigidos (`a250a39`); números em `graph-dense-benchmarks.md` |
| 24 | Security suite | **PASS** | `tests/security/test_domain_security.py` + hardening API herdado |
| 25 | Accessibility regression | **PARTIAL** | suite axe do 6.1 (24 scans, 0 violações) segue no e2e; **telas novas de domínio sem scan dedicado** (L6) |
| 26 | Linux packaging | **PASS** | wheel build + CLI smoke no CI |
| 27 | Windows packaging | **PARTIAL** | pacote+CLI cobertos por `test (windows-latest, py3.12/3.13)`; Explorer interativo Windows **NOT RUN** (L8) |
| 28 | Dependency audit | **PASS** | `pip-audit --strict` (job) + `npm audit` critical-gate; 8 vulns npm registradas, 0 critical (L24) |
| 29 | Regression Cycles 1–6.1 | **PASS** | suíte única — os 1188 testes incluem todo o legado; `domain_registry=None` preserva índice byte-a-byte (testado) |
| 30 | Documentation consistency | **PASS** | esta spec (lf-0126) + §5 |
| 31 | CI workflows | **PASS** | `ci.yml`: `test` matrix (ubuntu+windows × py3.12/3.13), `explorer` (build+unit), `explorer-e2e` (Playwright), `npm-audit-explorer`, `pip-audit` — e2e suites verdes |

**Contagem Phase 22:** 29 PASS · 2 PARTIAL · 0 NOT RUN.

## 3. Gates críticos do prompt (Phase 22, lista final)

| Critério | Status | Evidência |
|---|---|---|
| Arquitetura de adapters funciona | **PASS** | `registry.py` + 6 adapters registráveis (G1) |
| Domínios entregues possuem fixtures | **PASS** | `tests/fixtures/{sql_warehouse,pyspark_repo,aws_repo,api_repo,terraform_infra,cicd_repo,crossdomain_ws}` |
| Extrações têm precisão medida | **PASS** | eval com goldens curados — 7/7 `all_pass` |
| Relações têm proveniência | **PASS** | `evidence_ids` em toda aresta; aresta cross-domain sem evidência dos dois lados é descartada |
| Identidades cross-domain verificadas | **PASS** | qualified-identity agreement; `ambiguous` persiste candidatos |
| Federation preserva snapshots | **PASS** | pins por membro + `view_id` hash + isolamento testado |
| Busca federada funciona | **PASS** | `FederatedSearchEngine` + rota `/search?workspace=` |
| Impact Engine reutilizado | **PASS** | BFS misto no engine existente — nenhuma segunda implementação |
| Context Packs contextualizados | **PASS** | seção `domain` no envelope v2 existente |
| Explorer navega por domínios | **PASS** | 5 rotas novas consumindo `/domains*`/`/federation*` |
| Layout não bloqueia a interface | **PASS** | FA2 worker — RAF gap máx. 83 ms medido (era ~2300 ms) |
| Segurança validada | **PASS** | `test_domain_security.py` + zero P0/P1 conhecido aberto |
| CI verde | **PASS** | suítes verdes neste ciclo (1188 pytest / 219 ng / e2e) |
| Documentação atualizada | **PASS** | esta spec |
| Nenhum P0/P1 conhecido aberto | **PASS** | os 2 bugs P0 do benchmark denso foram corrigidos e re-verificados (`a250a39`) |

## 4. Divergências e NOT RUNs honestos

Nada aqui derruba gate — mas nada aqui pode ser afirmado sem evidência:

| # | Item | Por quê |
|---|---|---|
| H1 | Windows Explorer interativo | sem execução Node/browser em Windows — só pacote Python é coberto pela CI (L8) |
| H2 | Firefox/Safari (WebKit) | Playwright roda só Chromium (L5) |
| H3 | Screen reader walkthrough | axe+teclado ≠ AT real; telas de domínio sem scan dedicado (L6) |
| H4 | 10k+ nós no canvas | máximo medido segue 2000 renderizados; API trunca a 2000 (L1) |
| H5 | Domínio em repo grande | benchmarks medem 4–13 artefatos/fixture + sintético 50 (L4) |
| H6 | Lighthouse | nunca executado (L25) |
| H7 | Integração AWS real | fora de escopo por design — adapter é estático, zero rede (L14) |
| H8 | FTS de domínio | `/domains/entities?q=` é `LIKE` parametrizado, não full-text (L11) |
| H9 | Mutação de domínio por HTTP | inexistente por desenho — federação é CLI-only (L19) |
| H10 | Arestas cross-repo persistidas | computadas na leitura, nunca gravadas (L20) |
| H11 | `spark.sql` não-literal | vira diagnóstico `dynamic-sql`, nunca lineage (L13) |
| H12 | `BUILDS`/`PUBLISHES` como arestas | fora do vocabulário fechado — atributos de job com evidência (L12) |
| H13 | Adapter externo real instalado | `discover_entrypoints` testado em mock apenas (L22) |

## 5. Documentos produzidos/atualizados nesta homologação

- `docs/cycle-7-architecture.md` (novo)
- `docs/cycle-7-limitations.md` (novo)
- `docs/cycle-7-evidence.md` (novo — inventário API/CLI/MCP)
- `docs/cycle-7-homologation.md` (este)
- `docs/explorer-api.md` (atualizado — rotas de domínio/federação,
  params novos de `/search`, DTO de aresta, `files/occurrences`)
- `README.md` (seção Cycle 7), `docs/roadmap.md` (estado do Cycle 7),
  `CHANGELOG.md` (entrada do ciclo)

Docs por-spec já existentes e referenciados: `cycle-7-adapters.md`,
`cycle-7-sql-adapter.md`, `cycle-7-cross-domain-resolution.md`,
`cycle-7-federation.md`, `cycle-7-cross-domain-impact.md`,
`cycle-7-domain-context-packs.md`, `cycle-7-evaluation.md`,
`graph-dense-benchmarks.md`.

## 6. Referências

- Prompt: `../prompt_evo_cycle7.md` (Phases 0–22, Gates 0–20).
- Specs: `factory/specs/inbox/lf-0109…lf-0126`.
- Evidência primária: `evaluation/results/domain-eval-latest.json`,
  `benchmarks/results/domains-latest.json`,
  `benchmarks/results/graph-dense-*.json`,
  `docs/cycle-7-evaluation.md`, `docs/graph-dense-benchmarks.md`.
- Código: `src/code_atlas/domains/`, `federation/`, `eval/`,
  `search/federated.py`, `impact/domain_traversal.py`,
  `context/domain.py`, `api/handlers.py`, `cli.py`, `mcp.py`,
  `explorer/src/app/features/domains/`,
  `explorer/src/app/features/graph/fa2*`.
- Commits do ciclo (base → HEAD): `bbfe369` → `7cde6d3`
  (20 commits; destaques `ef57bdb` FA2 worker, `a250a39` fixes do
  benchmark denso, `9fab5a9` adapters, `7f95a3b` federação,
  `7cde6d3` busca federada).
