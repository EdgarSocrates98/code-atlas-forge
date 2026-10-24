# Explorer API — `/api/v1` (lf-0086)

HTTP local para o Code Atlas Explorer. Loopback-only, read-only,
erros tipados `{"error":{"code","message"}}`. ADR-0009.

## Endpoints implementados

| método | rota | descrição |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/capabilities` | capabilities do SearchEngine + `readonly` |
| GET | `/repositories` | repos indexados |
| GET | `/repositories/{id}/snapshots` | snapshots do repo (≤100) |
| GET | `/graph/overview` | nós agregados por arquivo (bounded, `total_files`, `truncated`) |
| GET | `/graph/neighbors?occurrence_id=&direction=in|out|both&limit=` | arestas + nós vizinhos |
| GET | `/symbols/{occurrence_id}` | detalhe + resumo de relações |
| GET | `/search?q=&strategy=&language=&kind=&path=&limit=&cursor=` | SearchEngine real (default híbrido) |
| POST | `/context` | ContextRequest → ContextPack |
| POST | `/impact/analyze` | ImpactRequest → ImpactResult |
| GET | `/evidence/{id}` | evidência + relações que a referenciam |
| GET | `/index/status` | contagens por snapshot READY |
| GET | `/{path}` (não-api) | assets do `explorer/dist` — SPA fallback |

## Segurança implementada

- Bind apenas `127.0.0.1`/`localhost`/`::1` — `AtlasApiServer` rejeita
  host público com `unsafe-bind`.
- Allowlist de `Host` (anti DNS-rebinding); `Origin` estrangeiro → 403.
- Corpo ≤ 64 KiB (413), resposta ≤ 4 MiB, JSON inválido → `bad-json`.
- `PRAGMA query_only` + `mode=ro` — nenhum handler escreve; canal
  vetorial degrada com diagnóstico `vector-unavailable-readonly`.
- Estáticos: allowlist de extensões, resolução confinada à `static_root`
  (sem traversal), SPA fallback para `index.html`.
- Erros internos nunca vazam traceback — `internal` + mensagem ≤200.

## NOT RUN (honesto)

- Rate limiting por cliente (loopback single-user — adiado).
- WebSocket/streaming (fora de escopo da fase).
- `GET /impact/{id}` — exige armazenamento de resultados; só `POST`
  síncrono por ora.
