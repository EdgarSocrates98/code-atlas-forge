# Estabilidade de layout do grafo — contrato de congelamento

> Cycle 6 (lf-0088 Phases 5–6 · lf-0089 Phases 7–8). Contrato
> verificado em `explorer/src/app/features/graph/`. O problema
> endereçado: em ferramentas de grafo, expandir um nó ou mudar um filtro
> tipicamente **re-executa o layout inteiro** e os nós "pulam" —
> destruindo o mapa mental do usuário. O `GraphDataStore` proíbe isso
> por construção.

## 1. O invariante central

`graph-data.store.ts` (linhas ~47-53, comentário de contrato no topo):

> `dataVersion.needsLayout` **é o único sinal que pode disparar uma
> passada de layout**. Tudo que muda dados (seleção, filtro, merge
> incremental, mudança de cap, saída de drill) versiona com
> `bumpVersion(false)` — os nós ficam exatamente onde estavam.

O `dataVersion` é um `signal<GraphDataVersion>` com `{version,
needsLayout}` (`graph.models.ts`). O canvas (`graph-canvas.component.ts`)
observa `dataVersion()`:

- `version` mudou → reconcilia a estrutura do grafo Sigma (adições/
  remoções/atributos), **nó a nó**, sem resetar;
- `needsLayout == true` → **aí sim** roda ForceAtlas2 e reporta as
  posições de volta via `store.reportPositions()`;
- `needsLayout == false` → nó novo nasce com posição determinística
  própria e nenhum nó existente se move.

### Quem arma `needsLayout = true` (lista fechada)

| Operação | Justificativa |
|---|---|
| `loadOverview()` / carga inicial | contexto novo: não há posições a preservar |
| `drillFile()` (entrada no arquivo) | substitui a vista por um contexto novo |
| `startAtSymbol()` (deep-link `?focus=occ:N`) | primeira carga sem contexto anterior |
| `requestRelayout()` (botão "Reorganizar") | única forma de o **usuário** pedir relayout — respeita nós fixados |

Tudo o resto — `expandNode()`, `applyCap()`, `setNodeCap()`,
`exitDrill()`, seleção, `transientError` — chama `bumpVersion(false)`.

## 2. Posições determinísticas sem relayout

Nós que entram depois do layout inicial recebem posição **sem** mover os
vizinhos (`graph-data.store.ts`, `graph.utils.ts`):

- **Vizinhos de expansão** — `spiralPosition(seedPos, i)`: espiral
  determinística ao redor do nó-semente. Nós novos de um mesmo seed
  nunca colidem entre si e a espiral é a mesma para a mesma entrada
  (testado em `graph.utils.spec.ts`: mesmo `(seed, i)` → mesma posição;
  `i` diferente → posições diferentes).
- **Nós sem posição em merges** — `hashPosition(nodeId, i, total)`:
  posição derivada do id estável, idempotente por definição
  (mesmo id → mesma posição, sempre).
- **Posições reportadas pelo renderer** — `reportPositions(positions)`
  escreve `x`/`y` de volta **nos nós do `nodesMap` e também nos do
  `overflowMap`** (linha ~508): mesmo um nó oculto pelo cap mantém a
  posição que tinha quando renderizado — ao reentrar, não "salta".

## 3. Cap e overflow — dados ocultos, nunca destruídos

```
nodesMap   ─ nós visíveis (≤ nodeCap)
overflowMap ─ nós conhecidos acima do cap (dados + posição preservados)
hiddenNodeCount() = overflowMap.size   → alimenta o banner da UI
totalKnownNodes() = nodes + hidden     → "exibindo X de Y"
```

- `DEFAULT_NODE_CAP = 500` (`graph.models.ts`); o input da toolbar
  permite 10–2000 (passo 50) e `setNodeCap()` faz clamp
  `Math.max(10, Math.min(2000, …))` (store linha ~490).
- `applyCap()` (linha ~625): prioridade de permanência = **pin +
  seleção + nós expandidos**, depois ordem de inserção; quem sai vai
  para `overflowMap` **com sua posição**. Aumentar o cap restaura do
  overflow — nunca re-busca, nunca re-posiciona.
- Os limites do servidor são paralelos e declarados: overview
  `limit` ≤ 2000 (`handlers.py`), neighbors ≤ 100 usados pela UI
  (`NEIGHBOR_LIMIT`), drill usa busca com cap de 200/página × 5 páginas
  + 12 seeds de vizinhança (`DRILL_*` em `graph.models.ts`).

