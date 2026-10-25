# Cycle 8 — Competitive benchmark (Phase 17)

Spec: `factory/specs/inbox/lf-0143-cycle8-competitive-bench.md`
Runner: `benchmarks/competitive/run_competitive.py`
Resultado completo (verbatim): `benchmarks/results/competitive-latest.json`
Run: `2026-10-25T15:17:27Z` · wall total do suite: **108.3s**
Ambiente: Linux x86_64 · 16 cores · Python 3.12.3 ·
`code-atlas 1.0.0rc1` · `ripgrep 14.1.0` (+simd, +pcre2)

> Este documento existe para ser **derrotável**: todo número aqui veio de
> subprocesso real ou chamada in-process medida, com amostras verbatim no
> JSON. Onde uma ferramenta não está instalada ou a comparação não é
> honesta, está escrito **NOT RUN** — sem número inventado, sem
> equivalência forçada.

## 1. A assimetria que não pode ser escondida

`ripgrep` e `code-atlas` respondem **classes diferentes de pergunta**:

| | `rg "<term>"` | `code-atlas search "<term>"` |
|---|---|---|
| O que é varrido | **conteúdo de arquivo** (bytes) | **índice pré-construído** de símbolos/arquivos (SQLite) |
| Índice necessário | nunca | sim — `code-atlas index` (medido abaixo) |
| Resultado | linhas de texto | entidades tipadas + proveniência |
| Expressividade | regex/PCRE2 sobre qualquer texto | substring em qualified_name + filtros `--language/--kind/--path` + FTS5 + grafo |
| Custo por query | ∝ bytes do corpus, toda vez | ~constante no tamanho do índice |
| Achados que o outro não vê | comentários, strings, docs, `.js` | tipos/arestas/resolution_status |

Portanto as tabelas abaixo colocam os números **lado a lado**, nunca como
"o mesmo número com dois nomes". E medimos **três latências** do Atlas:

- **CLI** (`code-atlas search ... --json`): o que o usuário sente —
  inclui o boot do interpretador (~171 ms medido em `--version`);
- **engine in-process** (`StructuralSearch`/`SearchEngine` sobre
  conexão aberta): o que vale em modo daemon/IDE/MCP;
- **API** (`/api/v1/search` num servidor real): o modo comparável a
  IDE/Sourcegraph, sem boot por query.

Escopo de arquivos (transparência — os três counts divergem por design):

| repo | Atlas inventaria | `rg --files` (default) | `rg --files --hidden` |
|---|---|---|---|
| jaffle_shop | 19 | 17 | 45 |
| terraform-aws-s3-bucket | 144 | 135 | 174 |
| requests | 121 | 102 | 157 |
| click | 178 | 161 | 207 |
| axios | 503 | 479 | 535 |

`rg` default pula dotfiles (perde `.github/` — onde vive a evidência
CI/CD do adapter `cicd`); `rg --hidden` cobre dotfiles **e também o
interior de `.git/`** (ex.: 26 arquivos `.git/*` em jaffle_shop — medido
`rg --files --hidden`); o Atlas poda `.git` via `ATLAS_DEFAULT_IGNORES`
e **recusa segredos** (`SECRET_PATTERNS` — `.pem/.key/.env` viram
`secrets_skipped`, nunca índice). As medições de scan abaixo usam
`rg --hidden` — ou seja, rg varre um escopo **maior** que o inventário
do Atlas; isso favorece rg no custo e torna qualquer vitória do Atlas
mais conservadora, não menos.

## 2. Ferramentas NÃO medidas (NOT RUN — declarado)

| ferramenta | situação | por quê |
|---|---|---|
| ast-grep (`sg`) | NOT RUN | não instalado (`/usr/bin/sg` é o `setgroups` do shadow-utils, não o ast-grep); spec proíbe instalar heavyweight |
| semgrep | NOT RUN | não instalado (CLI + engine pesados) |
| code2flow / pyan / ctags / gtags | NOT RUN | não instalados |
| Sourcegraph (+SCIP), Greptile, Cody | NOT RUN | SaaS/servidor — sem artefato local comparável neste corpus; entram só na matriz qualitativa |
| understand/scitools | NOT RUN | GUI comercial fechado |
| `code-atlas impact` no corpus | NOT RUN | os 5 clones são `--depth 1` (1 commit cada — verificado `git rev-list`); impact exige base+target. Homologado em `docs/cycle-5.1-evidence.md`/`docs/cycle-5.1-impact-integrity.md` — citado, não re-medido |

