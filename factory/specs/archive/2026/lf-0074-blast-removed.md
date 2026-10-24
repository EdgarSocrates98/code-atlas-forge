---
id: lf-0074
title: Blast radius — símbolos removidos + proveniência de ImpactPath
status: inbox
verification: python -m pytest tests/impact -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Travessia parte da ocorrência histórica (base) quando a atual não existe — recomendado.

# Acceptance
- Símbolo REMOVIDO gera blast a partir da ocorrência do snapshot base.
- Todo ImpactPath: origem, candidato, hops, relation_ids, intermediários, resolution por aresta, evidence_ids, fully_verified, reason.
- Cadeia com aresta incerta nunca promovida a verified.
- Limites: depth/nodes/edges/paths/timeout/cancelamento/ciclos/visited/ordem determinística.
- Múltiplos caminhos preservados quando configurado; dedup de paths; limites explícitos; comportamento medido.
