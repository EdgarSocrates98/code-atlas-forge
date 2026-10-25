# Cycle 8.1 — Densidade do grafo e memória (teto empurrado com honestidade)

**Spec:** `factory/specs/inbox/lf-0152-cycle81-graph-density.md` (Phase 9) · **Branch:** `cycle-8.1-ga-readiness`
**Data da medição:** 2026-10-25 · **Máquina:** Ubuntu 24.04, kernel `7.0.0-38-generic`, AMD Ryzen 7 5700U (16 threads), 30 GiB RAM
**Stack medida:** Explorer **Angular 22.2.3** · sigma 3.0.2 · graphology 0.25.4 · Playwright 1.56.1 · Chromium headless (WebGL2 via **SwiftShader** — limite inferior de software, não GPU real) · Node v22.23.2

> **Caveat honesto — medição compartilhada.** A suíte `e2e/` foi executada
> também por uma sessão irmã (`npx playwright test` completo, mesma worktree)
> durante esta fase; os artefatos `benchmarks/results/graph-*` refletem a
> última corrida íntegra desta fase (21/21 testes `graph-*` verdes, ~8.6 min),
> regenerada depois que a corrida concorrente terminou. Resultados são
> reproduzíveis pelos comandos ao final.

## TL;DR — o que o ciclo 8.1 mediu de verdade

| alvo do spec | estado | número medido (Angular 22) |
|---|---|---|
| Teto de **5.000 arestas renderizadas** | **RUN — alcançado** | **5.005 arestas / 102 nós** no canvas vivo; pan a **60,2 fps** (0 frames >33 ms) |
| **10.000 nós** arquivo (cap 2.000) | **RUN — alcançado** | overview trunca em 2.000 itens (cap servidor) → cap render 2.000; pan 57,1 fps @500 / 40,9 fps @2.000 |
| FPS denso / heap / tempo de layout | **RUN** | ver tabelas abaixo — todos os números medidos, nenhum estimado |
| Atribuição dos ~400 KB/ciclo | **RUN — atribuído** | **artefato de medição DevTools**: handles remotos do Playwright/CDP retêm o `<atlas-graph-table>` destruído; controle in-page → retenção do app ≈ **0** |
| Sessão longa (pan/zoom/busca ×N) | **RUN** | 40 rounds, inclinação +65 KB/round de heap; DOM e listeners planos; telemetria limpa |
| Caminho denso `canvas+edges` no build antigo | regressão Angular 18 **resolvida** | os 3 datasets `graph-dense` agora terminam em `canvas-edges` (antes: throw `Sigma: edge can't be repaint` + `drillFile()` 400) |

---

## 1. Teto denso — 5.005 arestas no canvas vivo (`graph-ceiling`, teste 1)

Dataset determinístico novo (`tests/fixtures/gen_large_repo.py`): **500 arquivos
× 100 calls, 1 hub × 400 fan-out** → 2.501 símbolos, 2.501 ocorrências,
**103.201 relações** indexadas. Cada símbolo tem grau ≥ ~100, então cada
expansão real (`]` + `Enter` + `e` no canvas) traz ~100 arestas novas contra
o `NEIGHBOR_LIMIT=100` do cliente.

| métrica | valor |
|---|---|
| deep link `?focus=1502` → nav→ready | 706 ms (101 nós / 100 arestas iniciais, modo `canvas`) |
| cap levantado | 500 → **2.000** via input da UI |
| expansões produtivas | **90** (91 tentativas, 1 dud), ~54,5 arestas/expansão, 21,1 s de wall |
| **arestas renderizadas finais** | **5.005** (alvo ≥5.000) · 102 nós |
| **pan 3 s no grafo denso** | **60,2 fps** — 205 frames, média 16,61 ms, p95 16,8 ms, máx 16,8 ms, **0 frames >33 ms**, 126 draw calls / 38.556 vértices |
| relayout real (botão Reorganizar, FA2 no worker) | wall **677 ms** · gap máximo main-thread **383,3 ms** |
| heap JS | 0,46 MB (before) → 11,19 MB (após crescimento) → 9,7 MB (fim) |
| longtasks durante a cena | 127, 170, 150, 51, 54, 63, 421 ms |
| sigma errors / page errors | **[]** / **[]** |
| telemetria final | `sigmaLive=1, workersLive=0`, `runs: 2 started = 2 done` |

