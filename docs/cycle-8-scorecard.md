# Cycle 8 — Scorecard de production readiness (Phase 18)

Spec: `factory/specs/inbox/lf-0144-cycle8-scorecard-docs.md`
Branch: `cycle-8-production-quality` · HEAD de referência: `d046055`
Versão medida: `code-atlas 1.0.0rc1` · Ambiente: Linux x86_64 · 16 cores ·
Python 3.12.3 · Node v22.23.2.

> **Regra deste documento:** todo número abaixo veio de medição real
> commitada em `docs/cycle-8-*.md` ou `benchmarks/results/*.json`. Onde
> uma fase/verificação não rodou, está escrito **NOT RUN** — a ausência
> entra como desconto na dimensão correspondente. Nada aqui é meta ou
> projeção.

## Rubrica

| Nota | Significado |
|---|---|
| 4 | Medido end-to-end, sem gaps materiais abertos |
| 3 | Medido, com gaps declarados menores ou contidos |
| 2 | Parcial — evidência existe, mas gap material segue aberto |
| 1 | Fraco — pouca evidência ou gaps grandes |
| 0 | Ausente, falhando ou NOT RUN |

## Resumo

| Dimensão | Nota | Leitura em uma linha |
|---|---|---|
| Correctness | **3** | Fabricação zero medida em todos os harnesses; recall e breadth de linguagem são os gaps declarados |
| Performance | **3** | Engine structural 0.125–2.07 ms e API p50 4–24 ms medidos; CLI one-shot e hybrid pagam preço medido e declarado |
| Security | **2** | Hardening server-side real (7 achados corrigidos, 71 testes adversariais); **Angular 18 EOL com XSS alcançável continua aberto** |
| Reliability | **3** | Fail-closed comprovado (axios), isolamento de pin medido, zero vazamento de workers; tetos de escala não exercitados |
| Observability | **3** | `/diagnostics`, `stage_ms`, `--verbose`, slow-query guard shipped e testados; export de métricas/tracing fora de escopo por design |
| DX | **3** | Gates de contribuição re-executados e verdes nesta branch; drift real do quickstart encontrado e corrigido nesta fase |
| Competitive position | **3** | Nicho único medido (grafo+domínio+federação local+packs); perdas reais declaradas (conteúdo, breadth, IDE, escala) |
| Release-readiness | **2** | RC cortado com gate de 14 itens e wheel verificado; **Phase 13 (cross-platform) e Phase 14 (a11y) NOT RUN** e XSS do Explorer aberto |
| **Total** | **22 / 32** | RC sólido e honesto; GA bloqueado por itens abertos listados em §3 |

---

## 1. Correctness — **3/4**

**Evidência medida:**

- Adapters de domínio vs. verdade-terreno rotulada à mão, auditoria
  exaustiva (`unaudited=0`): **precision 1.000 nos 6 domínios**,
  recall `sql`/`pyspark`/`terraform`/`cicd` = 1.000, `aws` = 0.962,
  `api` = 0.953 — `docs/cycle-8-domain-accuracy.md`.
- Blast de impacto vs. corpus rotulado (12 cenários cross-domain):
  **micro-agregado 63 TP / 0 FP / 0 FN — P=R=F1 = 1.000**; 0 leaks
  `possible→verified`, 0 violações de `absent`, 0 erros de evidência em
  86 linhas — `docs/cycle-8-impact-validation.md`.
- `patterns_forbidden`: **0 violações** — nenhum segredo de fixture
  vazou para entidades/arestas/diagnósticos.
- Suite: **1510 passed / 1 skipped** (70.6 s, re-executado nesta fase);
  ruff clean; mypy 167 fontes — gates CONTRIBUTING re-rodados.
- Regressão real encontrada e corrigida no ciclo: axios falhava o
  snapshot inteiro (`import x = require('y')` → aresta vazia); após o
  fix, **503/503 arquivos, status `success`, zero arestas vazias** —
  `docs/cycle-8-parser-compat.md`.
