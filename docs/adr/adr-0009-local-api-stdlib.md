# ADR-0009 — API local do Explorer em stdlib `http.server`

- Status: aceito (Cycle 6, lf-0086)
- Contexto: o Explorer precisa de uma API HTTP local reutilizando os
  engines de domínio. FastAPI/Starlette exigiriam dependência nova —
  proibida pela regra do repo (Python dependency-free) e pelo princípio
  de fricção mínima de instalação.
- Decisão: `ThreadingHTTPServer` + `BaseHTTPRequestHandler` em
  `src/code_atlas/api/`, bind exclusivamente loopback, conexão SQLite
  `mode=ro` + `PRAGMA query_only`, sem telemetria.
- Consequências: zero deps novas; superfície pequena e auditável;
  frontend consome endpoints versionados `/api/v1/*`; MCP stdio não é
  reutilizado como HTTP improvisado; guard de "no network imports" foi
  refinado para tratar `api/` como a fronteira declarada.
- Alternativas rejeitadas: FastAPI (dep nova), MCP-over-HTTP (prompt
  proíbe reuso improvisado), unix socket (Angular não fala unix socket).
