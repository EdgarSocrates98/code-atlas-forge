---
id: lf-0073
title: Identidade de símbolo cross-snapshot — rename/ambiguity hardening
status: inbox
verification: python -m pytest tests/impact -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Correspondência incerta permanece `possible`/`ambiguous`, nunca `verified` — recomendado.

# Acceptance
- Distinguir added/removed/renamed/moved/signature-changed/ambiguous/unknown.
- Não casar por nome sozinho com shadowing/namespaces/overloads/nested/classes homônimas/arquivos distintos.
- Evidência: id estável + path + qname + kind + span + assinatura + hash + similaridade estrutural determinística.
- content_hash sozinho não cria identidade falsa; preservar occurrence_ids velho/novo + evidence.
- Adversarial: mesmo conteúdo em locais diferentes; renames ambíguos (A→B e B→C simultâneos).
