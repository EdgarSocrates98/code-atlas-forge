# Cycle 6 — Limitações e gaps conhecidos

> lf-0095 (Phases 19–22). Inventário honesto de tudo que **não** está
> provado, é degradação por design, ou é bug/pendência aberta.
> Convenção: **[gap]** ausência de feature · **[degradação]**
> comportamento declarado, não erro · **[não medido]** sem evidência de
> execução · **[bug]** desvio código↔intenção.

## 1. Performance e escala

| # | Item | Estado | Evidência |
|---|---|---|---|
| L1 | Benchmark de 10k/25k nós | **[não medido]** | `docs/cycle-6-benchmarks.md` §3 — existe teste unitário de cap em 100/500/2000 (escrito, **não executado**); 10k+ não tem fixture e o ADR de renderização (`explorer/docs/graph-rendering.md`) fala em "headroom", não em número medido. Não reivindicar 10k. |
| L2 | Latência p50/p95 da API | **[não medido]** | `cycle-6-benchmarks.md` §2 — NOT RUN. |
| L3 | Bundle medido pós-features | **[não medido]** | budgets em `angular.json`; baseline reportou 257.6 kB iniciais **antes** das features finais — revalidar. |
| L4 | Sigma/WebGL a 500 nós visíveis (frame-time real) | **[não medido]** | `cycle-6-benchmarks.md` §3 — requer browser + profiler. |

## 2. Backend / contrato

| # | Item | Estado | Evidência |
|---|---|---|---|
| L5 | `GET /graph/neighbors` não emite `evidence_ids` | **[gap]** | `handlers.py:201-209` seleciona só `id, from_id, to_id, type, resolution_status, resolver`; o campo `evidence_ids` existe no DTO do frontend (`api.service.ts:405`) e o inspetor o renderiza quando presente — hoje, nunca. |
| L6 | Drill-down de arquivo via semente de busca | **[degradação declarada]** | `drillFile`/`searchFileOccurrences` semeiam via `GET /search` estrutural (`fileStem` + `.`), não por endpoint "ocorrências do arquivo"; a UI mostra "cobertura limitada pela busca" (`fileContext`). Cobertura é aproximada por construção. |
| L7 | Canal vetorial indisponível em conexão read-only | **[degradação declarada]** | `search/engine.py:224-240` — `PRAGMA query_only` ativo → diagnóstico `vector-unavailable-readonly` (embeddings materializam = escrita). Baseline registra o crash original e a correção. |
| L8 | Sem rate limiting | **[gap] declarado** | `explorer-product-spec.md` §7 — "loopback single-user — adiado". Não existe contador/janela em `server.py`. |
| L9 | Sem `GET /impact/{id}` (histórico) | **[gap] declarado** | `explorer-api.md` NOT RUN — backend só tem `POST` síncrono efêmero. |
| L10 | Sem `Content-Security-Policy` / `X-Frame-Options` | **[gap]** | `docs/explorer-security.md` "Gaps" — headers fixos são `nosniff`/`no-store`/`no-referrer`; recomendado `default-src 'self'` como follow-up. |
| L11 | Cap de resposta (4 MiB) sem teste | **[não medido]** | `explorer-security.md` gap #2 — `MAX_RESPONSE` degrada para 500 tipado, mas nenhum teste o dispara. |
| L12 | Estratégia `vector` selecionável na UI sem checar capabilities | **[gap menor]** | `/search` oferece as 5 estratégias; o backend responde com diagnóstico de indisponibilidade, mas a UI não esconde a opção indisponível (ux-review pede gate por `/capabilities`). |

## 3. Dependências de desenvolvimento (npm)

| # | Item | Estado | Evidência |
|---|---|---|---|
| L13 | ~55 vulnerabilidades `npm audit` | **[degradação aceita]** | `docs/cycle-6-baseline.md` — transitivas da toolchain Angular 18 (família esbuild/vite/webpack-dev); **runtime limpo**. `audit fix --force` rejeitado por ser breaking. **Não re-verificado** nesta sessão — `package.json` mudou desde (Playwright), o número atual pode divergir. |
| L14 | `package-lock.json` sem entradas do Playwright | **[bug de empacotamento]** | `package.json` tem `@playwright/test@1.48.2` + scripts `e2e`/`e2e:report`, mas grep no lock retorna 0 matches — `npm ci` falha; `npm install` corrige o lock. |

## 4. E2E e testes

