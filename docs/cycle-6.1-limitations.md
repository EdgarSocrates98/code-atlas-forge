# Cycle 6.1 — Limitações e gaps conhecidos

> Inventário honesto de tudo que **não** está provado, é degradação por
> design, ou pendência aberta — estado ao fim do branch
> `cycle-6.1-explorer-hardening`. Convenção: **[gap]** ausência de
> feature · **[degradação]** comportamento declarado, não erro ·
> **[não medido]** sem evidência de execução · **[bug]** desvio
> código↔intenção.

## 1. Escala e performance

| # | Item | Estado | Evidência |
|---|---|---|---|
| L1 | 10k+ nós no canvas | **[não medido]** | Máximo exercitado foi 2000 renderizados (cap experiment @3000/@5000). A API `overview` trunca a 2000 e o canvas capa a 500 default/2000 max — 10k exigiria mudança de contrato, não só de teste. `docs/cycle-6.1-benchmarks.md` §10 |
| L2 | Renderização de arestas | **[não medido]** | O overview emite 0 edges por design; nenhum run do graph-scale mediu custo de aresta. Grafos de símbolo (fetch-bounded ≤2500 nós) sem benchmark |
| L3 | GPU real | **[não medido]** | Todos os fps são SwiftShader (software) em headless Chromium — piso pessimista, não número de hardware real |
| L4 | Relayout FA2 a 2000 nós | **[medido — é lento]** | ~2.3–2.7 s de main thread congelada, página sem resposta. É o custo real do layout síncrono; mitigação (worker/chunked) não implementada |
| L5 | Banda de variância | **[não medido]** | Run único por tamanho; jitter de re-run observado (ex.: pan @2000 entre ~38–43 fps) — deltas de um dígito são ruído |
| L6 | Bundle pós-6.1 | **[medido na CI apenas]** | `ng build` verde no job `explorer`; budgets do `angular.json` vigentes; número de kB do bundle final não re-reportado neste doc (não fabricado) |

## 2. Browser / acessibilidade

| # | Item | Estado | Evidência |
|---|---|---|---|
| L7 | Firefox / Safari | **[não medido]** | Suite Playwright roda só Chromium (projeto único em `playwright.config.ts`); WebKit/Firefox nunca executados |
| L8 | Screen reader (NVDA/JAWS/VoiceOver) | **[não medido]** | axe (24 scans, 0 violações) + testes de teclado não substituem walkthrough com AT — `docs/explorer-accessibility-report.md` §"Remaining known issues" |
| L9 | `/impact` com resultado renderizado | **[parcial]** | `role="region"` aplicado por padrão, mas a fixture não produz revision pair → `.impact-result` não foi scaneado montado; `/impact` cobriu form/state blocks |
| L10 | Linhas expansíveis de snapshot em `/diagnostics` | **[não medido]** | tabela aninhada atrás de clique, sem scan dedicado (estrutura espelha a tabela externa auditada) |
| L11 | Traps de teclado dentro do canvas WebGL | **[parcial]** | axe não prova ausência; mitigação declarada: `role="application"` + mapa de teclado documentado + alternativa `<atlas-graph-table>` |

## 3. Plataforma

| # | Item | Estado | Evidência |
|---|---|---|---|
| L12 | Explorer interativo em Windows | **[não medido] — NOT RUN** | wheel+CLI cobertos pelos jobs `test (windows-latest, py3.12/3.13)`; nenhuma execução Node/browser em Windows — `docs/explorer-cross-platform.md` |
| L13 | macOS | **[não medido] — NOT RUN** | sem job macOS no CI; nenhuma execução registrada |
| L14 | `code-atlas explorer` bind/`webbrowser.open` fora de Linux | **[não medido]** | desenho portável por construção, sem evidência fora de Linux |

## 4. Produto / UX

