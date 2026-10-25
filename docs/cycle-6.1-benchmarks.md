# Cycle 6.1 — Benchmarks do Explorer (medidos, não estimados)

**Data do run:** 2026-10-25 · **Fonte primária:**
`benchmarks/results/graph-scale-latest.json` (run completo, 5/5 verde) +
`docs/graph-performance-homologation.md` (método, interpretação, contexto
de indexação). Números transcritos **verbatim** do JSON — onde a tabela
do doc de homologação difere em dígitos, é run anterior à correção do
canvas; as duas séries convergem na mesma conclusão e o próprio doc
registra jitter de run como ruído.

**Ambiente (do JSON):** AMD Ryzen 7 5700U, 16 cores, 31 GB RAM · Linux
`7.0.0-38-generic` · Node `v22.23.2` · Playwright `1.56.1` · Headless
Chrome 141 · **WebGL2 via SwiftShader — renderização por software, sem
GPU** (fps = piso pessimista; GPU real tende a ser mais rápida).

## 1. Latência da API — `GET /api/v1/graph/overview?limit=2000`

5 amostras curl por tamanho (ms):

| arquivos | amostras | **mediana** | items | total_files | truncated |
|---|---|---|---|---|---|
| 100 | 3.70 · 2.57 · 3.43 · 2.01 · 3.01 | **3.005** | 100 | 100 | false |
| 500 | 7.42 · 4.51 · 5.81 · 5.04 · 6.20 | **5.807** | 500 | 500 | false |
| 1,000 | 9.43 · 7.51 · 8.44 · 8.04 · 8.60 | **8.443** | 1,000 | 1,000 | false |
| 3,000 | 20.25 · 17.82 · 17.03 · 17.04 · 17.24 | **17.24** | 2,000 | 3,000 | **true** |
| 5,000 | 26.45 · 25.01 · 23.44 · 23.60 · 23.65 | **23.646** | 2,000 | 5,000 | **true** |

Latência escala com o payload retornado (2000 itens ≈ 144 KB), não com o
repo em disco — 3000 e 5000 custam ~o mesmo porque ambos devolvem a
página truncada de 2000 itens.

## 2. Carga inicial — tempo até primeiro nó (TTFN)

| arquivos | nav (ms) | fetch overview (ms) | TTFN page-clock (ms) | TTFN wall (ms) | max RAF gap (ms) | longtasks (ms) |
|---|---|---|---|---|---|---|
| 100 | 199.6 | 43.8 | 505.2 | 511 | 100 | 104 · 154 · 67 |
| 500 | 132.4 | 47.7 | 584.3 | 587 | 16.7 | 302 · 110 |
| 1,000 | 132.2 | 9.2 | 542.1 | 546 | 16.8 | 300 · 105 |
| 3,000 | 157.4 | 20.7 | 514.8 | 517 | 33.4 | 305 · 105 |
| 5,000 | 120.2 | 25.2 | 526.4 | 530 | 50.0 | 308 · 103 |

TTFN fica em ~0.5–0.59 s independente do tamanho do repo: o conjunto
visível é sempre ≤500 nós e o longtask de layout inicial (~300 ms)
incide sobre esse conjunto capado, não sobre os 5000 arquivos.

## 3. Contagem renderizada / oculta (cap default 500)

| arquivos | renderizados | ocultos | conhecidos pelo store | arestas | modo |
|---|---|---|---|---|---|
| 100 | 100 | 0 | — (sem aviso) | 0 | canvas (WebGL) |
| 500 | 500 | 0 | — | 0 | canvas |
| 1,000 | **500** | **500** | 1,000 | 0 | canvas |
| 3,000 | **500** | **1,500** | 2,000 | 0 | canvas |
| 5,000 | **500** | **1,500** | 2,000 | 0 | canvas |

Aviso visível medido no DOM (verbatim do JSON, 5000):
*"Visualização limitada: exibindo 500 de 2000 nós conhecidos (1500
ocultos pelo limite de 500) — o servidor também truncou resultados .
Expanda nós sob demanda ou ajuste o limite."*
Em 3000/5000 o servidor trunca a 2000 (`truncated: true`); os demais
nós de arquivo nunca chegam ao cliente. Nenhum run desenha 3000/5000 nós.

