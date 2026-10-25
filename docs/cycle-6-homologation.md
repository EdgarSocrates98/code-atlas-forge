# Cycle 6 — Matriz de homologação (gates G0–G20)

> lf-0095 (Phases 19–22). Checklist dos gates do ciclo contra o código
> real do branch `cycle-6-angular-explorer`. Convenção de status:
>
> | Status | Significado |
> |---|---|
> | **PASS** | evidência verificada em código/teste/doc citado |
> | **PARTIAL** | implementado com ressalva registrada — leia a nota |
> | **NOT RUN** | não executado nesta sessão (sem shell) — nada afirmado |
>
> Mapeamento: os specs `factory/specs/active/lf-0083…0094` +
> `inbox/lf-0095` cobrem Phases 0–22; os gates abaixo (G0–G20)
> consolidam essas fases um-a-um ou par-a-par onde a spec agrupa duas.
> **Sem shell nesta sessão** — "PASS" significa "evidência presente e
> coerente no repositório", não "teste rodado agora". Reexecuções
> pendentes estão em §4.

## 1. Matriz

| Gate | Fase / spec | Critério | Status | Evidência |
|---|---|---|---|---|
| G0 | Phase 0 · lf-0083 | baseline audit + `docs/cycle-6-baseline.md` | **PASS** | `docs/cycle-6-baseline.md` existe com auditoria de engines, achados e contagens da época |
| G1 | Phase 1 · lf-0084 | personas/jornadas + product-spec + ui-ux | **PASS** | `docs/explorer-product-spec.md`, `docs/explorer-ui-ux.md` |
| G2 | Phase 2 · lf-0085 | tokens + temas + componentes base | **PARTIAL** | `explorer/src/design-system/tokens.css`, `core/state/theme.service.ts`, `shared/components/{evidence-badge,state-block}` — auditoria de contraste pendente (gap declarado no próprio design-system doc) |
| G3 | Phase 3 · lf-0087 | scaffold strict + rotas lazy + build prod | **PARTIAL** | `app.routes.ts` (10 lazy), `tsconfig` strict, `angular.json` budgets, baseline registra build OK — mas `NAV_ITEMS` omite `/repositories` e `/revisions` (F1) |
| G4 | Phase 4 · lf-0086 | API loopback reutilizando engines + ADR | **PASS** | `src/code_atlas/api/{server.py,handlers.py}`, `docs/adr/adr-0009-local-api-stdlib.md`, `docs/explorer-api.md`, `tests/api/test_explorer_api.py` (bind/Host/Origin/413/404) |
| G5 | Phases 5–6a · lf-0088 | lib de grafo escolhida por ADR | **PASS** | `explorer/docs/graph-rendering.md` (Sigma/WebGL + graphology/FA2, alternativas avaliadas) + deps em `explorer/package.json` |
| G6 | Phases 5–6b · lf-0088 | câmera estável + bounded rendering | **PARTIAL** | contrato verificado em `graph-data.store.ts`/`graph-canvas.component.ts` (`needsLayout` único, caps, `customBBox`) — **mas sem benchmark medido**; ver G15 |
| G7 | Phase 7 · lf-0089 | node/edge inspector | **PASS** | `graph-inspector.component.ts` (+spec): campos do nó, `relations_summary`, aresta com evidência — ressalva: `evidence_ids` nunca chega do backend (L5) |
| G8 | Phase 8 · lf-0089 | drill-down agregado | **PARTIAL** | `drillFile`/`exitDrill`/`FrozenView` implementados — via semente de busca, cobertura aproximada e declarada (L6); não é endpoint por arquivo |
| G9 | Phase 9 · lf-0090 | hybrid search UI | **PASS** | `features/search/` — filtros, cursor com dedup (`search.vm.ts`), diagnósticos, `truncated`, histórico opt-in localStorage |
| G10 | Phase 10 · lf-0090 | blast radius visual | **PARTIAL** | `features/impact/` completo (banner parcial, risco≠incerteza, hops) — sem export JSON local do resultado (L24) |
| G11 | Phase 11 · lf-0091 | visual diff | **PARTIAL** | `features/revisions/` diff de **símbolos** com binding states — não é diff linha-a-linha; semântica honesta documentada em `docs/visual-diff.md` |
| G12 | Phase 12 · lf-0091 | test explorer | **PARTIAL** | `test_candidates` renderizados em `ImpactResultsComponent` com tag de revisão/relationship/strength — sem tela dedicada de exploração de testes |
| G13 | Phase 13 · lf-0091 | context pack explorer | **PASS** | `features/context/` — manifest, budget estimado, itens, diagnósticos, export JSON/markdown (`context.vm.ts`); ressalva: `expansion_refs` não acionáveis (L25) |
| G14 | Phase 14 · lf-0092 | painel diagnósticos/indexação | **PASS** | `features/diagnostics/` — health, capabilities, index status + snapshots, perf log (`perf-log.service.ts`), `lastError` do `GlobalErrorHandler` |
| G15 | Phase 15 · lf-0093 | budgets medidos (bundle, API, grafo) | **NOT RUN** | `docs/cycle-6-benchmarks.md` — tudo marcado NOT RUN honestamente; scale test escrito não executado; números 10k/25k inexistentes |
| G16 | Phase 16 · lf-0093 | WCAG 2.2 AA + catálogo de estados | **PARTIAL** | catálogo de estados amplamente implementado (`state-block`, banners, badges glifo+rótulo, teclado, reduced-motion) — auditoria axe/Lighthouse/contraste **NOT RUN** |
| G17 | Phase 17 · lf-0094 | hardening do servidor | **PARTIAL** | guards verificados em `server.py` + `explorer-security.md`; `security.spec.ts` (8 casos) escrito — e2e **não executado**; gaps CSP/`X-Frame-Options`/`MAX_RESPONSE` sem teste (L10–L11) |
| G18 | Phase 18 · lf-0094 | Playwright E2E — 18 fluxos | **NOT RUN** | 18 testes escritos (`e2e/flows.spec.ts` 10 + `security.spec.ts` 8), harness real (`global-setup/teardown`) — **nunca executado** (`cycle-6-e2e.md`); lock dessincronizado (L14); lf-0094 permanece pendente |
| G19 | Phase 19 · lf-0095 | `code-atlas explorer` CLI | **PASS** | `cli.py:852-889` + parser 1409-1417: dist resolution dupla, `--open` opt-in, `--host/--port`, erro sem db, erro de porta, fallback API-only |
| G20 | Phases 20–22 · lf-0095 | docs + PR + homologação | **PARTIAL** | docs do ciclo escritos/atualizados nesta spec (este arquivo + §5); PR **não verificável** em sessão read-only |

