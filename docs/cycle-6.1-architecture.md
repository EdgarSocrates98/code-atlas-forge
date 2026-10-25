# Cycle 6.1 — Arquitetura do Explorer (hardening)

> Status: branch `cycle-6.1-explorer-hardening`, specs lf-0096–lf-0108.
> Delta sobre `docs/cycle-6-architecture.md` — este documento só cobre o
> que **mudou** no 6.1; o desenho de base (um processo loopback,
> `http.server` stdlib, SQLite `mode=ro` + `PRAGMA query_only`, engines
> de domínio por trás dos handlers) permanece. Tudo verificado contra os
> arquivos citados.

## 1. Backend — `src/code_atlas/api/`

### 1.1 Novo endpoint `GET /files/{id}/occurrences` (handlers.py:242-288)

Drill-down real por arquivo — substitui a semente via busca textual
(L6 do cycle 6):

- Rota `re.fullmatch(r"/files/(\d+)/occurrences")` (`handlers.py:438`).
- O escopo é derivado do **arquivo**, não de parâmetro livre: lê
  `source_files.repository_id` do `file_id` (404 `file-not-found` se
  inexistente) e resolve o snapshot via `_snapshot` (READY-only,
  `snapshot_id` opcional com validação repo-scoped).
- Paginação determinística `ORDER BY o.line, o.col, o.id` —
  `limit` default 200, cap 500; `offset` aceito.
- Resposta: `file_id`, `repository_id`, `snapshot_id`, `path`,
  `language`, `items[{id: "occ:N", occurrence_id, symbol_id,
  qualified_name, kind, span_start, span_end, line, col, content_hash,
  parse_status}]`, `total`, `limit`, `offset`, `truncated`.
- Contrato coberto por `tests/api/test_files_occurrences.py` (happy,
  404, paginação — 1 caso `todo` marcado `skip` honestamente).

### 1.2 DTO completo de aresta em `/graph/neighbors` (handlers.py:190-239)

O SELECT de arestas foi ampliado de 6 para 9 colunas e o serializador
`_row_to_edge` agora emite a **prova da relação**, não só os endpoints:

```json
{ "id": 3, "relation_id": 3, "snapshot_id": 1,
  "from_id": 6, "to_id": 8, "to_unresolved": null,
  "type": "DEFINES", "resolution_status": "resolved",
  "confidence_class": "resolved", "resolver": "tree-sitter-python",
  "evidence_ids": [1] }
```

`evidence_ids` sai do JSON armazenado na coluna (`json.loads`) — fecha o
gap L5 do ciclo 6, em que o inspector tinha o campo no DTO mas o backend
nunca o emitia. `to_id: null` + `to_unresolved: <nome>` preservam alvo
não resolvido como fato.

### 1.3 `file_id` no overview (handlers.py:173-181)

`/graph/overview` passou a emitir `file_id` por item — o drill-down da
UI endereça `GET /files/{id}/occurrences` diretamente, sem fallback por
path (o fallback por path existe no cliente, mas com `file_id` presente
nunca é exercitado).

### 1.4 Hardening novo em `server.py`

| Controle | Implementação | Linha |
|---|---|---|
| Rate limit por cliente | `_RateLimiter` — janela deslizante em memória por IP de cliente (`deque` + lock); **300 req/min**, só rotas `/api/v1`; estáticos isentos; excesso → 429 `rate-limited` + `Retry-After` | `server.py:42-43,60-79,224-230` |
| Timeout por conexão | `REQUEST_TIMEOUT = 30s` via `connection.settimeout` no `setup`; `TimeoutError` → encerra quieto (keep-alive ocioso/leitura travada) ou 408 `request-timeout` se dentro do dispatch | `server.py:44,102-111,236-239` |
| CSP + XFO | `Content-Security-Policy: default-src 'self'; script-src 'self'; script-src-attr 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'` + `X-Frame-Options: DENY` em **toda** resposta | `server.py:82-95` |
| `unsafe-bind`, `bad-host`, `bad-origin`, `payload-too-large`, `response-too-large`, `bad-json` | sem mudança de contrato — ver `cycle-6-architecture.md` §2.1 | — |

**Racional `script-src-attr 'unsafe-inline'`:** o `index.html` emitido
pelo build Angular 18 carrega o stylesheet via
`<link rel="stylesheet" media="print" onload="this.media='all'">` — um
handler de evento inline. Sob `script-src-attr 'none'` (default da
política) o CSS nunca aplica e o app renderiza **sem estilo** — achado
E2E real (commit `c61ab38`). A diretiva abre só atributos de evento;
`script-src 'self'` continua bloqueando `<script>` inline e externos
não-self — trade-off medido, não blanket `'unsafe-inline'`.

**Rate limit 300/min:** o valor inicial 120/min produzia 429 em sessões
legítimas de drill-down (cada página de ocorrências + fan-out de
neighbors soma dezenas de requests); 300/min mantém o guard-rail contra
loops/runaway sem punir uso real (commit `c61ab38`).

## 2. Frontend — `explorer/src/app/`

### 2.1 `ExplorerContextService` — contexto global (`core/state/`)