| # | Item | Estado | Evidência |
|---|---|---|---|
| L15 | Playwright E2E (18 testes: 10 flows + 8 security) | **[não medido] — pendência lf-0094** | arquivos existem (`explorer/e2e/`, `playwright.config.ts`); `docs/cycle-6-e2e.md` declara explicitamente "not been executed" — sem shell na sessão. Spec `lf-0094-security-e2e` continua em `factory/specs/inbox/`. |
| L16 | Testes Angular novos desde o baseline | **[não medido]** | 14 `*.spec.ts` em `src/app` hoje; baseline reportou 13/13 — os specs de scale/inspector/revisions/etc. foram escritos depois e não têm evidência de execução registrada. |
| L17 | Suíte Python | **[não re-executado aqui]** | baseline: 539 testes + ruff + mypy. Grep atual conta 528 `def test_` sob `tests/` — divergência sem explicação verificada (possível parametrização ou remoção posterior). Rodar antes de merge. |

## 5. Produto / UX

| # | Item | Estado | Evidência |
|---|---|---|---|
| L18 | `/repositories` e `/revisions` fora da navegação | **[bug]** | rotas existem (`app.routes.ts`), mas `NAV_ITEMS` (`shell.component.ts:11-19`) tem 7 itens — achado F1 do `cycle-6-ux-review.md`, ainda presente no código. |
| L19 | `/repositories` ainda é placeholder | **[gap]** | `repositories.component.ts` usa `feature-placeholder` e só sonda a API; não lista repos/snapshots (F2). |
| L20 | Seletor de repositório do toolbar é somente-leitura | **[gap]** | `RepositoryContextService` existe mas nada popula `repositories()`; o selo do toolbar mostra "Nenhum repositório selecionado" sempre (R9/F12). |
| L21 | Texto `code-atlas serve` em UI | **[bug]** | comando inexistente citado em `dashboard.component.ts` (estado vazio) e `state-block.component.ts` (offline) — o correto é `explorer`/`api`. |
| L22 | `ApiService` legado aponta rotas inexistentes | **[gap] código morto** | `api.models.ts` + `getSnapshot/getSymbol/getRelations/getImpact/getContextPack` (`api.service.ts`) referem-se a endpoints do scaffold que a API real nunca teve (Q1/Q5–Q7 do product-spec). Telas de feature usam os tipos v1 — exceto o probe do placeholder. |
| L23 | Sem deep-linking de formulários | **[gap] alvo-C6 não feito** | `?base=&target=` em `/impact`/`/revisions` e `?q=` em `/search` não restauram estado (explorer-ui-ux §1.2). `/graph?focus=occ:N` e `/symbols/:id` funcionam. |
| L24 | Impacto sem export local | **[gap]** | spec C10 previa copiar `ImpactResult`; só `/context` exporta (JSON + markdown). |
| L25 | `expansion_refs` não acionáveis | **[gap]** | `/context` lista expansion refs, mas não há botão que re-dispare `POST /context` a partir deles (alvo-C6 E6). |

## 6. Plataforma

| # | Item | Estado | Evidência |
|---|---|---|---|
| L26 | Explorer em Windows | **[não medido] — NOT RUN** | CI roda Python em `windows-latest`, mas não há job Node; bind/webbrowser/npm não verificados. Detalhes em `docs/explorer-installation.md` §5. |
| L27 | Auditoria WCAG (axe/Lighthouse) | **[não medido] — lf-0093** | provisões no código (badges glifo+rótulo, teclado, reduced-motion, skip link), sem auditoria executada. |
| L28 | Auditoria de contraste automatizada | **[não medido]** | `explorer-design-system.md` declara escolha por luminância aproximada, pendente de axe/Lighthouse. |

## 7. O que **não** é limitação (esclarecimentos)

- **Sem "grafo inteiro"** — ausência deliberada: o modelo é macro
  (overview por arquivo) → meso (vizinhança) → micro (símbolo). Não é
  bug; é o contrato bounded (`explorer-ui-ux.md` §2).
- **API read-only** — `mode=ro` + `query_only` é a decisão central
  (ADR-0009), não uma restrição acidental.
- **Sem telemetria** — proibido por spec; ausência é o requisito.
- **Snapshot FAILED/STALE visível mas não selecionável** — comportamento
  desenhado (R10 do product-spec).

## 8. Referências

- Baseline e medidas anteriores: `docs/cycle-6-baseline.md`,
  `docs/cycle-6-benchmarks.md`, `docs/cycle-6-e2e.md`,
  `docs/explorer-security.md`.
- Revisão UX (F1–F12): `docs/cycle-6-ux-review.md`.
- Questões abertas Q1–Q7: `docs/explorer-product-spec.md` §8.
- Matriz de homologação: `docs/cycle-6-homologation.md`.
