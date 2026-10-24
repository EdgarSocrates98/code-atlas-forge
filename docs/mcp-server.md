# MCP Server (ADR-0023)

`code-atlas mcp` — JSON-RPC 2.0 ndjson sobre stdio, stdlib-only,
read-only. 8 tools (`atlas_search|symbol|references|dependencies|
context|evidence|status|expand`), resources `atlas://`, 5 prompts
(understand-component, locate-implementation, explore-dependencies,
prepare-safe-change, explain-code-path). `mcp doctor` diagnostica;
`mcp manifest --host` emite config (nunca escreve — ver
mcp-host-compatibility.md).
