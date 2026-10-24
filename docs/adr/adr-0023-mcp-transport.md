# ADR-0023 — Transporte MCP: JSON-RPC 2.0 próprio sobre stdio (zero-dep)

**Status:** aceito (lf-0042) · **Decisores:** Cycle 4 Grill Gate

## Contexto

Servidor MCP para hosts (Claude Code, Cursor, etc.) sem SDK oficial —
dep pesada que quebraria a instalação mínima. O padrão do decoder SCIP
(ADR-017) já provou que stdlib cobre protocolos quando o escopo é
controlado.

## Decisão

- **Transporte**: newline-delimited JSON-RPC 2.0 sobre stdio (MCP
  stdio). Sem HTTP/WebSocket neste ciclo.
- **Escopo**: 8 tools reais (delegam SearchEngine/ContextEngine/
  ContextExpander — nunca stubs), 6 resources `atlas://`, 5 prompts
  orientadores de evidência.
- **Read-only**: nenhuma tool destrutiva; schemas `additionalProperties:
  false`, `limit`/`budget` com teto; inputs validados com `-32602`.
- **Sem handshake externo**: `initialize` ecoa o `protocolVersion` do
  cliente (version negotiation honesta).

## Consequências

- `code-atlas mcp` funciona com qualquer cliente MCP stdio sem deps.
- Fake client cobre handshake/tools/resources/prompts/erros — cliente
  real declarado NOT RUN se indisponível no ambiente.
