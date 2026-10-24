"""Identidade lógica do repositório (lf-0021).

``repository_id`` é derivado de identidade **lógica**, nunca só do path:

1. ``git-remote:<remote.origin.url>`` — repositório com remote;
2. ``git-root:<sha do commit raiz>`` — repo git sem remote;
3. ``path:<root resolvido>`` — último recurso (sem git).

Realocar o diretório físico mantém a identidade (``root_handle`` passa a
ser campo de diagnóstico atualizado a cada index). Dois checkouts do
mesmo remote **compartilham** identidade — snapshots de ambos vivem sob
o mesmo repo e se distinguem por ``worktree_digest``/revisão.
"""

from __future__ import annotations

from pathlib import Path

from code_atlas.discovery.models import GitInfo
from code_atlas.discovery.scanner import probe_git


def identity_key(git: GitInfo, root: Path) -> str:
    """Chave de identidade estável do repositório."""
    if git.remote_url:
        return f"git-remote:{git.remote_url}"
    if git.root_commit:
        return f"git-root:{git.root_commit}"
    return f"path:{Path(root).resolve()}"


def identity_key_for_root(root: Path) -> str:
    """Identidade sem scan completo — usado por ``init`` (probe apenas)."""
    return identity_key(probe_git(Path(root)), Path(root))