Novo serviço singleton (lf-0098) que responde "qual repositório e qual
snapshot o usuário está vendo":

- `repositories`/`activeRepository`/`snapshots`/`activeSnapshot`/
  `selectedSymbol` como signals; `activeSnapshot: null` significa
  "default do servidor" (latest READY) — nunca snapshot fabricado.
- `init()` idempotente: bootstrap único compartilhado por shell e telas
  (`initPromise` cached; `refresh()` força re-bootstrap).
- Restaura contexto persistido de `localStorage['atlas-explorer-context']`
  — **apenas** `{repoIdentity: identity_key, snapshotId}` (identificadores,
  não dados); valida contra `GET /repositories` e marca `notice` explícito
  quando o salvo está stale (repo sumiu, snapshot não-READY) em vez de
  adivinhar.
- Deep links: `syncFromQueryParams({repo, snapshot})` — `repo` casa por
  `root_handle`, `identity_key` ou id numérico; `snapshot` deve existir e
  estar READY. Valores irresolúveis viram `notice` visível no shell
  (`role="status"`, dismissível), nunca contexto inventado.
- `contextQueryParams()`/`deepLink(path)` constroem links que carregam o
  contexto — usados pelas ações "Abrir grafo"/"Pesquisar" do control
  center e pelos links inspector→grafo.

### 2.2 Repositories control center (`features/repositories/`)

`RepositoriesComponent` substitui o `feature-placeholder` (P0/L19 do
ciclo 6): lista real de `GET /repositories` enriquecida por
`GET /index/status` (contagens `file_inventory`/`symbol_occurrences`/
`relations`/`evidence` por repo) e `GET /repositories/{id}/snapshots`
(latest READY com `created_at`, `git_ref`, `commit_sha` curto). Ações:
`Selecionar` (grava contexto global + pin do snapshot READY exibido),
deep links `/graph?repo=&snapshot=` e `/search?repo=`, `Copiar caminho`
com feedback `role="status"`, e falha de um repo degrada sua linha para
"—" sem derrubar a tela (Promise.all por-repo best-effort).

### 2.3 Shell — navegação de contexto (`layout/shell/`)

`NAV_ITEMS` agora tem **9 itens** incluindo `Repositórios` e `Revisões`
(fecha F1/L18). Toolbar ganhou: `<select>` funcional de repositório
(fecha F12/L20 — era somente-leitura), selo `snapshot #N` ou "snapshot:
mais recente", e faixa de `notice`. `buildBreadcrumbs(url)` gera crumbs
por segmento (`/symbols/:id` → "ocorrência #N"), crumb intermediário sem
rota renderiza sem link. Sidebar colapsável persistida em
`localStorage['atlas-sidebar-collapsed']`.

### 2.4 `GraphStateService` — câmera que sobrevive à rota

Serviço singleton (lf-0099) que resolve L15/"câmera não se preserva":
`GraphDataStore` é component-scoped, então navegar grafo→símbolo→grafo
perdia câmera e seleção. `GraphStateService` guarda
`GraphViewState{mode, camera{x,y,ratio}, selectedNodeId, selectedEdgeId,
pinnedIds}` com chave `r{repo}/s{snapshot}/{mode}` — escopo repo+snapshot
(spec), sufixo de modo para nunca aplicar câmera de arquitetura sobre
layout de símbolo. Bounded a 8 chaves (LRU por inserção); `consume()`
lê-e-remove — restore vale só para a próxima entrada. O canvas persiste
no switch de modo e no destroy; restaura no `runLayout()`.

### 2.5 Correção do canvas congelado — `relayoutCount`

Bug P0 medido no Phase 5 (documentado em
`docs/graph-performance-homologation.md`): após o primeiro
"Reorganizar", a câmera persistida — medida sobre o layout anterior —
era restaurada e jogava todos os nós fora do frustum (0 draw calls,
canvas aparentemente morto); adicionalmente `reportPositions → commitData`
escrevia o signal `nodes` dentro do `effect()` que o lê. Correção
(commit `03fc775`, `graph-canvas.component.ts:506-522` +
`graph-data.store.ts:92,543`):

- `store.relayoutCount` incrementa a cada relayout explícito; no
  `runLayout`, mismatch com `lastRelayoutCount` **força `fitView()`**
  em vez de restaurar a câmera salva (seleção e pins persistidos
  continuam aplicados via `applyViewState`).
- `reportPositions` é deferido por `queueMicrotask` — a escrita sai do
  effect de sync que lê `nodes`.
- Verificado no re-run do benchmark: `canvas_frozen_after_first_relayout:
  false` e `draw_calls_in_window: 6` em todos os tamanhos (100–5000).

### 2.6 Drill-down real (`graph-data.store.drillFile`)

