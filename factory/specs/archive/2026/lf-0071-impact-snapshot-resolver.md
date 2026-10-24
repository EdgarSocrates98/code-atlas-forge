---
id: lf-0071
title: ImpactSnapshotResolver — service central de resolução repo-aware
status: inbox
verification: python -m pytest tests/impact -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Serviço central `impact/snapshot_resolver.py` (não lógica inline no engine) — recomendado: sim, já parcialmente entregue via `_resolve_snapshots` no commit bb82d99; esta spec extrai para serviço nomeado com contrato `ResolvedImpactSnapshots`.

# Acceptance
- `ImpactSnapshotResolver` com `ResolvedImpactSnapshots{repository_id, base_snapshot_id, target_snapshot_id, base_revision, target_revision, mode}`.
- Invariantes: base+target do mesmo repo; seleção implícita nunca cruza repos; explícitos validados; repo inexistente não herda dados; STALE permitido como base histórica; FAILED nunca confiável; READY como alvo atual.
- Fixture adversarial: 1 SQLite com repos A,B,C intercalando READY/STALE/FAILED → análise de A prova zero vazamento B/C (parcialmente coberto por test_cross_repo_isolation — ampliar para STALE/FAILED).
- Sub-roots distintos do mesmo git repo identificados corretamente (@relpath).