**Contagem:** 9 PASS · 10 PARTIAL · 2 NOT RUN (21 gates, G0–G20).
G15 (budgets medidos) e G18 (E2E) são os únicos sem nenhuma evidência
executável hoje.

## 2. O que o PASS cobre — e o que não cobre

- **PASS ≠ teste executado agora.** G0–G14, G19 foram homologados por
  leitura direta dos arquivos citados nesta sessão. A evidência de
  execução mais recente é a registrada em `docs/cycle-6-baseline.md`
  (539 testes Python, `ng test` 13/13, build OK) — histórica, não
  reproduzida aqui.
- **PARTIAL com ressalva explícita** — cada ressalva aponta para um item
  numerado em `docs/cycle-6-limitations.md` (L1–L28).
- **NOT RUN** — não há como fabricar: faltam execução de shell,
  browser e profiler.

## 3. Divergências de homologação a resolver antes de "done"

| # | Item | Impacto no gate | Ação sugerida |
|---|---|---|---|
| H1 | Executar `npm run e2e` (Playwright ×18) | G18 | `cd explorer && npm install && npx playwright install chromium && npm run build && npm run e2e` |
| H2 | Re-rodar suíte: `pytest`, `ruff`, `mypy`, `npm test`, `npm run build` | G3/G6/G15/G17 | verification line dos specs; baseline é de commits atrás |
| H3 | Benchmark real: 10k nós no canvas + latências p50/p95 | G6/G15 | harness novo ou profiler manual — hoje nada mede isso |
| H4 | Auditoria WCAG (axe/Lighthouse + contraste) | G16 | lf-0093 ativo; sem auditoria automatizada até agora |
| H5 | `npm ci` reproduzível | empacotamento (G19/G20) | regenerar `package-lock.json` com Playwright incluído |
| H6 | `/repositories` + `/revisions` no `NAV_ITEMS` | G3 | correção de 2 linhas — achado F1 aberto |
| H7 | Texto `code-atlas serve` → comando real | UX (G3) | `dashboard.component.ts`, `state-block.component.ts` |
| H8 | `evidence_ids` no payload de `/graph/neighbors` | G7 | incluir coluna no SELECT de `_graph_neighbors` + teste |
| H9 | Windows Explorer smoke | G19/G20 | sem CI de Node; documentado NOT RUN |

## 4. Verificação replicável (linha dos specs)

```bash
python -m pytest tests/ -x -q && ruff check src tests && mypy src \
  && cd explorer && npm install && npm run build && npm test -- --watch=false \
  && npm run e2e
```

Cada trecho que falhar derruba os gates correspondentes — re-rodar
antes de declarar o ciclo encerrado.

## 5. Documentos produzidos/atualizados nesta homologação

- `docs/cycle-6-architecture.md` (novo)
- `docs/interactive-impact.md` (novo)
- `docs/visual-diff.md` (novo)
- `docs/graph-layout-stability.md` (novo)
- `docs/explorer-installation.md` (novo)
- `docs/cycle-6-limitations.md` (novo)
- `docs/cycle-6-homologation.md` (este)
- `README.md` (seção Cycle 6), `docs/adr/README.md` (ADR-0009),
  `docs/roadmap.md` (estado do Cycle 6)

## 6. Referências

- Specs: `factory/specs/active/lf-0083…lf-0094`,
  `factory/specs/inbox/lf-0095`.
- Evidência primária: `docs/cycle-6-baseline.md`,
  `docs/cycle-6-benchmarks.md`, `docs/cycle-6-e2e.md`,
  `docs/explorer-security.md`, `docs/cycle-6-ux-review.md`.