- Golden harness (inventário observado) + ground truth (correção) +
  impact truth (alcance) são três medições independentes — nenhuma
  fabricação medida em nenhuma.

**Descontos honestos:**

- Recall < 1.0 em `aws` (5 FN) e `api` (4 FN) — todos listados um a um;
  déficits de cobertura, não de fabricação.
- Breadth de linguagem é um gap de correção de fato: axios 503
  inventariados / **32 parseados** (`.js` não tem parser — inventário,
  não grafo); jaffle/terraform `parsed=0`. Declarado por design, mas
  limita "correctness" em repos JS-heavy —
  `docs/cycle-8-realworld-benchmark.md`, `docs/cycle-8-competitive.md` §6.
- 1 skip declarado (`test_files_occurrences.py:162`); rótulos de
  ground truth são single-evaluator (mitigado por `complete`/`absent`
  explícitos, declarado).

## 2. Performance — **3/4**

**Evidência medida:**

- Engine in-process (`benchmarks/results/competitive-latest.json`):
  structural **0.125–2.067 ms**, lexical 0.173–2.92 ms p50 — 3–70×
  abaixo do scan rg por query no mesmo corpus.
- API real (`/api/v1`, 25 req pós-warmup): `/graph/overview` p50
  2.35–4.87 ms; `/search` p50 2.3–23.9 ms; daemon competitivo vs rg
  (1.9× mais rápido em jaffle, até 2.6× mais lento em click) —
  `docs/cycle-8-realworld-benchmark.md`, `cycle-8-competitive.md` §4.3.
- Índice: 0.24–3.71 s por repo real (509 arquivos máx.); soma dos 5
  índices completos **6.78 s**; re-index noop **~0.28–0.36 s** —
  medido nos 5 repos.
- SQLite v11: `via_relation` 4.4–6.8 ms → **0.016 ms**; ego `IN (…)`
  2.79→0.08 ms; hidratação 1.0→0.03 ms; `entity_detail` 1.99→0.03 ms —
  `docs/cycle-8-sqlite-domain-search.md`.
- Escala sintética (`bench-index-latest.json`): 100→5000 arquivos =
  0.57→26.54 s; query p50 0.34→12.5 ms; RSS pico 343.6 MB.
- Federação 5 membros: resolve_view 0.19 ms, entity lookup 0.95 ms,
  search ~3.5 ms mediana — `federation-scale-latest.json`.
- Explorer: ready 310–406 ms em 100–5000 arquivos; RAF gap máx 50 ms;
  layout chunked ≤12 ms por fatia; watchdog 29 957 ms (SLA 45 s) —
  `graph-scale-latest.json`, `graph-hardiness-latest.json`.

**Descontos honestos:**

- **CLI one-shot**: boot Python ~171 ms p50 → 14–22× mais lento que um
  scan rg inteiro; não existe break-even em modo CLI
  (`cycle-8-competitive.md` §4.7).
- Estratégia `hybrid` custa 33–85 ms in-process (> scan rg completo) e
  a primeira chamada paga 6–237 ms de embeddings naïve.
- Estágio `domains` é o custo dominante com `--domains all`: click
  1814 ms ≈ 49% do wall; requests 624 ms ≈ 43%.
- `embedding_cache` não commitava via CLI (achado do bench → **fix
  commitado `155e658`**, verificado no log desta branch).
- Escala grande NÃO medida: >509 arquivos reais, >2294 occurrences de
  corpus real, >2000 nós renderizados — NOT RUN.

## 3. Security — **2/4**

**Evidência medida:**

- 7 achados reais corrigidos com probe + teste: 2 high (contenção de
  estático por prefixo — `GET /../static-evil/x.json` retornava dados;
  symlink benigno→`.env` hashed) + 4 medium (secrets em chaves YAML,
  valores com forma de credencial sob chaves benignas, `Content-Length:
  -1` DoS ~30 s, path escape TOCTOU nos adapters) — todos com vetores
  verificados, `docs/cycle-8-security-audit.md`.
