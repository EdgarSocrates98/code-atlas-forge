# Compatibilidade de hosts MCP (lf-0043)

Atlas serve MCP por stdio (JSON-RPC 2.0 ndjson, ADR-0023). O manifest
é **emitido** — nunca escrito em config global do host.

## Manifests

`code-atlas mcp manifest --host <host>`:

| host | formato | notas |
|---|---|---|
| claude-code | `{"mcpServers": {"code-atlas": {"command": "code-atlas", "args": ["mcp"]}}}` | colar em `.mcp.json` ou settings do projeto |
| codex-cli | `{"mcp_servers": ...}` | mesma tool, chave snake_case |
| generic | `mcpServers` | cliente MCP stdio genérico |

## Hosts testados

- **cliente fake** (`tests/mcp/`): handshake, tools/list+call,
  resources/list+read, prompts/list+get, erros — coberto.
- **Claude Code / Codex CLI / Cursor**: NOT RUN neste ambiente — o
  protocolo é MCP stdio padrão; `mcp doctor` + `mcp manifest` existem
  para verificação manual.

## `code-atlas mcp doctor`

Reporta: executável, versão Python, db_path + existência, schema
version, presença de sqlite_vec. `--json` para consumo por host.

## Independência

Atlas não importa Forge CLI, Loop Factory ou qualquer Forja — testado
em `tests/mcp/test_independence.py`. Zero egress, read-only.