## 3. Matriz de capacidades (qualitativa — o que está SHIPPED)

✅ = existe e verificável · ◐ = parcial · ❌ = ausente · n/a = não se aplica

| Capacidade | **Code Atlas** | ripgrep | ast-grep | semgrep (--pro) | Sourcegraph (+SCIP) | Cody/Greptile-class SaaS | understand/scitools | code2flow-class |
|---|---|---|---|---|---|---|---|---|
| Local-first / offline | ✅ (deps = tree-sitter; egress zero) | ✅ | ✅ | ◐ (CLI offline; registry/rules puxam rede) | ❌ (servidor; self-host existe) | ❌ | ✅ (desktop) | ✅ |
| Não requer LLM | ✅ | ✅ | ✅ | ✅ | ◐ (busca sim; answers precisam LLM) | ❌ (LLM é o core) | ✅ | ✅ |
| Zero config / zero índice p/ 1ª query | ❌ (index obrigatório) | ✅ | ✅ | ✅ | ❌ | ❌ (upload) | ❌ (build de projeto) | ✅ |
| Busca de conteúdo arbitrário | ❌ **não existe** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Pattern AST estrutural (DSL) | ❌ (só filtros kind/lang/path) | ❌ (regex) | ✅ | ✅ | ◐ (structural search) | ◐ | ◐ | ❌ |
| Grafo tipado persistido | ✅ (SQLite, snapshots, schema v12) | ❌ | ❌ | ◐ | ✅ | ◐ | ✅ | ◐ (call graph só) |
| Resolução semântica | ◐ syntactic + SCIP/LSP opt-in; `unresolved` nunca é maquiado | ❌ | ❌ | ◐ | ✅ **compiler-grade via SCIP — melhor que o nosso** | ◐ | ✅ | ❌ |
| Linguagens com parsing real | ◐ py / ts / tsx **apenas** | n/a (texto — todas) | ~25 | 30+ | dezenas via indexadores SCIP | dezenas | 20+ | ~10 |
| Domain adapters (SQL/PySpark/AWS data/API/Terraform/CI-CD) | ✅ 6 adapters opt-in | ❌ | ❌ | ❌ | ❌ | ◐ (RAG genérico, não tipado) | ◐ (métricas, não entidades) | ❌ |
| Linhagem de dados | ✅ (`domains lineage`, resolved/ambiguous/unresolved honestos) | ❌ | ❌ | ❌ | ❌ | ◐ | ❌ | ❌ |
| Federação multi-repo | ✅ (views + pins por snapshot; CLI-only) | ❌ | ❌ | ❌ | ✅ **o core deles, em escala cloud — maior que o nosso** | ✅ | ❌ | ❌ |
| Análise de impacto entre revisões | ✅ (binding honesto `partial`/`mismatch`/`unavailable`) | ❌ | ❌ | ◐ (diff-aware scan) | ❌ | ◐ (PR review) | ❌ | ❌ |
| Context packs p/ agente (budget de tokens) | ✅ | ❌ | ❌ | ❌ | ◐ (Cody context engine) | ✅ | ❌ | ❌ |
| Servidor p/ agentes (MCP stdio) | ✅ (`code-atlas mcp`, homologado c/ SDK oficial) | ❌ | ◐ (LSP) | ❌ | ◐ | ✅ | ❌ | ❌ |
| Explorer visual | ✅ (Angular 18, bounded 2000 nós) | ❌ | ◐ (playground web) | ◐ (dashboard SaaS) | ✅ maduro | ✅ | ✅ muito maduro | ✅ (é o propósito) |
| Integração IDE | ❌ (MCP ≠ plugin IDE) | ✅ ubíqua | ✅ (VSCode/LSP) | ✅ | ✅ | ✅ | ✅ | ❌ |
| Benchmark público reproduzível | ✅ (`benchmarks/` + este doc) | ✅ | ✅ | ◐ | ◐ | ❌ (claims de marketing) | ❌ | ◐ |
| Privacidade (sem egress por construção) | ✅ (zero telemetria no código — greppável; bind loopback enforced em `api/server.py`) | ✅ | ✅ | ◐ | ❌/self-host | ❌ (código sobe p/ cloud) | ✅ | ✅ |
| Custo | Apache-2.0 | MIT | MIT | free + pago (--pro) | free + enterprise | pago/seat | pago/seat | MIT |