- F8 (varredura `import os` em `eval/`) reportado e depois corrigido
  na branch (`e5c0ffb` — `patched_env`).
- **71 testes adversariais** novos, todos verdes; sweeps AST de
  execução insegura re-executados a cada run (0 `eval`/`exec`/
  `subprocess` fora dos 2 gateways autorizados).
- Injection suites: FTS5 `MATCH` (12 vetores, `fts_escape`), `LIKE`
  wildcard (5 vetores), isolamento cross-repo/snapshot, loopback-only
  enforced (`unsafe-bind`), host-header → 403, superfície read-only
  (`query_only`+`mode=ro` live), rate-limit 429.
- Segredos nunca indexados — medido no corpus real: `secrets_skipped`
  requests=7, axios=3.
- Supply chain: SBOM CycloneDX 1140 componentes; pinning auditado
  (runtime `==`, package-lock 1131 pacotes com integrity);
  `pip-audit` rodou — 0 vulns nas 3 deps de runtime; dependabot `npm
  /explorer` adicionado; **zero copyleft** — `docs/cycle-8-supply-chain.md`.

**Descontos honestos (por que não é 3+):**

- **Angular 18.2.13 EOL — 8 vulnerabilidades de produção (4 high),
  classificadas ALCANÇÁVEIS**: o Explorer renderiza strings derivadas
  de repo indexado (canal de entrega XSS real); fix exige major bump
  para Angular ≥20, fora de escopo do ciclo — **aberto, mitigado só
  por CSP + loopback**, `cycle-8-supply-chain.md` §S2,
  `cycle-8-baseline.md` gap 1.
- `npm audit` completo: 55 vulns (2 critical — `piscina`, `tar`,
  classificadas dev-only não-alcançáveis, mas presentes na toolchain).
- `pip` 24.0 da venv de dev com 12 PYSEC — housekeeping, não shipped.
- Rate limiter é memória por-processo; TOCTOU residual documentado;
  detecção de `.env` é por nome — todos declarados no threat model.

## 4. Reliability — **3/4**

**Evidência medida:**

- Fail-closed comprovado em produção de fato: o axios morreu inteiro
  na validação (`graph integrity violated`) — e depois do fix o
  pipeline ganhou **três camadas** (extractor nunca emite aresta
  vazia, `GraphBuilder` dropa com `empty-edge-target`, coordinator
  isola crash por arquivo `engine-crash`) —
  `docs/cycle-8-parser-compat.md`.
- Isolamento de federação sob stress: re-index de A para S2 com query
  pinada em S1 → `pinned_s2_rows: 0`, `view_id_differs: true`; view
  imutável declara `STALE` sem esconder; membro sem READY vira
  `member-no-snapshot` — `docs/cycle-8-federation-scale.md`.
- Lifecycle do Explorer: **0 workers/sigmas vivos** após ×20
  mount/unmount e ×10 nav com layout em voo; watchdog disparou em
  29 957 ms após kill real do worker; fallback chunked completa layout
  de 500 nós — `docs/cycle-8-graph-hardening.md`.
- Publicação transacional: snapshot READY/STALE na mesma tx; falha →
  rollback + snapshot FAILED; noop incremental medido 5/5 repos.
- Truncamento honesto medido: sondas at-cap declaram `truncated` em
  11/11 cenários não-vazios; over-cap não declara em 12/12 —
  `cycle-8-impact-validation.md`.

**Descontos honestos:**

- Retenção de heap ~400 KB/ciclo em remounts medida e **declarada sem
  atribuição** (slack V8 vs leak — heap-snapshot diff NOT RUN).
- Teto de 5000 arestas no canvas NOT RUN (máximo exercitado: 259);
  recusa real de `worker-src` por CSP NOT RUN (unit-covered, não e2e).
