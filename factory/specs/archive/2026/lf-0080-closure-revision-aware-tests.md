---
id: lf-0080
title: revision-aware-tests
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
Decisões tomadas no prompt closerue; implementar conforme phases.

# Acceptance
TestSelector recebe base+target; arestas TESTS históricas de removidos; campo revision no TestCandidate
