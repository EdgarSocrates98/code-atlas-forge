# Cycle 7 — Limitações e gaps conhecidos

> Inventário honesto de tudo que **não** está provado, é degradação por
> design, ou pendência aberta — estado ao fim do branch
> `cycle-7-domain-intelligence-federation`. Convenção idêntica à do
> 6.1: **[gap]** ausência de feature · **[degradação]** comportamento
> declarado, não erro · **[não medido]** sem evidência de execução ·
> **[bug]** desvio código↔intenção.

## 1. Escala e performance

| # | Item | Estado | Evidência |
|---|---|---|---|
| L1 | 10k+ nós no canvas | **[não medido]** | herdado do 6.1: máximo exercitado segue 2000 renderizados (API `overview` trunca a 2000; canvas capa 500 default/2000 max). O benchmark denso do ciclo (`e2e/graph-dense.spec.ts`) cobre egos de símbolo até 2000 arquivos, não canvas de 10k — `docs/graph-dense-benchmarks.md` |
| L2 | Arestas no canvas em escala | **[não medido]** | o overview emite 0 edges por design; após o fix `a250a39` as rotas `?focus=`/drill alcançam canvas com arestas, mas FPS/memória em grafo denso com arestas não foram re-medidos (spec de benchmark registra a cadeia `canvas_alive_with_edges` pronta para produzir números no re-run) |
| L3 | GPU real | **[não medido]** | todos os fps (incl. RAF-gap 83 ms do FA2 worker) são SwiftShader em headless Chromium — piso pessimista, não número de hardware |
| L4 | Custo de domínio em repos grandes | **[não medido]** | benchmarks de adapter medem fixtures de 4–13 artefatos (2.8–8.6 ms) + um teste sintético de 50 `.sql` (65.9 ms, ~1.3 ms/artefato). Nenhuma medição em repositório com milhares de artefatos de domínio — `docs/cycle-7-adapters.md` §"Measured cost" |

## 2. Browser / acessibilidade

| # | Item | Estado | Evidência |
|---|---|---|---|
| L5 | Firefox / Safari | **[não medido] — NOT RUN** | suite Playwright roda só Chromium (projeto único); WebKit/Firefox nunca executados — herdado do 6.1, inalterado no 7 |
| L6 | Screen reader (NVDA/JAWS/VoiceOver) | **[não medido] — NOT RUN** | axe + teclado não substituem walkthrough com AT; as 5 telas novas de domínio (`/lineage` `/contracts` `/infra` `/pipelines` `/federation`) não tiveram scan axe dedicado neste ciclo |
| L7 | Telas de domínio em `?repo=&snapshot=` | **[parcial]** | o contexto global existe; a restauração de deep-link nas telas novas não foi homologada item a item nesta sessão |

## 3. Plataforma

| # | Item | Estado | Evidência |
|---|---|---|---|
| L8 | Explorer interativo em Windows | **[não medido] — NOT RUN** | pacote Python + CLI cobertos pelos jobs `test (windows-latest, py3.12/3.13)` (pytest+wheel+CLI smoke na CI); nenhuma execução Node/browser em Windows |
| L9 | macOS | **[não medido] — NOT RUN** | sem job macOS no CI; nenhuma execução registrada |
| L10 | `code-atlas explorer` bind/`webbrowser.open` fora de Linux | **[não medido]** | desenho portável por construção, sem evidência fora de Linux |

## 4. Domínio — modelo e cobertura

| # | Item | Estado | Evidência |
|---|---|---|---|
| L11 | FTS de entidades de domínio | **[gap]** | `/domains/entities?q=` usa `LIKE … ESCAPE '\'` parametrizado (`handlers.py:744-748`); não existe `fts_domain_entities` — busca lexical de domínio é substring, não full-text |
| L12 | `BUILDS`/`PUBLISHES` | **[degradação — por design]** | fora do vocabulário fechado de relações (ADR-0010): passos de build/publish viram **atributos** do `cicd_job` (`artifacts_in`/`artifacts_out`) com evidência, nunca arestas — extensão exige spec+ADR (`cicd/adapter.py:17-20,490-506`) |
| L13 | `spark.sql(não-literal)` | **[degradação]** | argumento dinâmico (f-string, variável) vira diagnóstico `dynamic-sql` + alvo `unresolved`, nunca lineage inventada — `spark/sqllit.py` |
| L14 | Integração real AWS/Glue/Lake Formation | **[gap]** | o adapter `aws-data` lê artefatos **declarados** no repo (configs `.json`/`.yaml`/`.conf`, scripts de job, arquivos Athena); **nunca** conecta em boto3/aws CLI/catálogo Glue real — zero rede por construção (`aws/adapter.py:5`) |
| L15 | `edges_skipped` do adapter `api` | **[degradação]** | `web/legacy.js` sem parser JS → 2 arestas descartadas honestamente (a entidade símbolo existe) — medido na eval |
| L16 | `.gitlab-ci.yml` / outras plataformas CI | **[gap]** | globs cobrem GitHub Actions (`.github/workflows/*.yml`); GitLab/Jenkins declarados opcionais e não extraídos — zero entidades dessas plataformas |
| L17 | `ambiguous` cross-domain não-relacional | **[degradação]** | schema não tem coluna de candidatos — os candidatos ficam no texto `to_unresolved` (parseável, não relacional) — `cycle-7-cross-domain-resolution.md` §"Known limits" |
| L18 | Fallback `FILE` para âncoras de path | **[degradação]** | âncora cujo arquivo tem **zero** evidência no snapshot rende diagnóstico, não aresta |