**Leitura honesta da matriz:** ninguém nessa linha faz "grafo tipado +
domínio + federação local + context packs" junto e offline — esse é o
espaço do produto. Mas nas colunas onde perdemos (conteúdo arbitrário,
DSL de padrões AST, breadth de linguagens, IDE, escala cloud), perdemos
**de verdade** — ver §6.

## 4. Medido — custo de índice + latência de query

### 4.1 Setup e incremental (verbatim de `competitive-latest.json`)

`code-atlas index <repo> --full --domains all --json` (workspace
multi-repo compartilhado, `CODE_ATLAS_DATA_DIR`/`CONFIG_DIR` isolados,
`PYTHONHASHSEED=0`); re-index incremental = mesmo pipeline sem `--full`.

| repo | index wall (s) | status | files | parsed | occurrences | relations | domain_entities | incremental | 
|---|---|---|---|---|---|---|---|---|
| jaffle_shop | 0.320 | success | 19 | 0 | 19 | 0 | 2 | **noop=True** · 0.279 s |
| terraform-aws-s3-bucket | 0.577 | success | 144 | 0 | 144 | 0 | 186 | **noop=True** · 0.303 s |
| requests | 1.511 | success | 121 | 37 | 945 | 4 061 | 21 | **noop=True** · 0.298 s |
| click | 3.203 | success | 178 | 91 | 2 294 | 8 338 | 16 | **noop=True** · 0.311 s |
| axios | 1.165 | success | 503 | 32 | 869 | 1 721 | 74 | **noop=True** · 0.361 s |

- Soma dos 5 índices completos: **6.78 s** — uma vez, não por query.
- Incremental noop medido: ~0.28–0.36 s por repo (scan+hash, zero parse)
  — sustenta o claim "indexação incremental" do README.
- jaffle/terraform: `parsed=0` — nada em `.py/.ts` aí; o valor desses
  repos no Atlas está nos **domain entities** (2 e 186 resp.) — honesto:
  sem adapter, o Atlas só vê file-nodes neles.
- axios: 503 inventariados, **32 parseados** — axios é majoritariamente
  `.js`, que **não tem parser** (vai a inventário como file-node). Ver
  §6 — é uma perda real.

`rg` não tem fase de índice — esse custo todo é desvantagem nossa no
começo da sessão. **Startup floor medido:** `rg --version` p50 **2.5 ms**
vs `code-atlas --version` p50 **171 ms** (boot do interpretador Python —
domina toda chamada CLI).

### 4.2 Latência por query — verbatim p50 (15 reps pós-warmup; ms)

| repo · query | `rg -i --hidden --count-matches` (matches) | Atlas **CLI** `search` | Atlas **engine** structural | engine lexical | engine hybrid |
|---|---|---|---|---|---|
| jaffle · `customer` | 7.95 (57) | 170.4 | **0.126** | 0.173 | 2.65 |
| jaffle · `order` | 7.92 (88) | 173.9 | **0.125** | 0.259 | 2.82 |
| terraform · `bucket` | 9.84 (2 627) | 169.9 | **0.277** | 2.92 | 10.00 |
| terraform · `s3` | 9.80 (1 812) | 174.8 | **0.214** | 1.90 | 8.41 |
| requests · `session` | 9.39 (316) | 176.2 | **1.108** | 1.01 | 42.3 |
| requests · `redirect` | 9.06 (303) | 174.6 | **0.952** | 0.91 | 40.0 |
| click · `command` | 9.56 (2 094) | 176.8 | **2.067** | 1.46 | 84.9 |
| click · `Context` | 9.21 (826) | 179.7 | **1.938** | 1.74 | 83.7 |
| axios · `request` | 12.12 (4 776) | 204.4 | **0.829** | 1.20 | 33.0 |
| axios · `Axios` | 13.06 (9 723) | 205.3 | **1.121** | 2.00 | 36.3 |

