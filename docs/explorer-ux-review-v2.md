# Explorer — revisão de UX v2 (Cycle 6.1)

> Revisão de UX atualizada após o hardening 6.1. Suplanta
> `docs/cycle-6-ux-review.md` §11 (achados F1–F12 — maioria fechada).
> Evidência: **screenshots reais** em `docs/screenshots/` capturados por
> `explorer/e2e/screenshots.spec.ts` contra o `code-atlas explorer` real,
> **24 scans axe** (0 violações — `docs/explorer-accessibility-report.md`),
> e os flows E2E `flows2.spec.ts`. Nada é afirmado sem evidência citável.

## 1. Capturas de tela — o que existe de fato

`screenshots.spec.ts` captura as 9 rotas × {desktop 1280×720, mobile
375×812} × {light, dark} = **36 cenários**, cada um com a asserção de
overflow horizontal (nenhum elemento pode empurrar a página além do
viewport — tripwire de regressão visual).

| Conjunto | Estado | Evidência |
|---|---|---|
| Desktop ×2 temas (18 PNGs) | **CAPTURADO + PASS** | `docs/screenshots/{route}-desktop-{light,dark}.png` — 18 arquivos presentes |
| Mobile ×2 temas (18 cenários) | **ACHADO REAL + FIX APLICADO — re-run pendente** | Na primeira execução **todos os cenários mobile falharam** na asserção de overflow (~217 px de overflow horizontal a 375 px — `test-failed-1.png`/`error-context.md` em `explorer/test-results/`). Correção aplicada no working tree: `shell.component.css` (`minmax(0,1fr)` na coluna principal + media ≤700 px vira nav horizontal) e `graph.component.css`. **PNGs mobile ainda não regenerados — fix não re-verificado** |

**Leitura honesta:** o spec encontrou um bug visual real (layout vazava
o viewport em telas estreitas) e o fix está no working tree não
comitado. Mobile fica **NOT VERIFIED** até o re-run produzir os PNGs —
não é "passou".

## 2. Estados verificados por screenshot (desktop)

Cada PNG existe e mostra: shell com sidebar de 9 itens + selo de
contexto no toolbar; tela de feature correspondente renderizada em tema
claro e escuro (tema forçado via `localStorage atlas-theme` antes do
boot — ver spec). Rotas cobertas: dashboard, repositories, graph,
search, impact, revisions, context, diagnostics, settings.

## 3. Achados F1–F12 do ciclo 6 — status

| # | Achado original | Status 6.1 | Evidência |
|---|---|---|---|
| F1 | `/repositories` e `/revisions` fora do `NAV_ITEMS` | **FECHADO** | `shell.component.ts:31-41` — 9 itens incluindo ambos |
| F2 | `ApiService` chamava rotas inexistentes | **PARCIAL** | telas usam só tipos v1; sobra `getContextPack()` (`api.service.ts:507`) apontando `/symbols/{id}/context-pack` — código morto declarado (L19) |
| F3 | `SnapshotStatus` minúsculo | **FECHADO** | `ApiSnapshotItem.status: string` cru; `isReadySnapshot` compara `READY` uppercase (`explorer-context.service.ts:26-28`) |
| F4 | `risk` TS ≠ `RiskAssessment` real | **FECHADO** | `ApiRiskAssessment{category, uncertainty, reasons, formula_version}` em `api.service.ts:320-325` |
| F5 | Vocabulários de status misturados | **FECHADO** | `toEvidenceLevel` mapeia o vocabulário real (`resolved→verified`, `ambiguous→possible`) sem promoção — `graph.utils.ts:263-274` |
| F6 | `ContextPack` TS ≠ envelope real | **FECHADO** | `ApiContextPack` espelha `manifest/budget/items/diagnostics/expansion_refs` — `api.service.ts:210-220` |
| F7 | `Repository` sem `identity_key` | **FECHADO** | `ApiRepositoryItem{id, identity_key, root_handle}` — `api.service.ts:50-54` |
| F8 | `unknown` sem slot de cor | **FECHADO** | `EVIDENCE_MARKERS` inclui `unknown:'?'` — `graph.utils.ts:277-284` |
| F9 | Probe de `/search` com `q=""` | **FECHADO** | telas sondam `/health`/endpoints reais; placeholder de search removido |
| F10 | Badge de evidência ausente | **FECHADO** | `evidence-badge.component.ts` + `atlas-evidence-badge` no inspector e telas |
| F11 | Contraste AA não auditado | **FECHADO** | axe wcag22aa + best-practice em 24 scans, 0 violações — `explorer-accessibility-report.md` |
| F12 | Seletor de repo somente-leitura | **FECHADO** | `<select>` funcional no toolbar (`shell.component.html:24-46`) + `ExplorerContextService.selectRepository` |