## 4. Pan — 3 s de arraste circular (deltas de rAF)

**No cap default — sempre 500 nós renderizados:**

| arquivos | frames | fps médio | delta médio (ms) | p95 (ms) | max (ms) | frames >33 ms | draw calls | vértices |
|---|---|---|---|---|---|---|---|---|
| 100 | 205 | **60.1** | 16.63 | 16.8 | 16.8 | 0 | 129 | 36,027 |
| 500 | 205 | **60.0** | 16.66 | 16.7 | 16.8 | 0 | 141 | 180,063 |
| 1,000 | 205 | **60.2** | 16.60 | 16.7 | 16.8 | 0 | 137 | 180,051 |
| 3,000 | 205 | **60.2** | 16.62 | 16.8 | 16.8 | 0 | 138 | 180,054 |
| 5,000 | 204 | **59.9** | 16.68 | 16.8 | 33.4 | 0 | 135 | 180,045 |

O cap funciona: com 500 nós renderizados o pan é ~60 fps **independente
do tamanho do repo** — um repo de 5000 arquivos pan igual a um de 500.
Vértices confirmam geometria real por nó (36k → 180k → 744k).

## 5. Experimento cap 500 → 2000 — o cliff real

Para datasets acima do cap, o limite foi elevado para 2000 **antes** de
qualquer relayout:

| arquivos | apply (max gap, ms) | settle (ms) | renderizados depois | ocultos |
|---|---|---|---|---|
| 1,000 | 66.7 | 1,607.1 | 1,000 | 0 |
| 3,000 | 150.1 | 1,612.3 | 2,000 | 0 |
| 5,000 | 166.7 | 1,606.9 | 2,000 | 0 |

`setNodeCap` reusa posições e **não** dispara layout — o apply block
(67–167 ms) é a atualização síncrona do graphology; os nós restaurados
ficam perto da origem até um relayout.

**Pan com cap elevado (renderizados = 1000 ou 2000):**

| arquivos | renderizados | fps médio | delta médio (ms) | p95 (ms) | max (ms) | frames >33 ms | draw calls | vértices |
|---|---|---|---|---|---|---|---|---|
| 1,000 | 1,000 | **49.5** | 20.18 | 33.4 | 66.7 | 6 | 161 | 372,111 |
| 3,000 | 2,000 | **42.5** | 23.53 | 50.1 | 116.6 | 26 | 152 | 744,084 |
| 5,000 | 2,000 | **40.9** | 24.43 | 66.7 | 133.4 | 26 | 157 | 744,099 |

O cliff está nos **2000 nós visíveis**: ~60 fps → ~41–42 fps, p95 de
50–67 ms, ~18% dos frames >33 ms — em renderização por software. Com os
mesmos 2000 nós, repos de 3000 e 5000 pan igual (42.5 vs 40.9): o limite
é a contagem visível, não o tamanho do repo.

## 6. Relayout ForceAtlas2 ("Reorganizar") — bloqueio síncrono da main thread

Medido como maior gap de RAF produzido pelo clique:

| renderizados | max gap (ms) | settle (ms) | fonte |
|---|---|---|---|
| 100 | 50.0 | 1,616.2 | `relayout` @100 |
| 500 | 266.6 | 1,607.1 | `relayout` @500 |
| 1,000 | 650.0 | 1,600.2 | `cap_experiment.relayout_at_cap` @1000 |
| 2,000 | 2,366.6 | 2,672.6 | `cap_experiment.relayout_at_cap` @3000 |
| 2,000 | 2,333.2 | 2,637.5 | `cap_experiment.relayout_at_cap` @5000 |

Superlinear como esperado para FA2 O(n·iters) (iterações clamp em
80–400 por `layoutIterations`): **~2.3–2.7 s de main thread congelada a
2000 nós** — a página fica totalmente sem resposta durante o relayout.

## 7. Correção do canvas congelado — verificação medida