Banners que a UI renderiza quando há perda (`graph.component.html`
linhas ~109-130):

- `hiddenNodeCount() > 0` → "Visualização limitada: exibindo X de Y nós
  conhecidos (N ocultos pelo limite de CAP)";
- `serverTruncated()` → o servidor truncou o overview;
- `neighborsTruncated()` → expansão de vizinhos truncada;
- `fileContext()` → aviso de cobertura do drill por arquivo
  ("cobertura limitada pela busca" — ver limitações);
- `rendererFailed()` → fallback para a tabela (§6).

## 4. Freeze/restore do drill por arquivo

`drillFile(path, expectedSymbols)` salva a vista de arquitetura inteira
em `FrozenView` (`archSnapshot`, linhas ~36-44, ~657-670) — nós
visíveis, `overflowMap`, seleção — limpa o mapa e monta o contexto do
arquivo. `exitDrill()`:

1. restaura `nodesMap` + `overflowMap` **verbatim** do frozen view;
2. zera `fileContext`;
3. `bumpVersion(false)` — as posições já estão nos nós, o canvas volta
   exatamente ao estado pré-drill, sem relayout.

O breadcrumb oferece "← Voltar sem perder o contexto"
(`graph.component.html:95-105`) e deep-link sem contexto congelado cai
em reload de overview (store linha ~304).

## 5. Guarda de geração para async

Toda operação assíncrona (overview, drill, expansão, startAtSymbol)
incrementa uma geração; respostas que chegam de uma geração obsoleta são
descartadas sem tocar nos sinais — um clique rápido em dois arquivos não
pode intercalar resultados e reordenar o grafo em cima do usuário
(`graph-data.store.ts`, campo de geração + checks no topo dos awaits).

## 6. Renderizador e câmera

`graph-canvas.component.ts` — Sigma 3/WebGL sobre graphology +
`graphology-layout-forceatlas2`:

- Layout ForceAtlas2 roda **uma vez por `needsLayout`** com
  `iterations = min(400, max(80, 60000 / order))`, `adjustSizes`,
  `strongGravityMode`, `customBBox` com margem, e nós fixados
  restaurados às posições gravadas antes do fit;
- Câmera: `autoRescale: false`, `autoCenter: false`,
  `minCameraRatio 0.02`, `maxCameraRatio 10` — pan/zoom nunca podem
  "perder" o grafo; o ajuste inicial (`fitView`) usa
  `stagePadding` fixo;
- `prefers-reduced-motion` → durações de câmera/animação zeradas;
- Falha de WebGL → `markRendererFailed()` → `GraphTableComponent`
  assume (paridade WCAG: mesmas ações — selecionar, expandir, fixar,
  drill, abrir símbolo — e o mesmo aviso de `hiddenNodeCount`).

## 7. Resumo do contrato (para revisão)

| Regra | Mecanismo |
|---|---|
| Layout só quando pedido | `needsLayout` é o único gatilho; lista fechada de quem o arma |
| Posições congeladas | merges/filtros/cap/seleção versionam com `bumpVersion(false)` |
| O usuário pode relayoutar | `requestRelayout()` explícito, respeita pins |
| Oculto ≠ destruído | `overflowMap` guarda dados e posições; restaura ao abrir cap |
| Novos nós determinísticos | `spiralPosition` (expansão) / `hashPosition` (merge) |
| Async não reordena | generation-guard descarta resposta obsoleta |
| Drill restaura contexto | `FrozenView` + `exitDrill` com `bumpVersion(false)` |
| Câmera não se perde | razões min/max fixas, sem autoRescale/autoCenter |
| Truncamento declarado | `hiddenNodeCount` + `serverTruncated` + `neighborsTruncated` na UI |

## 8. Referências

- Store/contrato: `explorer/src/app/features/graph/graph-data.store.ts`
  (+ spec `graph-data.store.spec.ts` — invariantes de `needsLayout`,
  cap/overflow, freeze/restore).
- Modelos/constantes: `graph.models.ts`; posições: `graph.utils.ts`
  (+ spec).
- Canvas: `graph-canvas.component.ts`; host/toolbar/banners:
  `graph.component.ts` + `.html`; fallback: `graph-table.component.ts`;
  inspetor: `graph-inspector.component.ts`.
- ADR de renderização: `explorer/docs/graph-rendering.md`.
- Escala medida: `docs/cycle-6-benchmarks.md` (cap 100/500/2000 por
  teste unitário — **escrito, não executado**; 10k/25k nós **não
  medidos** — ver `docs/cycle-6-limitations.md`).
