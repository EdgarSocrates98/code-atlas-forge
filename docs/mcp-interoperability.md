# MCP Interoperability (Phase 7)

Cliente real homologado: **SDK oficial `mcp` 2.3.0** (dev-extra).

`tests/mcp/test_e2e_sdk.py` — subprocesso `python -m code_atlas mcp`
via `stdio_client`+`ClientSession`:

- initialize (server_info `code-atlas-forge`), tools/list = 8 tools,
  tools/call (status/search/symbol/context/evidence),
  resources/list+read, prompts/list+get,
  erros tipados JSON-RPC (tool desconhecida, repo não indexado),
  shutdown limpo.

Bugs reais achados pelo E2E e corrigidos: crash por colunas
inexistentes em `atlas_evidence`, exceção de tool derrubando o
processo, `CODE_ATLAS_ROOT` sombreado por `or "."`.

Hosts Codex/Claude não homologados (ausentes no ambiente) — o SDK é o
cliente compatível exigido pelo gate.