- `max_nodes`/`max_depth` no impact truncam **sem** levantar
  `truncated` — limitação de observabilidade registrada.
- `stale` nunca disparado em e2e (coberto em unit); blast repo→repo
  literal não existe por design (declarado, não defeito).

## 5. Observability — **3/4**

**Evidência medida:**

- `GET /api/v1/diagnostics` shipped e exercido: versão, schema v12,
  banco (size/wal/journal), presença real de FTS por `sqlite_master`,
  `analyzer_versions` do snapshot READY, `last_job.stage_ms`, adapters
  com `adapters.error` degradável — `docs/cycle-8-observability.md`.
- `index --verbose`: eventos `stage`/`parse-progress`/`noop` em stderr
  com stdout JSON intacto (travado por teste).
- Slow-query guard `CODE_ATLAS_SLOW_MS` na fronteira de request +
  `search_scoped` (todas as 5 estratégias); linhas saem pelo logger
  com `RedactionFilter` (home paths, tokens, PEM).
- Envelope de erro tipado travado por testes: 400 `bad-param`, 404
  `snapshot-not-found`/`route-not-found`, 500 `internal-error` **sem
  traceback no wire**.
- Telemetria de dev `window.__atlasGraph` (sigma/workers/runs) —
  usada pelo e2e para kill real de worker.

**Descontos honestos:**

- Metrics export / Prometheus / OTLP / tracing: **NOT RUN por design**
  (local-first, sem egress) — conta como ausência de capacidade, não
  como defeito.
- Timing por query individual de SQL: NOT RUN (guard mede fronteira);
  benchmark de latência do `/diagnostics`: NOT RUN (só teste
  funcional <1 s em fixture).

## 6. DX (developer experience) — **3/4**

**Evidência medida nesta fase (comandos re-executados verbatim):**

- `code-atlas --version` → `code-atlas 1.0.0rc1`; `code-atlas doctor`
  → probes python/sqlite/fts5/git/config/data_dir ok — matches
  `docs/install.md`.
- Gates do `CONTRIBUTING.md` todos verdes agora: `ruff check src
  tests` clean; `mypy src` 167 arquivos clean; `pytest -q` **1510
  passed / 1 skipped (70.6 s)**; `python -m build --wheel` →
  `code_atlas_forge-1.0.0rc1-py3-none-any.whl`.
- Fluxo init→index→status→search re-verificado contra
  `tests/golden/corpus/py_core` (o fixture por trás do antigo `demo/`).
- Release: `scripts/release_check.sh` 14 gates documentados +
  `docs/RELEASE-CHECKLIST.md` + `docs/RELEASING.md` (política
  SemVer/PEP440 decidida); `docs/troubleshooting.md` existe.
- `docs/architecture.md` atualizado nesta fase com os módulos
  cycle-7/8 (`domains/`, `federation/`, `eval/`, `api/`, `impact/`,
  `context/`, `mcp`, `observability/`).

**Descontos honestos:**

- Drift real encontrado e corrigido nesta fase: `docs/quickstart.md`
  citava fixture `demo/` **inexistente** e a seção "o que o Cycle 3
  não faz" listava `impact`/`context`/`mcp`/`api` como ausentes —
  todos existem hoje. Corrigido com saídas verbatim novas.
- `prompt_evo_cycle8.md` referenciado nas specs lf-0127+ não existe
  no repo (gap P2 do baseline, segue aberto).
- Superfície documental é extensa (100+ docs); não há índice único —
  `docs/cycle-*` + specs são a navegação de fato.
- Smoke e2e/Explorer exige Node+Playwright — pré-requisitos pesados
  declarados em `docs/RELEASE-CHECKLIST.md` §0.

## 7. Competitive position — **3/4**

**Evidência medida (`docs/cycle-8-competitive.md`):**

