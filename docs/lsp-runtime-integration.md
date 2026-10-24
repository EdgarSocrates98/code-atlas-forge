# LSP Runtime Integration (lf-0029)

Adapters LSP são **opt-in** (`[resolvers.lsp.servers.<nome>]`,
`enabled = true` por default desligado). Esta página registra o que
foi provado com server real vs fixture de protocolo.

## Cobertura do lifecycle

| item | estado | prova |
|---|---|---|
| initialize → capabilities → initialized | implementado | fixture in-process (`test_handshake_negotiates_capabilities`) |
| definition / references | implementado | fixtures + adapter (`test_lsp_*`) |
| documentSymbol | implementado, capability-gated | `test_document_symbol_*` |
| publishDiagnostics | capturado por uri, anexado como info | `test_publish_diagnostics_captured` |
| $/cancelRequest | enviado após timeout | `test_hanging_server_times_out_and_cancels` |
| timeout | obrigatório em todo request | `request_timeout_s` por spec |
| shutdown → exit → cleanup | sequência ordenada | `test_shutdown_sequence` |
| crash mid-request | `available=False`, erro tipado | `test_crash_mid_request_marks_unavailable` |
| recovery pós-falha | **1 restart + 1 retry** no adapter | `test_recovery_restart_after_crash`, `test_restart_failure_typed_diagnostic` |

## Segurança re-verificada

- Spawn: `shutil.which` + argv sem shell; `stderr=DEVNULL`; env
  minimizado (whitelist em `spawn._leak_env`); máx. 4 servidores
  (`MAX_LSP_SERVERS`); `command` vem só da config — nada é descoberto
  ou executado a partir do repositório indexado.
- Transporte: `Content-Length` framing, mensagem ≤ 16 MiB; POSIX usa
  `select()`, Windows `os.read()` bloqueante (fix Cycle 3 — `close()`
  não fecha fd com read pendente no Windows, leak documentado e
  limitado). Nenhum wait é indefinido.
- Lifecycle fecha com `close()`/`shutdown()` no fim do enrichment —
  nenhum processo sobrevive ao `index()`.

## O que NÃO foi provado

- **E2E com server real (pyright/ts-server): NOT RUN** — o ambiente
  não tem nenhum language server py/ts instalado (verificado no
  grill). Se o operador instalar um server autorizado e habilitar
  `enabled = true`, o caminho inteiro (spawn → handshake → requests
  → shutdown) já está exercido via fixtures idênticas em protocolo.
- `workspace/symbol`: capability negociada, sem uso no adapter ainda.
- Notifications além de `publishDiagnostics` são descartadas por
  design (server→client requests sem resposta — aceitável pois os
  servers alvo não exigem resposta para os métodos usados).

## Política de falha

- Server indisponível/sem capability → diagnóstico tipado, aresta
  preservada (fallback sintático intacto).
- Falha de request (timeout/transport/crash) → 1 restart + 1 retry;
  falhando, `server-error`/`request-timeout` — nunca crash do index.
- `enabled: false` ou ausente → zero processos, zero requests.
