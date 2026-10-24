---
id: lf-0081
title: consistency-homologation
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
Decisões tomadas no prompt closerue; implementar conforme phases.

# Acceptance
invariantes contrato↔risco↔evidência↔relation_ids por snapshot; audit serializadores