- Código: `src/code_atlas/api/`, `src/code_atlas/cli.py`,
  `explorer/src/app/**`.

## 7. Reconciliação Cycle 6.1 (adendo — não reescreve a matriz acima)

A matriz G0–G20 registra o estado **no fechamento do ciclo 6** e fica
como estava. O que o 6.1 mediu de fato, atualizando os pontos que eram
NOT RUN ou PARTIAL por falta de execução:

| Gate/Item | Estado no ciclo 6 | Medição 6.1 | Evidência |
|---|---|---|---|
| G15 — budgets medidos | NOT RUN | **EXECUTADO** — benchmarks reais 100–5000 arquivos (API, TTFN, pan, cap→2000, FA2, heap); 10k nós continua NÃO reivindicado (API trunca a 2000, canvas capa a 500/2000) | `docs/cycle-6.1-benchmarks.md`, `benchmarks/results/graph-scale-latest.json` |
| G16 — WCAG 2.2 AA | PARTIAL (auditoria NOT RUN) | **AUDITADO** — axe-core 24 scans em 12 superfícies ×2 temas (9 rotas + `/symbols/{id}` + busca com resultados + pack gerado): **0 violações**; 6 `aria-prohibited-attr` corrigidos no ciclo | `docs/explorer-accessibility-report.md`, `explorer/e2e/a11y.spec.ts` |
| G18 — E2E Playwright | NOT RUN (18 escritos) | **EXECUTADO** — suíte hoje tem **102 testes** em 6 specs (flows 10 + flows2 14 + security 8 + a11y 29 + graph-scale 5 + screenshots 36); job CI `explorer-e2e` verde. 18 mobile-shots falharam no overflow e o fix CSS aguarda re-run | `npx playwright test --list`, `docs/explorer-ux-review-v2.md` |
| H1 (`npm run e2e`) | pendente | fechado (acima) | — |
| H3 (benchmark real) | pendente | fechado parcialmente — medido até 2000 renderizados; "10k no canvas" substituído por evidência de mecanismo (truncated/ocultos/cap) | `docs/graph-performance-homologation.md` |
| H4 (axe/Lighthouse) | pendente | axe fechado; **Lighthouse segue NOT RUN** | `docs/explorer-accessibility-report.md` |
| H5 (`npm ci` reproduzível) | pendente | fechado — `npm ci` instala lock exato (1001 pacotes); lock L14 sanado | `docs/cycle-6.1-dependency-audit.md` |
| H6 (`/repositories`,`/revisions` no NAV) | aberto (F1) | **fechado** — NAV_ITEMS tem 9 itens | `shell.component.ts:31-41` |
| H7 (`code-atlas serve` na UI) | aberto | **fechado** — textos apontam `code-atlas explorer`; endereço da API derivado, não hardcoded | `state-block.component.ts` (`apiOrigin()`) |
| H8 (`evidence_ids` no neighbors) | aberto (L5) | **fechado** — DTO completo: `relation_id`, `snapshot_id`, `to_unresolved`, `confidence_class`, `resolver`, `evidence_ids` decodificado | `handlers.py:190-239`, `docs/cycle-6.1-evidence.md` |
| H9 (Windows smoke) | NOT RUN | **continua NOT RUN** — wheel+CLI cobertos por `test (windows-latest)`, Explorer interativo sem evidência | `docs/explorer-cross-platform.md` |
| L6 (drill via busca) | degradação | **fechado** — `GET /files/{id}/occurrences` real; fallback por busca removido (404 → empty honesto) | `handlers.py:242-288`, `docs/graph-evidence-contracts.md` |
| L8 (sem rate limit) | gap | **fechado** — 300 req/min por IP em `/api/v1`, 429+`Retry-After` | `server.py:42-43,60-79`, `docs/explorer-security-v2.md` |
| L10 (sem CSP/XFO) | gap | **fechado** — CSP com `script-src-attr 'unsafe-inline'` (racional documentado) + `X-Frame-Options: DENY` | `server.py:82-95`, `docs/explorer-security-v2.md` |
| L20 (seletor só-leitura) | gap | **fechado** — `<select>` funcional + contexto global | `shell.component.html`, `docs/global-context-navigation.md` |
| L23 (deep-linking de forms) | gap | **fechado parcialmente** — `/search`, `/impact`, `/graph`, `/symbols/:id`, `?repo=&snapshot=` restauram; `/context` segue sem | `docs/global-context-navigation.md`, L22 |
| L24 (export de impacto) | gap | **fechado** — Export JSON/Markdown client-side | `impact.component.ts` |
| L25 (`expansion_refs`) | gap | **fechado parcialmente** — chips acionáveis → `/search?q=`; re-run nível+1 não implementado | `context.component.ts`, L18 |
| câmera/canvas congelado | bug medido | **corrigido e re-medido** — `relayoutCount` força fit; `canvas_frozen=false`, 6 draw calls em todos os tamanhos | `graph-canvas.component.ts:506-522`, `docs/cycle-6.1-benchmarks.md` §7 |
