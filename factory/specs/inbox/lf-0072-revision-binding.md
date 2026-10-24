---
id: lf-0072
title: RevisionBindingResolver — binding explícito revisão↔snapshot
status: inbox
verification: python -m pytest tests/impact -x -q && ruff check src tests && mypy src
---

# Grill Gate
- Estados `exact|content_verified|partial|mismatch|unavailable` no resultado — recomendado.

# Acceptance
- `RevisionBindingResolver`: resolve commit/branch/merge-base/worktree-digest ↔ snapshot.commit_sha + repository_id + compatibilidade de conteúdo + dirty state.
- Worktree sujo NÃO equivale a HEAD limpo.
- Snapshot ausente → diagnóstico explícito + análise git-only marcada `partial` + comando seguro para indexar (sem checkout, sem substituição de snapshot).
- Branch name nunca é identidade; newest READY nunca representa commit arbitrário.
- Testes: A→B/A→C, branch divergente, detached HEAD, dirty worktree, snapshot de outro branch/root, snapshot sem commit_sha, commit sem snapshot, hash de conteúdo incompatível.
