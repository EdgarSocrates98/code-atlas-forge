---
id: lf-0079
title: multi-origin-paths
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src
---

# Grill Gate
Decisões tomadas no prompt closerue; implementar conforme phases.

# Acceptance
visited por (occ,origem) preserva caminhos alternativos; dedup canônico; limites explícitos