| # | Item | Estado | Evidência |
|---|---|---|---|
| L15 | Command palette / busca global | **[gap]** | ausente; `Ctrl+K`-style não existe no shell |
| L16 | Edge bundling / arestas agregadas | **[gap]** | arestas renderizam retas no sigma; sem bundling nem visualização agregada — em vizinhanças densas a malha é literal |
| L17 | Drill-down de arquivo paginado | **[degradação]** | `DRILL_MAX_PAGES=5` × `DRILL_PAGE_LIMIT=500` = até 2500 ocorrências semeadas; arquivo com mais ocorrências fica com drill parcial (declarado pelo `truncated`/contexto) |
| L18 | `expansion_refs` → nível+1 automático | **[degradação]** | chips acionáveis abrem `/search?q=` com o ref — navegação implementada; re-disparo de `POST /context` com level+1 não implementado |
| L19 | ApiService legado residual | **[gap] código morto** | `getContextPack()` (`api.service.ts:507`) ainda aponta `/symbols/{id}/context-pack` — rota que a API nunca teve; nenhuma tela o chama. `api.models.ts` legacy coexiste com tipos v1 |
| L20 | `docs/explorer-api.md` desatualizado | **[gap] doc** | não cobre `/files/{id}/occurrences` nem os campos novos de aresta — contrato atual está em `docs/cycle-6.1-evidence.md` |
| L21 | Histórico de impacto (`GET /impact/{id}`) | **[gap]** | backend só tem POST síncrono efêmero; export é client-side da resposta atual |
| L22 | Deep-link de formulário em `/context` | **[gap]** | `?task=`/`?level=` não restauram formulário de context pack (search/impact já restauram) |

## 5. Backend / segurança

| # | Item | Estado | Evidência |
|---|---|---|---|
| L23 | `MAX_RESPONSE` (4 MiB) disparado | **[não medido]** | nenhum teste provoca resposta >4 MiB — caminho `response-too-large` é código-verificado, não exercitado (mesma nota do ciclo 6) |
| L24 | Rate limit por-IP em multi-cliente loopback | **[degradação]** | chave = `client_address[0]`; todos os clientes locais são `127.0.0.1` — o limite é por-máquina na prática (correto para single-user; multi-uso via SSH/port-forward divide o teto) |
| L25 | `script-src-attr 'unsafe-inline'` | **[degradação documentada]** | necessário para o `onload=` do stylesheet no `index.html` do Angular 18 (achado E2E real — sem ele o app renderiza sem estilo). `script-src 'self'` segue bloqueando `<script>` inline |
| L26 | Vulnerabilidades npm | **[medido — aceito]** | 55 total / 0 critical em produção; 4 highs de runtime são advisories Angular 18 sem vetor endereçável neste app (fetch-only, sem SSR/i18n/HttpClient) — `docs/cycle-6.1-dependency-audit.md`. Fix completo exige Angular 22 (breaking, adiado); bump parcial `18.2.21` do toolchain dev disponível e não aplicado |

## 6. Testes / harness

| # | Item | Estado | Evidência |
|---|---|---|---|
| L27 | 1 teste Python `skip` | **[declarado]** | `tests/api/test_files_occurrences.py` — "todo arquivo tem ocorrências" marcado skip (todo honesto no nome) |
| L28 | graph-scale precisou de `trace:'off'` | **[degradação de harness]** | race ENOENT de artefato de trace no `page.close()` do Playwright — não relacionado ao app |
| L29 | Tempo do job `explorer-e2e` | **[degradação]** | `testDir: './e2e'` sem `testIgnore` — `npm run e2e` inclui os 5 cenários de benchmark (até 300 s cada), então o job cobre suite completa + scale; duração do job limita o quanto de scale cabe na CI |

## 7. O que **não** é limitação (esclarecimentos do ciclo 6 que continuam valendo)

- **Sem "grafo inteiro"** — modelo bounded macro→meso→micro, decisão de
  design, não bug.
- **API read-only** — `mode=ro` + `query_only` é a decisão central
  (ADR-0009).
- **Sem telemetria** — ausência é o requisito.
- **`truncated`/nós ocultos com aviso** — degradação declarada e visível
  na tela, não silenciosa.

## 8. Resolvidos neste ciclo (para constar — não reabrir)

L5 (evidence_ids), L6 (drill via busca), L8 (sem rate limit),
L10 (sem CSP/XFO), L14 (lockfile), L15 parcial (E2E agora executado),
L18/L19/L20/L21/L22 parciais (nav, repos reais, seletor, texto do
comando, deep links de search/impact), L24 (export de impacto),
L25 parcial (expansion_refs acionáveis → busca), L27/L28 (axe 0
violações em 24 scans), e o bug P0 do canvas congelado — detalhes em
`docs/cycle-6.1-architecture.md` e `docs/cycle-6-homologation.md` §7.
