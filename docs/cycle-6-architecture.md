# Cycle 6 — Arquitetura do Explorer

> Status: branch `cycle-6-angular-explorer`, spec lf-0095 (Phases 19–22).
> Tudo abaixo foi verificado contra os arquivos citados. Onde a evidência
> não existe no repositório, o item está marcado **[não verificado]**.

## 1. Visão geral

```
┌─────────────────────────── processo único, loopback ───────────────────────────┐
│  code-atlas explorer                                                           │
│                                                                                │
│  ThreadingHTTPServer (stdlib)                                                  │
│  ├── GET /*  (fora de /api/v1) → assets de explorer/dist (SPA fallback)        │
│  └── /api/v1/* → handlers.py → engines de domínio → SQLite atlas.db (ro)      │
└────────────────────────────────────────────────────────────────────────────────┘
            ▲                                           │
            │ fetch() same-origin (ou 127.0.0.1:8741)    │ sqlite3, mode=ro
            │                                           ▼
     Angular 18 SPA (compilado, servido          <data_dir>/atlas.db
     estaticamente pelo mesmo processo)          snapshots, símbolos,
     explorer/src/app/**                         relations, evidence
```

Dois fatos estruturais:

- **Um processo só.** `run_explorer` em `src/code_atlas/cli.py` (linhas
  852–889) sobe `code_atlas.api.server.serve()` com `static_root`
  apontando para `explorer/dist/explorer/browser` (fallback:
  `explorer/dist`). Se a dist não existir, serve **apenas a API** e avisa
  em stderr — nunca finge servir UI.
- **A API nunca escreve.** `_AtlasHttpd.connect()` (`server.py:195-198`)
  abre `connect(path, read_only=True)` — URI `mode=ro` em
  `storage/db.py:22-24` — e ainda aplica `PRAGMA query_only = ON`.
  Defesa em profundidade: mesmo que um handler tentasse escrever, o
  SQLite recusaria.

## 2. A fronteira `api/` (ADR-0009)

`docs/adr/adr-0009-local-api-stdlib.md` registra a decisão (lf-0086):
`http.server` + `ThreadingHTTPServer` + `BaseHTTPRequestHandler` em vez
de FastAPI/Starlette. Motivos: zero dependências novas (regra do repo),
superfície pequena e auditável, distribuição mínima. Alternativas
rejeitadas no próprio ADR: FastAPI (dep nova), MCP-over-HTTP (reuso
improvisado proibido), unix socket (Angular não fala).

`api/` é a **única** exceção ao guard de "no network imports":
`tests/e2e/test_hardening.py::test_no_network_or_shell_exec_in_src`
varre `src/` por AST proibindo `socket|urllib|requests|http|…` e abre
exceção explícita para `src/code_atlas/api/` (linhas 32–58 do teste).

### 2.1 Hardening implementado em `server.py`

| Controle | Implementação | Linha |
|---|---|---|
| Bind só loopback | `AtlasApiServer.__init__` rejeita host fora de `127.0.0.1`/`localhost`/`::1` com `unsafe-bind` | `server.py:207-209` |
| Allowlist de `Host` | regex `_ALLOWED_HOSTS` → `bad-host` 403 (anti DNS-rebinding) | `server.py:35,93-96` |
| CORS estrito | `Origin` estrangeira → `bad-origin` 403; **sem** `Origin` → permitido (curl/ferramentas locais); `Access-Control-Allow-Origin: http://127.0.0.1` + `Vary: Origin` | `server.py:86-98,48-54` |
| Preflight | `OPTIONS` → 204 com `Allow-Methods: GET, POST, OPTIONS`, `Max-Age: 300` | `server.py:147-158` |
| Limites | corpo ≤ 64 KiB (`MAX_BODY`) → 413; resposta ≤ 4 MiB (`MAX_RESPONSE`) → `response-too-large` | `server.py:33-34,65-71,100-111` |
| Headers | `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer` | `server.py:48-54` |
| Erros tipados | `{"error":{"code","message"}}` — internos viram `internal` + mensagem ≤200 chars, **nunca traceback** | `server.py:79-84` |
| Assets | allowlist de extensões, `resolve()` confinado a `static_root` (sem traversal), fallback SPA para `index.html` | `server.py:113-138` |

### 2.2 Handlers — `handlers.py`

Cada request abre uma conexão read-only própria (`handle()`,
`handlers.py:356-389`) e a fecha em `finally` — mesmo padrão de ciclo de
vida do MCP. Rotas:

| Método | Rota | Handler | Limites/validação |
|---|---|---|---|
| GET | `/health` | `_health` | — |
| GET | `/capabilities` | `_capabilities` | espelha `SearchEngine.capabilities()` + `readonly:true` |
| GET | `/repositories` | `_repositories` | envelope `{items,total}` |
| GET | `/repositories/{id}/snapshots` | `_snapshots` | `LIMIT 100`, `id DESC` |
| GET | `/index/status` | `_index_status` | contagens por tabela no `latest_ready` |
| GET | `/graph/overview` | `_graph_overview` | agregado por arquivo; `limit` default 500, cap 2000; devolve `total_files` + `truncated` |
| GET | `/graph/neighbors` | `_graph_neighbors` | `occurrence_id` obrigatório; `direction ∈ in|out|both`; `limit` default 50, cap 200 (`_MAX_LIMIT`) |
| GET | `/symbols/{occurrence_id}` | `_symbol` | nó + `relations_summary` (type × status × n) |
| GET | `/evidence/{id}` | `_evidence` | evidência + `referencing_relations` (≤100) |
| GET | `/search` | `_search` | `q` obrigatório ≤300; `strategy` whitelist → default `hybrid`; `limit` ≤100; `cursor` opaco |
| POST | `/context` | `_context` | `task` ≤2000 obrigatória; `budget_tokens` ≤64000; `max_symbols` ≤200 |
| POST | `/impact/analyze` | `_impact` | `repository_root` ≤1000 obrigatório; revisões ≤200; `symbol` ≤300; `file` ≤1000 |

Escopo de dados por request (obrigatório desde o Cycle 5.1):

- `_repo` (`handlers.py:42-57`): `repository_id` inválido → `bad-param`;
  inexistente → `repo-not-found` 404; ausente → primeiro repo por
  `ORDER BY id` (ou `no-repository` 404 se o banco está vazio).
- `_snapshot` (`handlers.py:66-86`): `snapshot_id` deve pertencer ao repo
  (senão `snapshot-not-found` 404) e estar `READY` (senão
  `snapshot-not-ready` 409); ausente → `latest_ready`.
- IN-lists usam `json_each(?)` e queries nomeadas constantes
  (`_NODES_IN_QUERY`, `_COUNT_QUERIES`) — convenção exigida pelo
  SQL-audit do repo (achado 3 do baseline, `docs/cycle-6-baseline.md`).

**Consequência arquitetural importante:** os handlers chamam os engines
de domínio (`SearchEngine`, `ContextEngine`, `ImpactEngine`,
`StructuralSearch`/`Store`) — nunca SQL livre por feature. A UI Angular
consome exatamente o que o domínio já provava via CLI/MCP; a API é uma
camada fina de serialização + escopo + limites.

## 3. Camadas do frontend Angular

`explorer/` é um workspace Angular 18.2.13 independente
(`explorer/package.json`), standalone components, `strict` TS, build
`@angular-devkit/build-angular:application` (ESBuild), testes
Karma+Jasmine em `ChromeHeadlessCI` (`karma.conf.js`).

```
explorer/src/app/
├── core/
│   ├── api/        ApiService (fetch+AbortController), api.config.ts,
│   │               api.errors.ts — token ATLAS_API_BASE_URL
│   ├── models/     api.models.ts — modelos legacy do scaffold (ver §5)
│   ├── state/      ThemeService, RepositoryContextService (signals)
│   └── errors/     GlobalErrorHandler — boundary único, lastError signal
├── shared/
│   ├── errors.ts   apiErrorText/isApiOffline/FeaturePhase
│   └── components/ state-block, evidence-badge, diagnostics-list,
│                   feature-placeholder
├── layout/shell/   casca: toolbar + sidebar NAV_ITEMS + router-outlet
└── features/       dashboard, repositories, graph, search, symbols,
                    impact, revisions, context, diagnostics, settings
```

Contratos do frontend:

- **`ApiService`** (`core/api/api.service.ts`): `fetch` nativo em vez de
  `HttpClient` (API estritamente loopback — bundle menor, AbortController
  de primeira classe). Cada chamada cria um `AbortController` interno e
  aceita `signal` externa; `cancelAll()` aborta tudo em destroy. Três
  classes de erro: `ApiUnreachableError` (processo fora do ar),
  `ApiHttpError` (não-2xx, carrega o envelope `{error:{code,message}}`),
  `ApiAbortedError` (cancelamento — nunca tratado como falha). A seção
  "v1 wire contracts" (linhas 18–349) espelha os handlers reais; os
  métodos `getHealth/listRepositories/listSnapshots/getIndexStatus/
  searchCode/analyzeImpact/buildContextPack/getGraphOverview/
  getGraphNeighbors/getSymbolDetail` são os usados pelas telas.
- **`ATLAS_API_BASE_URL`** (`api.config.ts`): `http://127.0.0.1:8741/api/v1`
  — o SPA servido pelo mesmo processo fala same-origin com esse prefixo.
- **Rotas lazy** (`app.routes.ts`): 10 rotas `loadComponent`, `''` e `**`
  redirecionam para `/dashboard`; `withComponentInputBinding()` ativo.
