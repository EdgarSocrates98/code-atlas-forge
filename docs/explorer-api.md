# Explorer API — `/api/v1` (lf-0086, atualizado no lf-0126)

HTTP local para o Code Atlas Explorer. Loopback-only, read-only,
erros tipados `{"error":{"code","message"}}`. ADR-0009. O contrato
campo-a-campo das arestas está em `docs/graph-evidence-contracts.md` e
`docs/cycle-6.1-evidence.md`; o inventário completo do ciclo 7 está em
`docs/cycle-7-evidence.md`.

## Endpoints implementados

| método | rota | descrição |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/capabilities` | capabilities do SearchEngine + `readonly` |
| GET | `/repositories` | repos indexados |
| GET | `/repositories/{id}/snapshots` | snapshots do repo (≤100) |
| GET | `/graph/overview` | nós agregados por arquivo (bounded, `file_id`, `total_files`, `truncated`; limit ≤2000) |
| GET | `/graph/neighbors?occurrence_id=&direction=in|out|both&limit=` | arestas + nós vizinhos — DTO com `relation_id`, `to_unresolved`, `confidence_class`, `resolver`, `evidence_ids` |
| GET | `/files/{id}/occurrences?limit=&offset=` | drill-down real por arquivo (6.1) — `id:"occ:N"`, paginação `ORDER BY line,col,id` |
| GET | `/symbols/{occurrence_id}` | detalhe + `relations_summary` |
| GET | `/search?q=&strategy=&language=&kind=&path=&limit=&cursor=` | SearchEngine real (default híbrido) |
| GET | `/search?…&domain=&workspace=&pin=&repository_id=` | **(7)** faceta de domínio (`domain`) e busca federada (`workspace`/`federation`, `pin=repo:snap`, `repository_id` narrow) |
| GET | `/domains` | **(7)** inventário domain×kind com contagens entities/occurrences |
| GET | `/domains/entities?domain=&kind=&q=&limit=&offset=` | **(7)** entidades de domínio do snapshot, paginado |
| GET | `/domains/entities/{id}/neighbors?direction=&limit=` | **(7)** ego da entidade em `domain_relations` |
| GET | `/domains/relations?type=&limit=&offset=` | **(7)** arestas de domínio com endpoints hidratados + prova |
| GET | `/domains/lineage?table=&direction=&depth=&limit=` | **(7)** linhagem BFS de dados (upstream/downstream, depth ≤8) |
| GET | `/federation/workspaces` | **(7)** workspaces federados |
| GET | `/federation/workspaces/{id}` | **(7)** workspace + membros e pins |
| GET | `/federation/workspaces/{id}/view?pin=` | **(7)** view resolvida `{repo→snapshot}` + `view_id` |
| GET | `/federation/workspaces/{id}/entities/{key}/occurrences?pin=` | **(7)** ocorrências por membro, snapshot-scoped por membro |
| GET | `/federation/workspaces/{id}/graph?depth=&limit=` | **(7)** BFS intra+shared (arestas `shared_entity` computadas, depth ≤8, limit ≤5000) |
| GET | `/federation/workspaces/{id}/search?…` | **(7)** busca federada no workspace |
| POST | `/context` | ContextRequest → ContextPack |
| POST | `/impact/analyze` | ImpactRequest → ImpactResult (blast misto símbolo+domínio) |
| GET | `/evidence/{id}` | evidência + relações que a referenciam |
| GET | `/index/status` | contagens por snapshot READY |
| GET | `/{path}` (não-api) | assets do `explorer/dist` — SPA fallback |

Escopo comum: `repository_id` + `snapshot_id` resolvem repo/snapshot
READY (404 `repo-not-found`/`snapshot-not-found`, 409
`snapshot-not-ready`). Filtros csv `domain`/`kind`/`type` são
validados por regex com teto de itens (`bad-param` em formato inválido).

## Segurança implementada

- Bind apenas `127.0.0.1`/`localhost`/`::1` — `AtlasApiServer` rejeita
  host público com `unsafe-bind`.
- Allowlist de `Host` (anti DNS-rebinding); `Origin` estrangeiro → 403.
- Corpo ≤ 64 KiB (413), resposta ≤ 4 MiB, JSON inválido → `bad-json`.
- `PRAGMA query_only` + `mode=ro` — nenhum handler escreve; canal
  vetorial degrada com diagnóstico `vector-unavailable-readonly`.
- Rate limit 300 req/min por IP em `/api/v1` → 429 `rate-limited` +
  `Retry-After`; timeout 30 s; CSP + `X-Frame-Options: DENY` (6.1).
- Estáticos: allowlist de extensões, resolução confinada à `static_root`
  (sem traversal), SPA fallback para `index.html`.
- Erros internos nunca vazam traceback — `internal` + mensagem ≤200.
- Federação: mutação inexistente no HTTP — create/add/pin são CLI-only;
  POST/PUT/DELETE sob `/federation/*` → 404.

## NOT RUN (honesto)

- WebSocket/streaming (fora de escopo).
- `GET /impact/{id}` — exige armazenamento de resultados; só `POST`
  síncrono por ora (inalterado no 7 — L28 de `cycle-7-limitations.md`).
- FTS full-text de entidades de domínio — `q=` em `/domains/entities` é
  `LIKE` parametrizado (L11 de `cycle-7-limitations.md`).