- `n` retornado pelo Atlas: structural = total real (COUNT); lexical/
  hybrid = itens da página (limit 20). rg = ocorrências de texto —
  unidades diferentes, divulgadas lado a lado.
- Primeira chamada `hybrid` fria (constrói embeddings naïve do
  snapshot): jaffle 6.0 ms · terraform 18.2 ms · requests 108.1 ms ·
  click 236.6 ms · axios 77.8 ms — escala com nº de símbolos.
- `lexical` em jaffle retornou 0 itens com diagnóstico
  `domain-fts-fallback`: FTS5 casa **tokens**, não substring —
  `customer` ≠ `customers`. Comportamento tipado correto, mas é um
  jeito legítimo de o Atlas "não achar" o que `rg` acha. Registrado.

### 4.3 Modo daemon — `/api/v1/search` (servidor real, 25 GETs pós-warmup)

| repo (q) | p50 ms | p95 ms | vs rg scan p50 |
|---|---|---|---|
| jaffle (`customer`) | 4.14 | 4.79 | **Atlas 1.9× mais rápido** |
| terraform (`bucket`) | 9.63 | 11.24 | empate (~1.02× rg) |
| requests (`session`) | 20.7 | 26.9 | rg 2.2× mais rápido |
| click (`command`) | 23.9 | 25.8 | rg 2.6× mais rápido |
| axios (`request`) | 13.7 | 14.7 | empate (~1.1× rg) |

Em modo daemon o Atlas entrega resposta **tipada + rankeada + faceta de
domínio** na mesma ordem de grandeza do scan de conteúdo — ganhando no
menor repo, perdendo ~2× nos maiores. A leitura correta: **com processo
vivo a latência é competitiva; one-shot via CLI, rg ganha sempre** (ver
break-even).

### 4.4 Tarefa "listar definições" — aproximação honesta de AST-search

| repo | `rg` aprox. (regex, linhas casadas) | Atlas `search "" --kind function` (símbolos tipados) |
|---|---|---|
| requests | p50 10.6 ms · **711 linhas** `^\s*(async )?def ` | p50 **1.13 ms** · **181 funções** |
| click | p50 12.9 ms · **1 905 linhas** | p50 **4.85 ms** · **1 374 funções** |
| axios | p50 9.9 ms · **421 linhas** (`--type ts`) | p50 **0.69 ms** · **68 funções** |

O count diverge de propósito: rg conta linhas de texto que casam com o
regex (inclui docstrings/comentários e não distingue método/função);
o Atlas conta **símbolos tipados** `kind=function` (exclui métodos —
`kind` separado; honesto, não "menos"). Em axios só 68 funções `.ts`
existem no índice porque `.js` não é parseado — a query semântica só
existe onde houve extração. Sem equivalência de count — há equivalência
de **tarefa**: para "me mostre as funções", o Atlas responde em ~1–5 ms
com entidades; rg devolve linhas para o humano filtrar.

### 4.5 Tarefa "referências" — precisão, não só latência

| repo · alvo (símbolo com mais arestas REFERENCES/CALLS entrantes) | `rg "\b<nome>\b"` (menções de palavra) | `code-atlas references` (arestas tipadas) |
|---|---|---|
| requests · `src.requests.sessions.Session` | p50 9.7 ms · **252 menções** | engine **0.99 ms** · **2 arestas** `resolved` (CLI 177 ms) |
| click · `tests.test_shell_completion._get_words` | p50 10.4 ms · **70 menções** | engine **2.42 ms** · **69 arestas** `resolved` (CLI 216 ms) |
| axios · `tests.smoke.deno.tests.fetch.smoke.test.run` | p50 10.2 ms · **322 menções** | engine **0.59 ms** · **6 arestas** `resolved` (CLI 174 ms) |
| jaffle / terraform | — | **NOT RUN** — zero arestas resolvidas: sem linguagem parseada, o grafo só tem file-nodes. O Atlas **declara** o vazio em vez de inventar arestas. |