**Comparação direta com o ciclo 8 (Angular 18.2.13):** o mesmo objetivo estava
**NOT RUN** — o pico medido era **259 arestas** via expansões limitadas, e
todo caminho para `canvas+edges` estava bloqueado pelo throw do
`updateEachEdgeAttributes` + `drillFile()` 400. O build Angular 22 não só
abre o caminho como segura 5.005 arestas a 60 fps de software-render.

## 2. Escala — 100 → 10.000 arquivos (`graph-scale`, cap 500→2.000)

| arquivos | API `overview?limit=2000` mediana | itens API | total | nav→ready | max RAF gap | pan @500 (fps) | render @cap | pan @cap (fps) | p95 @cap | relayout gap @cap | heap pós-pan |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 | 2,7 ms | 100 | 100 | 332 ms | 66,7 ms | **60,0** | — | — | — | — | 9,3 MB |
| 500 | 5,1 ms | 500 | 500 | 341 ms | 66,7 ms | **57,4** | — | — | — | — | 9,7 MB |
| 1.000 | 8,4 ms | 1.000 | 1.000 | 295 ms | 66,7 ms | 57,7 | 1.000 | 49,5 | 33,4 ms | 100 ms | 10,6 MB |
| 3.000 | 17,6 ms | **2.000 (trunc)** | 3.000 | 300 ms | 50 ms | 58,1 | 2.000 | 38,9 | 66,7 ms | 133,3 ms | 10,6 MB |
| 5.000 | 24,7 ms | **2.000 (trunc)** | 5.000 | 321 ms | 66,6 ms | 58,0 | 2.000 | 40,7 | 66,7 ms | 133,3 ms | 11,0 MB |
| **10.000** | **49,3 ms** | **2.000 (trunc)** | **10.000** | 394 ms | 100 ms | **57,1** | 2.000 | **40,9** | 66,6 ms | **133,4 ms** | 10,6 MB |

Leituras honestas:

- **10.000 arquivos nunca entram todos no canvas** — o funil é
  `10.000 conhecidos → 2.000 fetch (cap servidor) → 500 render (cap default)
  → 2.000 render (cap levantado)`. O aviso de truncamento e as contagens de
  ocultos batem em todos os tamanhos. O teto de 10k exercita geração do
  fixture, indexação, latência da API (49,3 ms mediana) e o canvas no cap —
  exatamente o que o spec pediu.
- Pan @500 fica **~57–60 fps** em todos os tamanhos (incl. 10k) — o cap de
  render isola o custo, como no ciclo 6.1.
- Ao cap 2.000 o pan cai para **~39–41 fps** (SwiftShader) — dentro da banda
  histórica **38–41 fps** do ciclo 6.1.
- Relayout real (FA2 worker) no cap 2.000: gap main-thread **~133 ms** —
  comparável ao bloco de ~83–150 ms documentado no ciclo 8 (a fatória
  síncrona restante é serialização+merge no main thread, não o layout).

## 3. Atribuição do "~400 KB/ciclo" — resultado: artefato de medição

Protocolo do ciclo 8 reproduzido no `graph-ceiling` teste 2 (fixture
`500-c2-h3x150`, warm-up ×3, **10 ciclos** Lista↔Grafo *settled*):

| medida | sob instrumentação | **controle in-page** (sem handles) |
|---|---|---|
| ciclos | 10 | 4 |
| JSHeapUsedSize (pós-GC) | 7,31 → 11,81 MB (**+4,5 MB = 460,8 KB/ciclo**) | 11,81 → 10,98 MB (**−0,83 MB ≈ −212 KB/ciclo**) |
| `Memory.getDOMCounters` nodes | 27.387 → **126.809** (+~9,1k/ciclo, monotônico) | 126.809 → 126.871 (**+62 total ≈ 15/ciclo**) |
| `jsEventListeners` | 84 → 1.551 | — |
| snapshot V8 A→B | +32,95 MB; topo = `blink::UniqueElementData`, `<td>`, `<tr>`, `<button.mini>`, `Text`, `Comment`, `NodeList` — **uma árvore `atlas-graph-table` inteira por ciclo** | — |
| detached (flag V8) | 9.185 nós / 0,98 MB ao final | — |

**Como a atribuição foi provada** (diagnóstico dedicado, reproduzível):
1. `DOM.getDetachedDomNodes` mostra as raízes retidas: são árvores
   **`ATLAS-GRAPH-TABLE` inteiras** (o componente destruído pelo toggle).
2. BFS de retainer-path no `.heapsnapshot` de cada `<atlas-graph-table>`
   retido termina em `internal: "N / DevTools console" → synthetic:(Global
   handles) → GC roots` — **o retentor é o próprio DevTools** (handles
   remotos criados por `getByRole`/`waitForSelector`/`evaluate`/sessões CDP
   a cada ciclo), não uma referência do app.