- Matriz de capacidades vs. 7 classes de ferramentas — o conjunto
  "grafo tipado + domínio + federação local + context packs + MCP"
  **offline** não tem comparador open-source local.
- Vitórias medidas: engine 3–70× abaixo do rg por query em regime;
  respostas tipadas com `resolution_status`+evidência (252 menções vs
  2 arestas reais de `Session`); linhagem, federação local de 5 repos
  (busca federada p50 175 ms in-process hybrid; domain-facet ~3.5 ms
  mediana no bench de escala), packs sob budget, MCP stdio homologado.
- Auditoria de claims do README: 2 corrigidas (`context --task`
  inexistente → sintaxe real; header de ciclo stale), 1 datada
  ("102 testes Playwright" do 6.1), 1 subdeclarada (strategy `vector`).

**Descontos honestos (a seção §6 do doc é explícita):**

- Sem busca de conteúdo arbitrário — fora de escopo por design, mas é
  perda real vs rg/grep.
- 3 parsers (py/ts/tsx) vs ~25 (ast-grep) / 30+ (semgrep) / dezenas
  (SCIP); sem DSL de padrão AST; sem IDE; sem escala hosted/NL.
- Comparadores pesados **NOT RUN** por ausência de artefato local:
  ast-grep, semgrep, code2flow, ctags, Sourcegraph/Cody/Greptile
  (SaaS), understand (comercial).
- `impact` não re-medido no corpus (clones depth-1) — homologação
  cycle-5.1 citada, não re-medida.
- Teto de escala medido é pequeno: maior corpus real = 2294
  occurrences; canvas trunca em 2000 nós.

## 8. Release-readiness — **2/4**

**Evidência medida:**

- `1.0.0rc1` cortado: `__version__` consistente, CHANGELOG
  `[1.0.0rc1] - 2026-10-25`, política SemVer/PEP440 registrada —
  `docs/RELEASING.md`.
- `scripts/release_check.sh`: **14 gates** executáveis (versão, lint,
  tipos, pytest, build, inventário do wheel, smoke de instalação em
  venv limpa, smoke funcional, npm ci/build/unit/e2e, npm audit,
  pip-audit) — `docs/RELEASE-CHECKLIST.md`.
- Wheel `code_atlas_forge-1.0.0rc1-py3-none-any.whl` construído nesta
  fase; wheel anterior `0.1.0` presente em `dist/`.
- CI de `main` verde no baseline (`5c20177`, 9m53s).
- Licenças limpas (zero copyleft), SBOM gerado, dependabot cobrindo
  npm/pip/github-actions.

**Descontos honestos (por que 2):**

- **Phase 13 (cross-platform, lf-0139): NOT RUN** — nenhum doc de
  homologação Windows/macOS/Linux commitado neste ciclo (CI cobre
  `windows-latest` desde o 6.1, mas a homologação do ciclo não tem
  evidência).
- **Phase 14 (a11y-visual, lf-0140): NOT RUN** — sem doc de axe/
  keyboard/zoom/screenshots commitado (artefatos em progresso na
  working tree, sem medição consolidada).
- **XSS alcançável do Explorer (Angular EOL) aberto** — o checklist
  manual exige "achados P0/P1 de revisão externa: zero abertos" para
  GA; com S2 aberto isso não passa honestamente.
- Nenhuma tag git existe (`git tag -l` vazio) — RC é bump de versão,
  não artefato publicado/taggeado ainda (delivery = lf-0145).
- Cosmético: `pip show` reporta `0.1.0` (metadado de install editável
  stale, declarado); claim Playwright "102 testes" datado.

---

## Registro consolidado NOT RUN / limitações conhecidas

