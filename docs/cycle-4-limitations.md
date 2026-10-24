# Limitações conhecidas — Cycle 4

- **Graph recall baixo** no eval: calls `x.method()` ficam
  syntactic-unresolved → poucas arestas resolved p/ BFS. Decisão
  honesta (nunca inventar aresta), não bug.
- **lexical/structural sem `evidence`**: hits carregam
  `matched_fields`; `evidence` vazio — hardening futuro (ADR-0028).
- **Vector provider naïve** não é semântico — baseline determinístico;
  sqlite-vec não testado em runtime (ausente no ambiente).
- **Hosts MCP reais NOT RUN** (Claude Code/Codex ausentes) — protocolo
  padrão + `mcp doctor`/`manifest` para verificação manual.
- **Agentic e2e NOT RUN** — sem provider; economia de tokens não
  provada (regra explícita no eval).
- **Tokenizer heurístico** (chars/4) — estimado, nunca exato.
- **LSP real degradado** — pyright instalado no ambiente dev, E2E
  opt-in; demais servidores ausentes (lf-0035).
