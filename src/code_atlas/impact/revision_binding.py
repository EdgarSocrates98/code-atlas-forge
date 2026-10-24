"""RevisionBindingResolver — binding explícito revisão↔snapshot (lf-0072).

Nunca equaciona nome de branch com identidade, nunca usa o snapshot
READY mais novo para representar um commit arbitrário, e nunca trata
worktree sujo como equivalente a HEAD limpo.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from code_atlas.impact.contracts import ImpactDiagnostic
from code_atlas.impact.diff_reader import dirty_worktree, resolve_revision


class BindingState(StrEnum):
    EXACT = "exact"                       # sha do snapshot == sha resolvido
    CONTENT_VERIFIED = "content_verified" # snapshot sem sha, repo+root ok
    PARTIAL = "partial"                   # snapshot existe p/ outro ponto
    MISMATCH = "mismatch"                 # sha do snapshot != sha pedido
    UNAVAILABLE = "unavailable"           # revisão não resolve / sem git


@dataclass(frozen=True)
class RevisionBinding:
    revision_expr: str
    resolved_sha: str | None
    snapshot_id: int | None
    snapshot_sha: str | None
    state: BindingState
    detail: str


def bind(
    conn: sqlite3.Connection, root: Path, repo_id: int,
    revision_expr: str, snapshot_id: int | None,
    *, allow_dirty_check: bool = True,
) -> tuple[RevisionBinding, ImpactDiagnostic | None]:
    """Liga `revision_expr` ao `snapshot_id` escolhido.

    Retorna (binding, diagnóstico opcional). Nunca substitui snapshot.
    """
    conn.row_factory = sqlite3.Row
    sha = resolve_revision(root, revision_expr)
    if sha is None:
        return RevisionBinding(
            revision_expr, None, snapshot_id, None,
            BindingState.UNAVAILABLE,
            f"revisão não resolvida: {revision_expr}"), ImpactDiagnostic(
            "revision-unresolved",
            f"git rev-parse falhou para '{revision_expr}'")

    snap_sha: str | None = None
    if snapshot_id is not None:
        row = conn.execute(
            "SELECT commit_sha FROM snapshots WHERE id = ?",
            (snapshot_id,)).fetchone()
        snap_sha = str(row["commit_sha"]) if row and row["commit_sha"] \
            else None
        if row is not None and snap_sha is None:
            return RevisionBinding(
                revision_expr, sha, snapshot_id, None,
                BindingState.CONTENT_VERIFIED,
                "snapshot sem commit_sha — binding por repo/root"), None

    if snap_sha is not None and snap_sha != sha:
        return RevisionBinding(
            revision_expr, sha, snapshot_id, snap_sha,
            BindingState.MISMATCH,
            f"snapshot {snapshot_id} é de {snap_sha[:12]}, pedido {sha[:12]}"
        ), ImpactDiagnostic(
            "revision-binding-mismatch",
            f"snapshot {snapshot_id} corresponde a {snap_sha[:12]}, "
            f"não a '{revision_expr}' ({sha[:12]}) — análise marcada "
            "parcial")

    if snapshot_id is not None:
        if allow_dirty_check and dirty_worktree(root):
            return RevisionBinding(
                revision_expr, sha, snapshot_id, snap_sha,
                BindingState.PARTIAL,
                "worktree sujo — snapshot reflete HEAD limpo"), \
                ImpactDiagnostic(
                    "dirty-worktree",
                    "worktree tem mudanças não commitadas — snapshot "
                    "reflete HEAD, não o estado atual")
        return RevisionBinding(
            revision_expr, sha, snapshot_id, snap_sha,
            BindingState.EXACT, "sha coincide"), None

    # Revisão pedida sem snapshot correspondente: git-only parcial.
    row = conn.execute(
        "SELECT id FROM snapshots WHERE repository_id = ?"
        " AND commit_sha = ?", (repo_id, sha)).fetchone()
    if row is not None:
        return RevisionBinding(
            revision_expr, sha, int(row["id"]), sha,
            BindingState.EXACT, "sha coincide com snapshot do repo"), None
    return RevisionBinding(
        revision_expr, sha, None, None,
        BindingState.PARTIAL,
        "commit existe sem snapshot indexado"), ImpactDiagnostic(
        "snapshot-for-revision-missing",
        f"nenhum snapshot indexado para {sha[:12]} — análise parcial; "
        f"indexe com: code-atlas index --root {root} "
        f"(rev {sha[:12]})")