Bug P0 descoberto neste benchmark (registrado em
`graph-performance-homologation.md` §"PRODUCT BUG"): após o primeiro
relayout explícito, a câmera persistida — medida sobre o layout anterior
— era restaurada, jogando todos os nós fora do frustum → **0 draw
calls**, canvas aparentemente morto. Fix aplicado em commit `03fc775`
(`relayoutCount` força fit; `reportPositions` deferido por
`queueMicrotask`). **Re-run confirma a correção** — probe de segundo
relayout:

| arquivos | 2º relayout: max gap (ms) | settle (ms) | draw calls na janela | `canvas_frozen_after_first_relayout` |
|---|---|---|---|---|
| 100 | 33.3 | 1,207.8 | 6 | **false** |
| 500 | 250.0 | 1,207.5 | 6 | **false** |
| 1,000 | 633.3 | 1,200.2 | 6 | **false** |
| 3,000 | 2,383.2 | 2,430.0 | 6 | **false** |
| 5,000 | 2,316.5 | 2,380.1 | 6 | **false** |

> Nota: a seção "PRODUCT BUG" do doc de homologação descreve a medição
> **pré-fix** (frozen=true, 0 draws) que motivou o commit; o JSON
> consolidado aqui é o run **pós-fix** — `frozen=false` + 6 draw calls
> é o resultado verificado da correção.

## 8. Heap JS (CDP `Performance.getMetrics`, MB)

| arquivos | antes do load | após render | após pan | após cap raise+relayout |
|---|---|---|---|---|
| 100 | 1.1 | 6.8 | 6.8 | — |
| 500 | 1.1 | 5.7 | 6.1 | — |
| 1,000 | 1.1 | 6.3 | 7.0 | 6.0 |
| 3,000 | 1.1 | 5.3 | 8.5 | 11.5 |
| 5,000 | 1.1 | 6.4 | 7.2 | 14.6 |

Heap ≈ 6–15 MB em todos os tamanhos — memória não é o problema; o custo
é CPU (layout) e raster (SwiftShader).

## 9. Contexto de indexação (medido antes no ciclo, mesmas fixtures)

| arquivos | símbolos | ocorrências | relações | tempo de índice | pico RSS |
|---|---|---|---|---|---|
| 100 | 500 | 500 | 700 | ~0.3 s | — |
| 500 | 2,500 | 2,500 | 3,500 | 0.66 s | 44 MB |
| 1,000 | 5,000 | 5,000 | 7,000 | 1.12 s | 59 MB |
| 3,000 | 15,000 | 15,000 | 21,000 | 3.13 s | 114 MB |
| 5,000 | 25,000 | 25,000 | 35,000 | 5.13 s | 164 MB |

## 10. O que este benchmark **não** mediu (honesto)

- **Zero arestas renderizadas** — o overview emite 0 edges por design;
  custo de aresta não exercido aqui. Grafos de símbolo (fetch-bounded
  ≤2500 nós) não foram medidos nesta fase.
- **3000/5000 nós nunca estiveram todos na tela** — API trunca a 2000 e
  canvas capa a 500/2000; a evidência de escala é o *mecanismo*
  (truncated, ocultos, cap raise), não um draw de 5000.
- **SwiftShader ≠ GPU real** — fps é piso de software; o pan de 2000 nós
  (~41–42 fps) deve ser lido como "no máximo tão ruim em hardware real,
  possivelmente melhor".
- Run único por tamanho (sem banda de variância); re-runs jitteram
  alguns ms/fps — deltas de um dígito são ruído, não sinal.
- Detalhe: o spec precisou de tracing desligado
  (`test.use({trace:'off'})`) por uma race ENOENT de artefato de trace
  no `page.close()` — não relacionado ao app; registrado para
  reprodutibilidade.

## 11. Como reproduzir

```bash
cd explorer && npm ci && npx playwright install chromium
npm run build
npx playwright test e2e/graph-scale.spec.ts   # ~5 cenários, minutos cada
```

Gera `benchmarks/results/graph-scale-{100,500,1000,3000,5000}.json` e
atualiza `graph-scale-latest.json`. Fixture: `tests/fixtures/gen_large_repo.py`
(`--files N`, repos sintéticos determinísticos em anel).
