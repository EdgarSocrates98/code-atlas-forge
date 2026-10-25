# Cycle 6.1 — Evidência de contratos de API (o que entrou no wire)

> Delta de contrato sobre `docs/explorer-api.md` / ciclo 6. Cada campo
> citado abaixo é lido de `src/code_atlas/api/handlers.py` e coberto por
> `tests/api/test_files_occurrences.py` e/ou `tests/api/test_api_hardening.py`.
> Sem campos inventados: onde o backend não emite, está marcado.

## 1. `GET /graph/overview` — campo adicionado

| Campo | Novo? | Fonte |
|---|---|---|
| `items[].file_id` | **SIM** | `handlers.py:174` — `f.id AS file_id` no SELECT agregado |

Restante do envelope inalterado: `snapshot_id`, `repository_id`,
`items[{path, symbols, language}]`, `total_files`, `truncated`
(`handlers.py:185-187`). `limit` default 500, cap 2000.

**Efeito:** o drill-down da UI endereça `GET /files/{file_id}/occurrences`
diretamente; o fallback por path do cliente (`ApiFileOccurrencesScope.path`)
só dispara contra backends antigos sem `file_id`.

## 2. `GET /graph/neighbors` — DTO de aresta completo

Antes (ciclo 6): `id, from_id, to_id, type, resolution_status, resolver`.

Agora (`_row_to_edge`, `handlers.py:190-204`):

```json
{
  "id": 3,
  "relation_id": 3,          // NOVO — alias explícito do relations.id
  "snapshot_id": 1,          // NOVO — escopo que emitiu a aresta
  "from_id": 6,
  "to_id": 8,                // NULL quando o alvo nunca resolveu
  "to_unresolved": null,     // NOVO — nome livre do alvo não resolvido
  "type": "DEFINES",
  "resolution_status": "resolved",
  "confidence_class": "resolved",   // NOVO — vocabulário do resolver
  "resolver": "tree-sitter-python",
  "evidence_ids": [1]        // NOVO — decodificado de relations.evidence_ids
}
```

Detalhes de contrato:

- `evidence_ids` é `json.loads` da coluna (`handlers.py:203`) — array de
  inteiros real, não string; `[]` quando não há evidência registrada
  (fecha L5 do ciclo 6).
- `to_id: null` + `to_unresolved: <texto>` é como o alvo-fantasma chega —
  o cliente desenha "Alvo não resolvido: X" sem fabricar endpoint
  (`graph-inspector.component.ts:152-154`, `toEdgeVM` em
  `graph.utils.ts:169-183`).
- `nodes[]` inalterado (`occ:{id}`, `occurrence_id`, `symbol_id`, `qname`,
  `kind`, `language`, `path`, `span{start,end}`, `parse_status`).
- Envelope: `{snapshot_id, edges[], nodes[], truncated}`; `direction ∈
  in|out|both`, `limit` default 50 cap 200.

## 3. `GET /files/{id}/occurrences` — endpoint novo

`handlers.py:242-288` + rota em `handlers.py:438`.

**Request:** `GET /api/v1/files/{file_id}/occurrences?limit=&offset=
&snapshot_id=` (`limit` default 200, cap 500; `offset` ≥0; `snapshot_id`
opcional validado repo-scoped READY).

**Response:**

```json
{
  "file_id": 4,
  "repository_id": 1,
  "snapshot_id": 1,
  "path": "src/hello.py",
  "language": "python",
  "items": [
    {
      "id": "occ:8",
      "occurrence_id": 8,
      "symbol_id": 8,
      "qualified_name": "src.hello.hello",
      "kind": "function",
      "span_start": 10,
      "span_end": 42,
      "line": 1,
      "col": 0,
      "content_hash": "…",
      "parse_status": "parsed"
    }
  ],
  "total": 4,
  "limit": 200,
  "offset": 0,
  "truncated": false
}
```

Garantias (verificadas em `test_files_occurrences.py`):

- Escopo derivado do arquivo: `repository_id` vem de
  `source_files.repository_id` — um `file_id` sempre resolve o **seu**
  repo; não existe parâmetro para pescar arquivo de outro repo.
- `file_id` inexistente → 404 `file-not-found`; `file_id` não numérico →
  `bad-param`; snapshot não-READY → 409 `snapshot-not-ready`.
- Ordem estável `ORDER BY o.line, o.col, o.id` — paginação por
  `offset`/`limit` é determinística (`total` é o count total do arquivo
  no snapshot; `truncated = offset + len(items) < total`).

## 4. Respostas de erro / hardening que viraram wire

| Resposta | Quando | Fonte |
|---|---|---|
| `429 {"error":{"code":"rate-limited"}}` + `Retry-After: <s>` | >300 req/min por IP em `/api/v1` | `server.py:224-230` |
| `408 {"error":{"code":"request-timeout"}}` | timeout disparado dentro do dispatch | `server.py:236-239` |
| `500 {"error":{"code":"response-too-large"}}` | corpo >4 MiB (JSON ou asset) | `server.py:120-124,185-186` |
| `403 bad-host` / `403 bad-origin` / `413 payload-too-large` / `400 bad-json` | inalterados do ciclo 6 | `server.py` |
| Headers em **toda** resposta | `CSP`, `X-Frame-Options: DENY`, `nosniff`, `no-store`, `no-referrer` (+`Access-Control-Allow-Origin` quando Origin válida) | `server.py:82-95` |

## 5. O que **não** mudou no wire

- `POST /context`, `POST /impact/analyze`, `GET /search`,
  `GET /symbols/{id}`, `GET /evidence/{id}`, `GET /repositories`,
  `GET /repositories/{id}/snapshots`, `GET /index/status`,
  `GET /health`, `GET /capabilities` — envelopes idênticos ao ciclo 6.
- Nenhuma rota de escrita existe; conexão `mode=ro` + `query_only` segue
  por request (`handlers.py:422-458`, `server.py:263-266`).
- `docs/explorer-api.md` ainda **não documenta** `/files/{id}/occurrences`
  nem os campos novos de aresta — este arquivo é a referência atual;
  a sincronia do doc de API fica como follow-up declarado em
  `docs/cycle-6.1-limitations.md`.