## 4. Novos achados 6.1 (registrados, não acusados)

| # | Achado | Onde | Ação sugerida |
|---|---|---|---|
| N1 | **Overflow horizontal em mobile** — ~217 px a 375 px em todas as rotas | `shell` grid + conteúdo min-width | fix aplicado no working tree (shell/graph CSS); **re-run do spec + PNGs pendentes** |
| N2 | Endereço da API era hardcoded `127.0.0.1:8741` no estado offline | `state-block.component.ts` | **corrigido** — `apiOrigin()` deriva de `ATLAS_API_BASE_URL`/`document.location` (qualquer `--port`) |
| N3 | Relayout FA2 a 2000 nós congela a página ~2.3–2.7 s | `runLayout()` síncrono | declarado no painel de diagnósticos + doc; worker/chunked é follow-up |
| N4 | Command palette inexistente | shell | backlog P2 declarado (L15) |
| N5 | `expansion_refs` abrem busca, não re-run de contexto nível+1 | `context.component.ts` | degradação declarada (L18) |

## 5. Cobertura E2E que suporta esta revisão

| Spec | Testes | O que prova para UX |
|---|---|---|
| `flows.spec.ts` | 10 | boot, rotas, tema persistente, teclado, offline explícito, fallback SPA |
| `flows2.spec.ts` | 14 | repos reais+contexto, deep links `?repo=&snapshot=`/`?q=`/`:id`, URL sync, exports JSON/MD com download real, canvas vivo pós-tema, evidência de aresta, drill via endpoint real, restaura câmera, sidebar persiste, kill/restart do subprocesso → offline→recovery |
| `security.spec.ts` | 8 | guards de Host/Origin/413/traversal/cookies/headers/preflight |
| `a11y.spec.ts` | 29 | 24 scans axe + skip link + focus-visible + nav por teclado + reduced-motion |
| `graph-scale.spec.ts` | 5 | comportamento medido a 100–5000 arquivos (aviso de truncamento, cap, relayout) |
| `screenshots.spec.ts` | 36 | captura real + tripwire de overflow (18 PASS desktop, 18 mobile em re-verificação) |
| **Total** | **102** | `npx playwright test --list` = 102 testes em 6 arquivos |

## 6. Veredito

**APROVADO COM RESSALVAS** para desktop (screenshots + axe + E2E);
**mobile em re-verificação** (fix aplicado, prova pendente). Ressalvas
ativas: N1 (mobile), L15–L22 de `docs/cycle-6.1-limitations.md`.
Nenhum dado simulado apresentado como real; nenhum estado de evidência
comunicado só por cor; `partial-analysis`, `truncated`, nós ocultos e
alvos não resolvidos são todos declarados na UI.

### Registro

```
Tela:            todas (9 rotas + estados)
Spec de impl.:   lf-0097…lf-0108
Revisor:         agente (subagent docs pass)  Data: 2026-10-25
Ambiente:        branch cycle-6.1-explorer-hardening · code-atlas explorer
Checklist:       ✅ desktop/a11y/E2E  ⚠ mobile (fix pendente de prova)  ❌ nenhum
Evidência:       docs/screenshots/*.png · explorer-accessibility-report.md ·
                 playwright --list (102 testes) · graph-scale-latest.json
Veredito:        APROVADO COM RESSALVAS
```