- **Estado:** Signals apenas — nenhuma lib de state management.
  `ThemeService` persiste `atlas-theme` em `localStorage`;
  `RepositoryContextService` é seam declarado (ver limitações).
- **Erros:** `GlobalErrorHandler` ignora `ApiAbortedError`, grava
  `lastError` (consumido por `/diagnostics`), loga só fora de produção.
- **Estados:** `shared/components/state-block` renderiza
  `loading|offline|error|empty|ready` com `aria-live`/`role=alert`;
  `evidence-badge` sempre glifo + rótulo pt-BR (nunca só cor).

## 4. Fluxo de dados UI → API → engines → SQLite

Caminho completo de uma tela real (busca):

```
SearchComponent.submit()                         explorer/.../search.component.ts
  → perf.time('GET /search', …)                  features/diagnostics/perf-log.service.ts
  → ApiService.searchCode(params)                core/api/api.service.ts:565
  → fetch GET /api/v1/search?q=…&strategy=…
        │
        ▼  ThreadingHTTPServer._dispatch         src/code_atlas/api/server.py:160
  → _guard()  (Host allowlist + Origin)          server.py:93
  → handlers.handle() → conn = server.connect()  handlers.py:356  (ro + query_only)
  → _search() → SearchEngine(conn).search(       handlers.py:244
        SearchRequest(strategy='hybrid', …))
        │
        ▼  src/code_atlas/search/engine.py
  → rankings lexical/structural/graph (+vector*), fuse RRF, diagnostics
  → SELECT sobre symbol_occurrences/symbols/relations/fts_occurrences
        WHERE snapshot_id = ?   (snapshot resolvido READY-only)
        │
        ▼  storage/db.py connect(mode=ro)
  → dataclasses.asdict(res) → _json() → envelope com security headers
        │
        ▼  de volta ao componente
  → toSearchView() → SearchHitView[]             features/search/search.vm.ts
  → signals (phase='ready', items, next_cursor)  → render OnPush
```

\* Canal vetorial: `SearchEngine` detecta `PRAGMA query_only` ativo e
emite diagnóstico `vector-unavailable-readonly` em vez de falhar
(`search/engine.py:224-240`) — embeddings exigem escrita/cache.

O mesmo desenho vale para as demais telas:

| Tela | Endpoint(s) | Engine |
|---|---|---|
| `/dashboard`, `/diagnostics` | `GET /health`, `/index/status`, `/capabilities`, `/repositories/{id}/snapshots` | SQL de status (handlers) |
| `/graph` | `GET /graph/overview`, `/graph/neighbors`, `/symbols/{id}` | queries de grafo (handlers) |
| `/search` | `GET /search` | `SearchEngine` |
| `/symbols/:id` | `GET /symbols/{occ}` | queries de nó (handlers) |
| `/impact`, `/revisions` | `POST /impact/analyze` | `ImpactEngine` (diff git + snapshot resolve + binding + symbol diff + blast + risk) |
| `/context` | `POST /context` | `ContextEngine` |

## 5. Divergências conhecidas (modelo × implementação)

Registradas para homologação — detalhes em
`docs/cycle-6-limitations.md` e na revisão de UX
(`docs/cycle-6-ux-review.md` §11, achados F1–F12):

- `api.models.ts` (modelos "legacy" do scaffold) e os métodos
  `getSnapshot/getSymbol/getRelations/getImpact/getContextPack` de
  `ApiService` apontam rotas que **não existem** na API — coexistem com
  os tipos v1 corretos; nenhuma tela de feature os usa, exceto o probe do
  placeholder de `/repositories`.
- `/repositories` continua `feature-placeholder` (não lista dados
  reais); `/repositories` e `/revisions` **não** constam em `NAV_ITEMS`
  (`layout/shell/shell.component.ts:11-19`) embora as rotas existam.
- Dois textos de UI referenciam `code-atlas serve`, comando inexistente
  (`dashboard.component.ts:124`, `state-block.component.ts:36`) — o
  correto é `code-atlas explorer` (ou `code-atlas api`).

## 6. Referências

- Backend: `src/code_atlas/api/server.py`, `src/code_atlas/api/handlers.py`,
  `src/code_atlas/cli.py` (`run_explorer`, `run_api`), `src/code_atlas/storage/db.py`.
- ADRs: `docs/adr/adr-0009-local-api-stdlib.md`,
  `explorer/docs/graph-rendering.md` (ADR local de renderização).
- API: `docs/explorer-api.md`. Produto/UX: `docs/explorer-product-spec.md`,
  `docs/explorer-ui-ux.md`. Design tokens: `docs/explorer-design-system.md`.
- Frontend: `explorer/src/app/**`; ADR de biblioteca de grafo:
  `explorer/docs/graph-rendering.md`; estabilidade de layout:
  `docs/graph-layout-stability.md`.