Dois lados da moeda, ambos reais: `Session` tem centenas de menções
textuais mas só **2 arestas CALLS/REFERENCES resolvidas** no snapshot
(a maioria das arestas entrantes dela é `IMPORTS`, fora do filtro
default — `--relation-type IMPORTS` as mostra); `run` em axios tem 322
menções e 6 referências tipadas. O Atlas responde "quem **usa** X" com
`resolution_status` e evidência por aresta; rg responde "onde **aparece
o texto** X". Para refatoração, o primeiro é a resposta; para auditoria
de string solta, o segundo.

### 4.6 Capacidades sem baseline local (medidas onde rodam)

| capacidade | medição | resultado |
|---|---|---|
| `context` pack (focus, budget 2000) | CLI e2e, 5 reps | p50 **191–459 ms** por repo, exit 0 |
| `domains lineage` jaffle (entidade `dom:sql/…customers.sql#0`) | CLI e2e, 5 reps | p50 **173 ms**, upstream inclui `unresolved:{{ ref('stg_payments') }}` — ref que não resolve é **preservado como unresolved**, nunca fingido |
| federação `create` + `add`×5 + `view` | CLI | 5 membros na view, exit 0 |
| busca federada (`session`, hybrid, 5 membros) | in-process, 60 reps | p50 **175 ms** — ~soma dos engines por membro |
| `mcp doctor --json` | CLI | exit 0 — db, schema v12, sqlite_vec=False reportados |
| `search --strategy lexical` CLI (requests) | 3 reps | p50 **221.8 ms** |
| `search --strategy hybrid` CLI (requests) | 3 reps | p50 **300.3 ms** |

### 4.7 Break-even honesto

Custo total de N queries da classe "símbolo" (engine/daemon, não CLI):

```
T_atlas(N) = index_wall + N · engine_p50      T_rg(N) = N · rg_p50
```

| repo | index (s) | engine structural p50 (ms) | rg p50 (ms) | break-even (queries) |
|---|---|---|---|---|
| jaffle_shop | 0.320 | 0.126 | 7.95 | **~41** |
| terraform | 0.577 | 0.277 | 9.84 | **~60** |
| requests | 1.511 | 1.108 | 9.39 | **~182** |
| click | 3.203 | 2.067 | 9.56 | **~427** |
| axios | 1.165 | 0.829 | 12.12 | **~103** |

Em modo CLI, **não existe break-even**: o boot de ~171 ms torna cada
invocação ~14–22× mais lenta que um scan rg completo — para uso
one-shot no terminal, `rg` vence sempre. O Atlas só compensa onde o
processo vive (API/MCP/IDE) ou onde a pergunta é semântica.

## 5. Onde ganhamos (medido)

- **Latência de engine steady-state**: structural **0.13–2.7 ms**,
  lexical **0.17–2.9 ms** in-process — 3–70× abaixo do scan rg por
  query — sobre um corpus onde rg já é extremamente rápido.
- **Respostas tipadas com proveniência**: `resolved/ambiguous/
  unresolved/external` + `evidence_ids` por aresta — rg não tem como
  expressar isso (§4.5: 252 menções vs 2 arestas reais).
- **Capacidades sem comparador local**: linhagem SQL/dbt, federação
  local de 5 repos (175 ms a busca federada), context packs sob budget,
  MCP stdio — **zero baseline open-source local** cobre esse conjunto.
- **Custo de índice baixo o bastante para ser casual**: 0.32–3.2 s por
  repo, noop reindex ~0.3 s.
- **Privacidade por construção, verificável**: nenhum símbolo de
  telemetria em `src/`; `api/server.py` **recusa bind não-loopback**
  (`unsafe-bind`); segredos nem entram no índice.