## 5. Federação

| # | Item | Estado | Evidência |
|---|---|---|---|
| L19 | Mutação de workspace por HTTP | **[gap — por design]** | POST/PUT/DELETE sob `/federation/*` não têm rota (404); create/add/pin existem **só na CLI** — `cycle-7-federation.md` |
| L20 | Arestas federadas persistidas | **[degradação — por design]** | `shared_entity` é **computada** na leitura (`computed=true`, `relation_id=null`), nunca gravada em `domain_relations`; re-computada a cada consulta — sem cache de view |
| L21 | `view_id` como chave de cache/invalidação | **[gap]** | o hash identifica a view honestamente (inclui `None`), mas nada o usa para invalidar cache — custo de `resolve_view` pago a cada request |

## 6. Segurança / privacidade

| # | Item | Estado | Evidência |
|---|---|---|---|
| L22 | Adapters de terceiros via entry-points | **[parcial]** | `discover_entrypoints()` existe e é opt-in por chamada, mas **não há adapter externo publicado** — o caminho de descoberta é testado em mocks, não com um pacote real instalado |
| L23 | Secrets em repositórios reais | **[parcial]** | redaction verificada nas fixtures adversariais (`.tf` sensitive + `.tfvars`, `${{ secrets.* }}`) — cobertura de formatos além desses (`.env`, keyfiles) não é testada neste ciclo |
| L24 | Vulnerabilidades npm | **[medido — aceito]** | `npm audit --omit=dev`: 8 (4 moderate, 4 high) — advisories Angular 18 sem vetor endereçável (fetch-only, sem HttpClient/TransferCache); gate CI só bloqueia `critical` — `docs/graph-dense-benchmarks.md` §0.4 |

## 7. Homologação / testes

| # | Item | Estado | Evidência |
|---|---|---|---|
| L25 | Lighthouse | **[não medido] — NOT RUN** | axe (0 violações, ciclo 6.1) cobre auditoria estática; Lighthouse nunca rodou — herdado |
| L26 | Suite `tests/domains/{aws,spark}` WIP | **[declarado]** | eram diretórios untracked WIP durante lf-0118 que quebravam coleta (módulo duplicado `test_adapter_contract`); estado final verde — a observação histórica fica registrada em `cycle-7-federation.md` |
| L27 | 1 teste Python `skip` | **[declarado]** | `tests/api/test_files_occurrences.py` — caso `todo` honesto (inalterado do 6.1) |
| L28 | `GET /impact/{id}` / histórico | **[gap]** | herdado: impacto é POST síncrono efêmero; sem persistência de resultados — nem o blast de domínio tem histórico |

## 8. O que **não** é limitação (esclarecimentos que continuam valendo)

- **Sem execução** — adapters observam artefatos estáticos; jobs, SQL,
  Spark, Terraform, pipelines e código indexado nunca rodam. É o
  contrato, não um TODO.
- **`unresolved`/`ambiguous`/`external`/`dynamic:*` preservados** —
  estados honestos são fatos do grafo, não falhas a esconder.
- **Federação read-only no HTTP** — mutação CLI-only é decisão
  deliberada (handlers rodam em `query_only`).
- **Domínio opt-in** — `domain_registry=None` preserva o índice
  pré-Cycle-7 byte a byte; ausência de linhas de domínio é resposta
  vazia com nota, nunca erro.
- **Sem grafo inteiro / API read-only / sem telemetria** — inalterado
  dos ciclos anteriores (ADR-0009, ADR-0020).

## 9. Resolvidos neste ciclo (para constar — não reabrir)

- Bloco de ~2300 ms do FA2 síncrono → worker assíncrono, **RAF gap máx.
  83 ms** medido (commit `ef57bdb`).
- Crash `Sigma: edge "…" can't be repaint` em qualquer mount com
  arestas + `drillFile` 400 por tipo errado de `occurrence_id` —
  encontrados pelo benchmark denso, corrigidos em `a250a39`.
- `tests/domains/api` WIP quebrando a suíte (10 falhas na Phase 0) —
  adapters consolidados, suite final 1188 passed / 1 skipped.
- Contenção de path em Windows (`is_relative_to` em vez de concat de
  string) — `cycle-7-adapters.md`.