`drillFile(path, expectedSymbols, fileId)` semeia via
`fetchFileOccurrences` — pagina `GET /files/{ref}/occurrences`
(`DRILL_PAGE_LIMIT=500`, `DRILL_MAX_PAGES=5`) usando `file_id` do item de
overview; `ref` cai para o path só quando o backend antigo não emite o
id, e 404 vira estado `empty` honesto ("endpoint não disponível nesta
versão da API"), **nunca** fallback fabricado por busca. Toda ocorrência
vira nó (mesmo sem arestas), depois expansão de vizinhança limitada
(`DRILL_NEIGHBOR_BUDGET=12` seeds × `NEIGHBOR_LIMIT=100`, `direction=both`)
desenha arestas intra/inter-arquivo. `archSnapshot = freeze()` preserva a
visão macro e `exitDrill()` restaura verbatim — inclusive pins
reconstruídos dos VMs congelados.

### 2.7 Vocabulário de evidência correto (`graph.utils.toEvidenceLevel`)

Mapa explícito do vocabulário real do schema
(`resolution_status CHECK`): `resolved→verified`,
`ambiguous→possible`, `unresolved`/`external` direto, resto→`unknown` —
nunca promove (commit `c61ab38`; antes `resolved` caía em `unknown`).
`confidence_class` e `resolver` exibidos crus no inspector.

### 2.8 Outros hardenings do canvas

- `rendererLost` distinto de `rendererFailed`: WebGL context lost pode
  recuperar via `contextrestored`; sanitização de coordenadas inválidas
  (NaN/Infinity) com diagnóstico antes de envenenar o spatial index do
  sigma (`graph-canvas.component.ts:400-408,456-468`).
- `GraphDiagnosticsComponent` — painel colapsável com contagens reais do
  store + zoom/layout/FPS amostrados por rAF **só enquanto aberto**
  (fora do NgZone; fechar destrói o loop — nenhum profiling permanente).
- Busca: `?q=&strategy=&language=&kind=&path=&repo=&limit=` restaura e
  sincroniza URL (L23 pago para `/search`); hits linkam
  `/symbols/{occ}` e `/graph?focus=occ:N`.
- Impacto: `?repo=&base=&target=&base_snapshot=&target_snapshot=&symbol=
  &file=&possible=&known=` restaura formulário; **Export JSON/Markdown**
  client-side do `ImpactResult` bruto (L24 pago); links de foco no grafo
  em blast/test candidates.
- Context: `expansion_refs` viram chips acionáveis → `/search?q=` (L25
  pago); Copiar JSON/Markdown + Exportar `.json`.

## 3. Harness de benchmark de escala (`explorer/e2e/graph-scale.spec.ts`)

Medição real — não estimativa (lf-0100, Phase 5):

- `tests/fixtures/gen_large_repo.py` gera repos sintéticos
  determinísticos (`mod_NNNN.py` em anel; `--files N` ⇒ exatamente N
  nós de overview, `items == total_files == N` verificado para N≤2000).
- Para cada tamanho (100/500/1000/3000/5000): indexa com o CLI real em
  `CODE_ATLAS_DATA_DIR` isolado, sobe `code-atlas explorer` real, mede
  via Playwright headless Chromium + CDP: latência curl do overview,
  TTFN (page-clock + wall), contagem DOM de nós/ocultos, FPS de pan por
  deltas de rAF, gap máximo de RAF do relayout FA2, experimento de cap
  500→2000, heap via `Performance.getMetrics`, e probe de canvas
  congelado (`second_relayout_probe`).
- Registros brutos: `benchmarks/results/graph-scale-{100,500,1000,3000,
  5000}.json` + `graph-scale-latest.json`; relatório em
  `docs/graph-performance-homologation.md`; números consolidados em
  `docs/cycle-6.1-benchmarks.md`.

## 4. Diagrama do fluxo 6.1 (drill-down com contexto global)

```
/repositories  →  select(repo)  →  ExplorerContextService
                                        │ persist {identity_key, snapshotId}
        "Abrir grafo"                   ▼
   /graph?repo=<root>&snapshot=<id>  →  ShellComponent.syncFromQueryParams
                                        │ valida contra /repositories + /snapshots
                                        ▼
   GraphDataStore.loadOverview → GET /graph/overview (items têm file_id)
                                        │ dblclick no nó file:
                                        ▼
   drillFile → GET /files/{file_id}/occurrences (paginado)
             → GET /graph/neighbors × ≤12 seeds (edges com DTO completo)
                                        │
   GraphCanvasComponent (sigma/WebGL)   ▼
   GraphStateService.save/consume  ←──  câmera+seleção+pins por r/repo/s/snap/mode
```

## 5. Referências

- Backend: `src/code_atlas/api/{server.py,handlers.py}`.
- Frontend: `explorer/src/app/core/state/explorer-context.service.ts`,
  `features/repositories/`, `layout/shell/`, `features/graph/` (store,
  canvas, state service, diagnostics, utils).
- Testes: `tests/api/test_files_occurrences.py`,
  `tests/api/test_api_hardening.py`, `explorer/e2e/{flows2,a11y,
  graph-scale}.spec.ts`.
- Docs correlatos: `docs/cycle-6.1-evidence.md`,
  `docs/cycle-6.1-benchmarks.md`, `docs/cycle-6.1-limitations.md`,
  `docs/repository-control-center.md`, `docs/global-context-navigation.md`,
  `docs/graph-evidence-contracts.md`, `docs/explorer-security-v2.md`.