- **Honestidade de estados**: `unresolved` preservado (lineage dbt,
  binding do impact) — comparáveis promovem por heurística ou calam.

## 6. Onde perdemos (medido + estrutural) — a seção obrigatória

1. **CLI one-shot é ~14–22× mais lento que rg** — o boot Python
   (~171 ms p50) sozinho excede uma query rg inteira (~8–13 ms). Quem
   faz grep pontual no terminal não deve usar `code-atlas search`.
2. **Não buscamos conteúdo** — `search` consulta símbolos/caminhos
   indexados; "ache o texto X" é literalmente fora de escopo. Para
   auditoria de string/comentário/doc, `rg`/`grep` são a ferramenta.
3. **Estratégia `hybrid` é mais lenta que um scan rg completo**:
   33–85 ms in-process em click/axios vs ~9–13 ms de rg — o preço de
   RRF+leg vetorial por query. E a **primeira** chamada hybrid paga
   6–237 ms de build de embeddings naïve (escala com símbolos).
4. **`embedding_cache` nunca persiste via CLI** — *defeito encontrado
   por este bench*: `SqliteEmbeddingCache.put()` faz INSERT sem
   commit; o CLI fecha a conexão sem `commit()` → rollback. Probe:
   `embedding_cache` ficou em **0 linhas** após todas as chamadas CLI
   (hybrid + context) do run. Consequência medida: cada `search
   --strategy hybrid` no CLI repaga o build — em requests, hybrid CLI
   p50 300 ms vs lexical CLI p50 222 ms. Sessões longas (MCP/API)
   mantêm o cache **em memória de conexão**; em close, perde-se.
   *(registrado como finding — `src/` fora do escopo desta spec.)*
5. **Breadth de linguagens**: 3 parsers (py/ts/tsx). axios é
   majoritariamente `.js` → **32/503 arquivos parseados**: num codebase
   JS-heavy a maior parte dos símbolos é invisível. ast-grep ~25
   linguagens, semgrep 30+, Sourcegraph dezenas via SCIP.
6. **Resolução semântica limitada**: resolver syntactic não infere
   receiver; `unresolved` fica unresolved. SCIP do Sourcegraph é
   compiler-grade quando há indexador — melhor que o nosso baseline.
   (Temos `--scip`/`--resolver` opt-in; não medido neste corpus.)
7. **Sem DSL de padrão AST para o usuário**: ast-grep expressa
   `foo($$$)`/metavars e rewrite; nós só filtros `kind/language/path`.
   `rg --multiline`/`semgrep -e` também cobrem buracos nossos.
8. **Sem IDE, sem hosted scale, sem resposta em linguagem natural**:
   Sourcegraph/Cody/Greptile respondem "por quê" em NL com escala de
   cloud; nós exigimos query estruturada e rodamos numa máquina.
9. **Teto de escala medido é pequeno**: maior corpus medido aqui = 2 294
   occurrences (click); canvas/API truncam em 2 000 nós
   (`docs/cycle-6.1-limitations.md`). Sourcegraph indexa monorepos de
   milhões de arquivos — não temos evidência nessa ordem.
10. **Playwright "102 testes" está datado** (ver §7) — o número mudou
    com a reestruturação do suite; a afirmação era verdade no 6.1.
11. **Fraqueza conhecida herdada**: 55 vulns `npm audit` (0 critical em
    prod — `docs/cycle-6.1-dependency-audit.md`) — dívida declarada.

## 7. Auditoria de claims de posicionamento (README + docs)

Critério: cada claim deve estar lastreada em código shipped **e**
evidência medida existente. Status: ✅ verificado · 🔧 corrigido ·
⚠️ parcial/datado.

