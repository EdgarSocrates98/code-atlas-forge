---
id: lf-0086
title: local-backend-api
status: inbox
verification: python -m pytest tests/ -x -q && ruff check src tests && mypy src && cd explorer && npm run build
---

# Grill Gate
Decisões do prompt cycle-6; skills especializadas por fase.

# Acceptance
Phase 4: HTTP API loopback-only reutilizando engines + segurança + ADR