| # | Item | Origem | Estado |
|---|---|---|---|
| 1 | Phase 13 — homologação cross-platform (Windows/macOS interativo) | lf-0139 | **NOT RUN** — sem evidência commitada |
| 2 | Phase 14 — axe/keyboard/zoom/screenshots | lf-0140 | **NOT RUN** — sem evidência commitada |
| 3 | Angular 18.2.13 EOL — 8 vulns de prod (4 high), XSS alcançável | baseline gap 1, supply-chain S2 | **ABERTO** — mitigação CSP+loopback, fix = major upgrade fora do ciclo |
| 4 | npm audit total: 55 vulns (2 critical dev-only `piscina`/`tar`) | supply-chain S3 | declarado dev-only não-alcançável |
| 5 | `.js`/`.cjs` fora dos parsers — inventário, não grafo (axios 32/503) | realworld, competitive §6 | limitação de breadth declarada |
| 6 | `parse_result_cache` warm/incremental não benchmarkado (só cold `--full`) | realworld | NOT RUN |
| 7 | Resolvers opt-in (scip/lsp) não medidos no corpus real | realworld, competitive | NOT RUN |
| 8 | Repos >509 arquivos / >2294 occurrences / >2000 nós renderizados | realworld, competitive | NOT RUN (teto declarado) |
| 9 | AFTSSS/hardware-in-the-loop na federação | federation-scale | NOT RUN (bench in-process) |
| 10 | Recusa real de `worker-src` por CSP; hover em arestas inexistente; heap/ciclo sem atribuição; teto 5000 arestas | graph-hardening | NOT RUN / declarado |
| 11 | `max_nodes`/`max_depth` truncam sem `truncated` | impact-validation | limitação registrada |
| 12 | `agent_quality` (qualidade de resposta LLM) | context-pack-value | **NOT RUN** — sem LLM no repo |
| 13 | Pack tokens = chars/4 estimado | context-pack-value | estimativa declarada |
| 14 | Metrics export / OTLP / tracing; timing por SQL; bench de `/diagnostics` | observability | NOT RUN (design/escopo) |
| 15 | `impact` no corpus depth-1 | competitive | NOT RUN (homologado no 5.1) |
| 16 | `pip` 24.0 venv dev — 12 PYSEC | supply-chain S1 | housekeeping, não shipped |
| 17 | `prompt_evo_cycle8.md` referenciado nas specs não existe | baseline gap 3 | **aberto** |
| 18 | 1 skip de teste (`test_files_occurrences.py:162`) | baseline, pytest | declarado |
| 19 | Deps CommonJS (sigma/graphology) → bailouts de otimização no bundle | baseline gap 2 | declarado |
| 20 | `pip show` = `0.1.0` stale; "102 testes Playwright" datado | competitive §7 | cosmético declarado |
| 21 | Blast literal repo→repo (cross-snapshot) | impact-validation | inexistente por design (isolamento) |
| 22 | Firefox/Safari/screen reader; AWS real (estático por design); mutação de domínio só CLI | herdado cycle-7-limitations | declarado |
| 23 | FTS de termo ultra-comum pode empatar com LIKE; passo BFS de linhagem ~2.2 ms residual | sqlite-domain-search | declarado |

## Fontes de evidência

`docs/cycle-8-baseline.md` · `cycle-8-realworld-benchmark.md` ·
`cycle-8-domain-accuracy.md` · `cycle-8-parser-compat.md` ·
`cycle-8-sqlite-domain-search.md` · `cycle-8-federation-scale.md` ·
`cycle-8-graph-hardening.md` · `cycle-8-impact-validation.md` ·
`cycle-8-context-pack-value.md` · `cycle-8-security-audit.md` ·
`cycle-8-supply-chain.md` · `cycle-8-observability.md` ·
`cycle-8-competitive.md` · `docs/RELEASING.md` ·
`docs/RELEASE-CHECKLIST.md` · `benchmarks/results/{realworld,sqlite,
federation-scale,graph-hardiness,competitive,bench-index,domains,
graph-scale}-latest.json` · gates re-executados nesta fase
(`ruff`, `mypy`, `pytest` 1510, `build`, CLI smoke).
