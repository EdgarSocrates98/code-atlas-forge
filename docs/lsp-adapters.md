# Adaptadores LSP (opt-in)

Bridge JSON-RPC para language servers **do usuário** — o Atlas nunca
instala, baixa ou escolhe servers. Configuração explícita em
`config.toml [resolvers.lsp.servers.<name>]`.

## Guardrails

- **Subprocess confinado**: `resolvers/lsp/spawn.py` é o único site de
  `import subprocess` fora `gitbin.py` (teste de auditoria).
- argv sem shell, binário via `shutil.which`, comando só de config —
  **nunca derivado do conteúdo do repo indexado**.
- `MAX_LSP_SERVERS = 4` processos simultâneos.
- Env leak-minimizado (`_leak_env`): só `PATH/HOME/LANG/LC_/
  SYSTEMROOT/WINDIR/TEMP/TMP` — segredos (`*_TOKEN`, `*_KEY`,
  `*_SECRET`…) nunca vazam ao server (testado).
- Timeouts finitos: `INIT_TIMEOUT_S` no handshake,
  `request_timeout_s` por request — nenhum wait indefinido.
- `enabled: false` é o default de todo `LspServerSpec`.

## Lifecycle

`spawn_server → handshake (initialize/initialized) → requests →
shutdown`. Server ausente/crash/hang → `SpawnError`/`LspError`/
`TransportError` tipados — nunca hang, nunca fallback silencioso.
Leitura do stdout usa `select`+`os.read` (sem deadlock no shutdown).

## Resolução

`LspResolver` traduz `textDocument/definition` e
`textDocument/references` para `ResolvedReference` com proveniência
`tier=LSP`. Resultados entram no grafo rotulados — nunca misturados
com evidência sintática.
