---
id: lf-0076
title: Report/packs consistency + CLI/MCP regression + docs + homologação 5.1
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Reports e Context Packs compartilham o mesmo binding/evidência do engine — recomendado.

# Acceptance
- Report: repository, base/target revisions, summary, símbolos, contratos, impacts (direct/transitive/possible), unknown coverage, testes, risk, evidence, limitations, diagnostics; JSON/Markdown/human determinístico e versionado; truncation explícito.
- CLI/MCP: mesmos comportamentos preservados; revisões inválidas, snapshot mismatch, requests gigantes, cancelamento, timeout; sem tools redundantes.
- Golden dataset ampliado (removals, renames, contratos, transitives, circular, monorepo, no-tests) + real-world py/ts.
- Perf/security medidos; docs/cycle-5.1-*.md + evidência; homologação; readiness Cycle 6 declarado só com evidência.
