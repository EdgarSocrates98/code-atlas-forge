---
id: lf-0078
title: historical-reconciliation
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
Decisões tomadas no prompt closerue; implementar conforme phases.

# Acceptance
classificar consumidores históricos (still_present/removed_with_dependency/dependency_removed/unresolved_in_target) + campo aditivo consumer_state
