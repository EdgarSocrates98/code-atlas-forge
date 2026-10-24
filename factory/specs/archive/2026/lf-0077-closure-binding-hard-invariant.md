---
id: lf-0077
title: binding-hard-invariant
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
Decisões tomadas no prompt closerue; implementar conforme phases.

# Acceptance
ImpactRevisionPlan + MISMATCH bloqueia análise semântica; CONTENT_VERIFIED só com prova real (worktree_digest vs scan)