| Claim (fonte) | Verificação | Status |
|---|---|---|
| "open source, local-first, offline-first; nenhum serviço externo/API key" (`README`) | `pyproject` runtime deps = `tree-sitter`+gramáticas só; nenhum egress em `src/`; `api/server.py` rejeita bind público (`unsafe-bind`) | ✅ |
| "sem telemetria — a coleta está ausente" | `grep -r telemetry\|analytics` em `src/` → zero código de coleta (matches são strings de detecção do adapter `api`) | ✅ |
| "parsing real Tree-sitter, grafo tipado em SQLite" | `parsers/languages/{python,typescript,tsx}.py`, `storage/` schema v12; occurrences/relations medidos por repo | ✅ |
| "CLI + MCP stdio + API `/api/v1` + Explorer" | `mcp doctor` exit 0 (probe); API medida live (§4.3); 14 rotas no `app.routes.ts` | ✅ |
| "seis adapters de domínio opt-in" | `domains list` verbatim: `api, aws-data, cicd, pyspark, sql, terraform` | ✅ |
| "resolução cross-domain + federação multi-repo com pins + busca federada + faceta domain" | `--cross-domain` flag; federação criada live (5 membros); `FederatedSearchEngine` medido p50 175 ms | ✅ |
| "blast radius misto (impact)" | código shipped; **NOT RUN aqui** — corpus depth-1; homologado em cycle-5.1 | ✅ (evidência existe fora deste run) |
| "context packs com budget + expansão" | `code-atlas context` medido por repo (191–459 ms), envelope com `budget`/`est_tokens` | ✅ |
| "cinco telas novas no Explorer (`/lineage /contracts /infra /pipelines /federation`)" | as 5 rotas existem em `explorer/src/app/app.routes.ts` | ✅ |
| "FA2 assíncrono em Web Worker — RAF gap máx 83 ms (era ~2300 ms)" | `explorer/.../fa2.worker.ts` existe; medido em `docs/graph-performance-homologation.md` (83.4/83.3 ms) | ✅ |
| "indexação incremental com cache versionado" | **medido neste bench**: 5/5 repos `noop=True` em ~0.3 s | ✅ |
| "FTS5 snapshot-scoped + RRF k=60 + rerank opt-in" | `lexical.py` bm25 + `hybrid.py` `RRF_K=60`; latências medidas | ✅ |
| "linguagens: py/ts/tsx; demais viram file-nodes" | exato: axios 503 indexados / 32 parseados; jaffle 0 parseados | ✅ |
| "resolvers opt-in SCIP/LSP" | flags `--scip`/`--resolver` existem; não medidos aqui | ✅ |
| `code-atlas context --task "…"` (`README` linha 84) | **`--task` não existe** — argparse rejeita (`task` é positional). Comando documentado falhava verbatim. | 🔧 **corrigido** — README agora usa `context "create_order" …` |
| "Status atual: Cycle 7 (branch cycle-7-…)" | stale nesta branch (cycle-8 + specs arquivados) | 🔧 **corrigido** — header atualizado |
| "102 testes Playwright verdes (6.1)" | verdade no 6.1 (`--list` = 102 em 6 files); **hoje**: 61 chamadas `test(` em 9 spec files — suite reestruturada com loops internos (a11y 29→9, screenshots 36→1, +graph-*). Não é regressão de cobertura conhecida, mas o número citado não corresponde ao suite atual | ⚠️ datado — reportado; atualizar quando o 8.x for homologado |
| `search --strategy lexical\|structural\|graph\|hybrid` (`README` linha 83) | `STRATEGIES` também inclui `vector` — omissão benigna | ⚠️ subdeclarado, sem ação |
| Version strings | `code-atlas --version` = `1.0.0rc1` consistente em todo envelope JSON; `pip show` reporta `0.1.0` — metadado de install editável stale, não drift de código | ⚠️ cosmético (pip metadata) |

## 8. Reproduzir

```bash
# corpus já clonado em benchmarks/.corpus/realworld (clone_corpus.sh)
.venv/bin/python benchmarks/competitive/run_competitive.py
# saída: benchmarks/results/competitive-latest.json (+ tabelas acima)
```

Timeouts por comando (60 s query / 900 s index); nada escrito dentro do
corpus; nenhum código de repo indexado executado; segredos excluídos
pelo scanner antes de hash/inventário (`secrets_skipped` registrado por
repo no JSON).
