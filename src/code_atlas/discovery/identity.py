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
    """Chave de identidade estável do repositório.

    lf-0058: raiz **abaixo** do toplevel git recebe sufixo
    ``@<caminho relativo>`` — dois projetos num mesmo monorepo não
    colidem mais (antes compartilhavam o remote → mesmo repo).
    Raiz == toplevel mantém o formato legado (retrocompatível);
    realocar o diretório preserva identidade pois o relpath é estável.
    """
    base = ""
    if git.remote_url:
        base = f"git-remote:{git.remote_url}"
    elif git.root_commit:
        base = f"git-root:{git.root_commit}"
    else:
        return f"path:{Path(root).resolve()}"
    if git.toplevel:
        try:
            rel = Path(root).resolve().relative_to(
                Path(git.toplevel).resolve())
        except ValueError:
            rel = None
        if rel is not None and str(rel) not in ("", "."):
            return f"{base}@{rel.as_posix()}"
    return base


def identity_key_for_root(root: Path) -> str:
    """Identidade sem scan completo — usado por ``init`` (probe apenas)."""
    return identity_key(probe_git(Path(root)), Path(root))