3. **Controle decisivo:** os mesmos 4 ciclos executados inteiramente in-page
   (`el.click()` dentro de um único `evaluate`, zero handles de elemento,
   CDP só antes/depois) cresceram apenas **+50–62 nós totais (~12–15/ciclo)**
   e o heap pós-GC **caiu** 0,83 MB. Se o app retivesse uma tabela por
   ciclo, o controle in-page cresceria igual — não cresce.
4. Route-level destroy (long-session, §4) já indicava o mesmo: remontagens
   completas do feature via `/search`↔`/graph` mantêm DOM ~260 nós plano.

**Conclusão honesta:** o delta de ~400 KB/ciclo do ciclo 8 (e o +460 KB/ciclo
instrumentado desta corrida) é majoritariamente **artefato do protocolo de
medição** — Playwright/CDP retêm o DOM destruído via handles remotos. O
resíduo app-side medido é **≈ 0 KB/ciclo** (dentro do ruído de GC; bound
honesto: bem abaixo de 100 KB/ciclo). Não há bug de produto confirmado para
corrigir — e o suite agora registra esse caveat permanentemente em
`control_inpage_cycles` dentro do JSON. A lista de "top retained classes"
continua no JSON como evidência do *mecanismo* do artefato, não como prova
de leak do app.

## 4. Sessão longa — 40 rounds mistos (`graph-ceiling`, teste 3)

Mix por round: drag-pan + wheel-zoom + ciclo de foco por teclado + seleção;
**round-trip real para `/search` a cada 8º round** (a remontagem do feature
faz parte da sessão). Heap pós-GC amostrado por janela.

| métrica | valor |
|---|---|
| heap baseline → fim | **6,34 → 9,50 MB** |
| amostras (round → MB) | 4→6,18 · 9→7,37 · 14→8,20 · 19→7,72 · 24→8,06 · 29→8,84 · 34→8,31 · 39→9,02 |
| **inclinação (regressão linear)** | **+65,4 KB/round** |
| DOM nodes | 260–285 (plano) · documents=1 |
| jsEventListeners | ~84–88 (plano) |
| max RAF gap na sessão | 200–233 ms |
| sigma remounts (busca↔grafo) | 5 total, `sigmaLive=0`, `workersLive=0` no fim |
| runs | `5 started = 5 done` — nada pendurado |
| page errors / sigma errors | **[]** / **[]** |

O heap sobe ~3,2 MB em 40 rounds (~65 KB/round). A série é quase linear e o
DOM/listeners ficam planos — compatível com acúmulo de dados da sessão
(store cresce com expansões/seleções + caches de busca), não com leak de
estrutura. Não é evidência de leak; é a taxa de crescimento medida —
registrada verbatim para o próximo ciclo comparar.

## 5. Suítes herdadas re-rodadas no Angular 22

| suíte | resultado | observações medidas |
|---|---|---|
| `graph-dense.spec.ts` | 3/3 PASS | **o caminho `canvas+edges` existe de novo**: `drill_attempt.outcome=canvas-edges`, `canvas_alive_with_edges=true` nos 3 datasets (no Angular 18 terminava em `state:error`/throw). Pico de arestas via expansão de tabela: 163–220 (a suite é fetch-bounded por design — o teto real agora é o `graph-ceiling`). Pan ego ~59,5–59,9 fps; pan arch ~55,5–56,1 fps |
| `graph-hardiness.spec.ts` | 6/6 PASS | mount/unmount ×20: heap +7,39 MB (≈370 KB/ciclo **instrumentado** — mesmo artefato do §3, drivers idênticos); `sigmaTotal=24, sigmaLive=0`, `workersTotal=24, workersLive=0`, `24 started = 14 done + 10 cancelled`, listeners globais net −6, rapid-nav ×10 sem worker vivo no /search |
| `graph-async-layout.spec.ts` | 3/3 PASS | relayout mantém main-thread <300 ms; cancel aborta sem commit nem erro; revisão mid-layout descarta stale run limpo |
| `graph-scale.spec.ts` | 6/6 PASS | tabela do §2 — inclui **10.000** pela primeira vez |
| `graph-ceiling.spec.ts` (novo) | 3/3 PASS | §§1, 3, 4 |

## 6. Tabela comparativa — Angular 18.2.13 (ciclos 6.1/8) × Angular 22.2.3 (8.1)

