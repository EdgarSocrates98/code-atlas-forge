---
id: lf-0075
title: Contract analysis + test-impact — separação risco/incerteza
status: inbox
verification: python -m pytest tests/impact -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Nunca alegar equivalência a type-checker; risco ≠ evidência — recomendado.

# Acceptance
- Assinaturas py/ts/tsx: required/optional/defaults/keyword-only/async/visibility/exports/herança/interfaces/genéricos/overloads/retorno onde representado.
- `potential incompatibility` separado de `demonstrated incompatible consumers`.
- Test selector: imports/calls/references/TESTS edges/convenções/fixtures/mappings configurados; direct/transitive/heuristic/unknown; proximidade de arquivo nunca = cobertura; sem execução de testes; precision/recall medidos em fixtures.
- Risk: fórmula/pesos/versão/limitações documentados; incrementos com razão; não-probabilidade; low-risk+high-uncertainty não é tranquilizador.
