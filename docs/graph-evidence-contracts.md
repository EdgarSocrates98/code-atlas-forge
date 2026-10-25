# Contratos de evidência do grafo — arestas, nós e drill-down

> Cycle 6.1 (lf-0101). O contrato de grafo do Explorer agora carrega a
> **prova** de cada relação — não só os endpoints. Fonte:
> `src/code_atlas/api/handlers.py`, `explorer/src/app/features/graph/`
> (`graph.utils.ts`, `graph-inspector.component.ts`,
> `graph-data.store.ts`), `explorer/src/app/core/api/api.service.ts`.

## 1. DTO de aresta — `GET /graph/neighbors`

Cada aresta é uma relação tipada **com metadados de prova**
(`_row_to_edge`, `handlers.py:190-204`):

| Campo | Tipo | Semântica |
|---|---|---|
| `id` / `relation_id` | int | `relations.id` — a prova tem identidade estável |
| `snapshot_id` | int | escopo que emitiu a aresta |
| `from_id` | int | occurrence_id de origem |
| `to_id` | int \| null | occurrence_id de destino — `null` quando o alvo nunca resolveu |
| `to_unresolved` | string \| null | nome livre do alvo não resolvido (ex.: `d.speak`) |
| `type` | string | `CALLS` / `IMPORTS` / `DEFINES` / `CONTAINS` / `TESTS` / … |
| `resolution_status` | string | `resolved` / `ambiguous` / `unresolved` / `external` — vocabulário real do schema (`CHECK` na migration) |
| `confidence_class` | string | classe de confiança do resolver (`resolved`/`syntactic`/`heuristic`/`inferred`) |
| `resolver` | string | origem da aresta (`tree-sitter-python`, `syntactic-scope-v1`, `scip`, …) |
| `evidence_ids` | int[] | ids de `evidence` que provam a relação — decodificados do JSON da coluna; `[]` = sem evidência registrada |

O que o Inspector faz com cada um
(`graph-inspector.component.ts:126-184`):

- `type` → título da seção;
- `from_id`/`to_id` → botões navegáveis para os nós;
- `to_id === null` → **"Alvo não resolvido: `to_unresolved`"** — alvo
  fantasma declarado, nunca endpoint inventado;
- `resolution_status` → `atlas-evidence-badge` (glifo + rótulo pt-BR)
  **mais** o status cru em `.field__raw` — nenhuma promoção de estado;
- `confidence_class` e `resolver` → valores crus em mono;
- `evidence_ids` → lista de ids, ou `Sem evidência registrada` quando
  vazio — a ausência é um fato exibido, não um erro.

## 2. Mapeamento de status → nível de evidência

`toEvidenceLevel` (`graph.utils.ts:269-274`) implementa o vocabulário
real do backend — corrigido no 6.1 após achado E2E (antes `resolved`
caía em `unknown`):

| `resolution_status` | Badge |
|---|---|
| `resolved` | `verified` ✓ |
| `ambiguous` | `possible` ◆ |
| `unresolved` | `unresolved` ✕ |
| `external` | `external` ◇ |
| `verified`/`supported`/`possible` (já no vocabulário de findings) | direto |
| qualquer outro / ausente | `unknown` ? — nunca promovido |

`confidence_class` **não** entra no badge — é exibida separada; um
`heuristic` com `resolved` não vira `possible`, e um `resolved` não
vira `supported`.

## 3. Nós — dois vocabulários, ambos com prova

**Overview (`/graph/overview`)** — um item por arquivo:
`{file_id, path, symbols, language}` → nó `file:{path}` com contagem.

**Vizinhança (`/graph/neighbors`) e detalhe (`/symbols/{id}`)** — nó de
ocorrência `{id: "occ:N", occurrence_id, symbol_id, qname, kind,
language, path, span{start,end}, parse_status}` (+ `relations_summary`
no detalhe). `parse_status` `parsed-with-errors`/`parse-failed` é
rotulado, nunca escondido.

## 4. Drill-down real — `GET /files/{id}/occurrences`

`GraphDataStore.drillFile` (lf-0101) substitui a semente por busca
textual do ciclo 6:

1. Semeia nós a partir do endpoint real — **toda** ocorrência do arquivo
   vira nó (mesmo sem arestas), paginado determinístico
   (`ORDER BY line, col, id`; `DRILL_PAGE_LIMIT=500`,
   `DRILL_MAX_PAGES=5`).
2. Expansão de vizinhança limitada — até `DRILL_NEIGHBOR_BUDGET=12`
   seeds × `GET /graph/neighbors?direction=both&limit=100` para desenhar
   arestas intra/inter-arquivo.
3. `file_id` vem do item de overview; `file_id` ausente (backend antigo)
   → ref cai para o path e `404`/`file-not-found` mapeia para estado
   `empty` honesto: *"O endpoint de ocorrências (GET
   /files/{id}/occurrences) não está disponível nesta versão da API…"*.
   **Nenhum fallback por busca é fabricado.**
4. `archSnapshot = freeze()` congela a visão macro; `exitDrill()`
   restaura nodes/edges/hidden/pins verbatim e reconstrói o conjunto de
   pins dos VMs congelados (sem `needsLayout` — posições já estão nos
   nós).
5. `fileContext` exibe `{path, expectedSymbols, occurrencesTotal}` — a
   cobertura do drill é declarada, não presumida.

## 5. Truncamento como fato de wire

- `truncated` do overview (server >limit) → `serverTruncated` → aviso
  `notice--warning` "Visualização limitada … o servidor também truncou
  resultados" (`role="status"`).
- `truncated` dos neighbors → `neighborsTruncated` → mesmo aviso.
- Cap de render (500 default / 2000 max) → `hiddenNodeCount` exato →
  "N ocultos pelo limite de M" com ação de ajuste.
- Arestas sem endpoints visíveis (alvo fora do conjunto ou filtro) →
  `hiddenEdgeCount` contado, nunca somem silenciosamente.

## 6. Verificação

- Backend: `tests/api/test_files_occurrences.py` (endpoint + DTO +
  evidence_ids decodificado), `tests/api/test_explorer_api.py`.
- Store: `graph-data.store.spec.ts` (drill real, freeze/restore, cap,
  merge dedup).
- Utils: `graph.utils.spec.ts` (`toEdgeVM`, `toEvidenceLevel` cobrindo
  resolved→verified e ambiguous→possible).
- E2E real: `flows2.spec.ts` teste **20** (edge selection mostra
  resolver/confidence/evidence) e **21** (drill dispara
  `/files/{ref}/occurrences`, nunca busca por nome — asserção sobre os
  requests observados).