| métrica | antes (Angular 18) | agora (Angular 22) | leitura |
|---|---|---|---|
| pan @500 nós (overview) | ~60,2 fps | 57,1–60,0 fps | paridade dentro de run-to-run jitter |
| pan @cap 2.000 | ~38–41 fps | 38,9–40,9 fps | paridade — SwiftShader-bound |
| bloco main-thread em layout | ~83–150 ms (pós-async); ~2,3 s no layout síncrono do 6.1 | apply cap 100–167 ms; relayout @2.000 ~133 ms; denso 5k-arestas 383 ms/677 ms wall | assíncrono mantido; o gap denso agora inclui arestas de verdade |
| arestas máximas no canvas | **259** (teto de 5k NOT RUN) | **5.005** | teto decretado alcançado e homologado |
| `canvas+edges` a partir de drill | **bloqueado** (`can't be repaint` + 400 `occ:1`) | `canvas-edges` nos 3 datasets | regressões do build antigo resolvidas |
| 10.000 arquivos | NOT RUN | **RUN** — funil 10k→2k→500/2000 medido, API 49,3 ms mediana | teto de dataset alcançado |
| heap por ciclo mount/unmount | +8,01 MB/20 (≈400 KB/ciclo), não atribuído | +4,5 MB/10 (460 KB/ciclo) **instrumentado**; controle in-page ≈ **0** | a "leak" era artefato DevTools; app-side residual ≈ 0 (bound <100 KB/ciclo) |
| sessão longa | NOT RUN | 40 rounds, +65 KB/round, DOM/listeners planos | baseline registrada para comparação futura |
| FPS em canvas com arestas | NOT RUN | 60,2 fps @5.005 arestas; 59,5–59,9 fps ego pan | primeira medição real de arestas |

## 7. NOT RUN declarado

- **GPU real / FPS em hardware:** toda medição é SwiftShader (headless). Os
  números de FPS são limites inferiores de software; a banda ~40 fps @2.000 é
  comparável ao histórico sob o mesmo renderer.
- **`worker_reported` ms no relayout denso:** a telemetria não expõe o tempo
  reportado pelo worker nesta superfície (campo gravado como `"—"`); medido
  por wall + max RAF gap.
- **Caminho inverso de arestas >5.005:** o teto *decretado* (5k) foi
  alcançado; pushes além disso ficam para um ciclo futuro com fixture maior.
- **Heap attribution abaixo de ~100 KB/ciclo:** o método usado não separa
  resíduo app <~50–100 KB/ciclo de ruído de GC — bound declarado, não leak
  confirmado.
- **Cross-browser/mobile no teto denso:** fora de escopo (Chromium-only por
  spec; ver `docs/cycle-8.1-platform-homologation.md`).

## 8. Reprodução

```bash
cd explorer
npm run build                                   # Angular 22 produção → dist/
npx playwright test e2e/graph-                  # 21 testes, ~8,6 min (1 worker)
# ou individualmente:
npx playwright test e2e/graph-ceiling.spec.ts   # teto 5k + atribuição + sessão
npx playwright test e2e/graph-scale.spec.ts     # 100→10.000 arquivos
```

Artefatos brutos (tudo medido, nada estimado):

- `benchmarks/results/graph-ceiling-{dense_ceiling,heap_attribution,long_session}.json`
- `benchmarks/results/graph-ceiling-latest.json` (merge + ambiente)
- `benchmarks/results/graph-scale-{100,500,1000,3000,5000,10000}.json` + `-latest.json`
- `benchmarks/results/graph-dense-*.json` + `-latest.json`
- `benchmarks/results/graph-hardiness-latest.json`

## 9. Mudanças deste spec

| arquivo | mudança |
|---|---|
| `explorer/e2e/graph-ceiling.spec.ts` | **novo** — 3 testes: teto denso ≥5k arestas; atribuição heap com diff de snapshots V8 + **controle in-page** que separa artefato DevTools de retenção do app; sessão longa ×40 rounds. Persistência por teste em `benchmarks/results/graph-ceiling-*.json` + merge no `-latest` |
| `explorer/e2e/graph-scale.spec.ts` | aditivo — `SIZES` inclui `10000` (+comentário do funil cap) |
| `docs/cycle-8.1-graph-density.md` | **novo** — este relatório |

Nenhuma mudança em `src/` (produção): a atribuição provou que não há bug
confirmado — o delta de heap é artefato de medição e as contradições do
Angular 18 (`Sigma repaint`, `drillFile 400`) já estavam resolvidas no build
migrado.
