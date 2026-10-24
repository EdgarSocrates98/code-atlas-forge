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
            "SELECT commit_sha, worktree_digest FROM snapshots"
            " WHERE id = ?", (snapshot_id,)).fetchone()
        snap_sha = str(row["commit_sha"]) if row and row["commit_sha"] \
            else None
        if row is not None and snap_sha is None:
            # lf-0077/Phase2: CONTENT_VERIFIED só com prova real —
            # digest do snapshot vs digest do worktree ATUAL, e só
            # quando a revisão pedida é o HEAD corrente e limpo.
            return _content_verify(
                root, sha, snapshot_id,
                str(row["worktree_digest"] or ""), revision_expr)

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


def _content_verify(
    root: Path, sha: str, snapshot_id: int, snap_digest: str,
    revision_expr: str,
) -> tuple[RevisionBinding, ImpactDiagnostic | None]:
    """Compara o digest gravado no snapshot com o worktree atual.

    Sinais registrados no ``detail``: algoritmo (sha256 manifest),
    digest esperado vs observado, HEAD resolvido, dirty state.
    """
    from code_atlas.discovery.scanner import scan

    head = resolve_revision(root, "HEAD")
    if head != sha:
        return RevisionBinding(
            revision_expr, sha, snapshot_id, None,
            BindingState.PARTIAL,
            "snapshot sem commit_sha e revisão não é o HEAD atual — "
            "sem material para comprovar equivalência"), ImpactDiagnostic(
            "content-unverifiable",
            f"snapshot {snapshot_id} sem commit_sha; "
            f"'{revision_expr}' != HEAD — binding parcial")
    try:
        observed = scan(root).manifest_sha256()
    except Exception:  # scan falhou → sem prova
        return RevisionBinding(
            revision_expr, sha, snapshot_id, None,
            BindingState.UNAVAILABLE,
            "scan do worktree falhou — equivalência não comprovada"), None
    dirty = dirty_worktree(root)
    detail = (
        f"alg=manifest-sha256 expected={snap_digest[:16]} "
        f"observed={observed[:16]} dirty={dirty}")
    if not dirty and snap_digest and snap_digest == observed:
        return RevisionBinding(
            revision_expr, sha, snapshot_id, None,
            BindingState.CONTENT_VERIFIED, detail), None
    return RevisionBinding(
        revision_expr, sha, snapshot_id, None,
        BindingState.PARTIAL, f"digests divergem ou dirty — {detail}"), \
        ImpactDiagnostic(
            "content-mismatch",
            f"conteúdo do worktree difere do snapshot {snapshot_id} "
            "— binding parcial")


@dataclass(frozen=True)
class ImpactRevisionPlan:
    """Plano de execução — binding como invariante duro (Phase 1)."""

    repository_id: int
    base_snapshot_id: int | None
    target_snapshot_id: int | None
    base_binding: RevisionBinding | None
    target_binding: RevisionBinding | None
    semantic_analysis_allowed: bool
    partial_analysis_allowed: bool
    diagnostics: tuple[ImpactDiagnostic, ...]


_HARD_BLOCK = {BindingState.MISMATCH, BindingState.UNAVAILABLE}


def plan(
    conn: sqlite3.Connection, root: Path, repo_id: int,
    base_revision: str, target_revision: str,
    base_snapshot_id: int | None, target_snapshot_id: int | None,
) -> ImpactRevisionPlan:
    """Liga revisões pedidas aos snapshots escolhidos e decide se a
    análise semântica pode prosseguir (Phase 1 — hard invariant)."""
    diags: list[ImpactDiagnostic] = []
    base_b: RevisionBinding | None = None
    target_b: RevisionBinding | None = None
    blocked = False

    for expr, sid, role in (
            (base_revision, base_snapshot_id, "base"),
            (target_revision, target_snapshot_id, "target")):
        if not expr:
            continue
        b, d = bind(conn, root, repo_id, expr, sid,
                    allow_dirty_check=(role == "target"))
        if d is not None:
            diags.append(d)
        if role == "base":
            base_b = b
        else:
            target_b = b
        if b.state in _HARD_BLOCK:
            blocked = True
            diags.append(ImpactDiagnostic(
                "semantic-blocked",
                f"binding {role}={b.state} — análise semântica não "
                f"será atribuída à revisão '{expr}'"))
        elif b.state is BindingState.PARTIAL:
            diags.append(ImpactDiagnostic(
                "partial-analysis",
                f"binding {role} parcial — resultado marcado parcial"))

    return ImpactRevisionPlan(
        repository_id=repo_id,
        base_snapshot_id=base_snapshot_id,
        target_snapshot_id=target_snapshot_id,
        base_binding=base_b, target_binding=target_b,
        semantic_analysis_allowed=not blocked,
        partial_analysis_allowed=True,
        diagnostics=tuple(diags))

