"""Leitura segura de diffs Git (Phase 2).

Regras: somente plumbing read-only (`diff`, `rev-parse`, `status`),
argv sem shell via ``gitbin``, ``-z`` para nomes com bytes/space,
nenhuma escrita no workspace/index/checkout.
"""

from __future__ import annotations

from pathlib import Path

from code_atlas import gitbin
from code_atlas.impact.contracts import (
    ChangeKind,
    ChangeSet,
    FileChange,
    ImpactDiagnostic,
)

_KIND_BY_STATUS = {
    "A": ChangeKind.ADDED, "M": ChangeKind.MODIFIED,
    "D": ChangeKind.DELETED, "R": ChangeKind.RENAMED,
    "C": ChangeKind.COPIED, "T": ChangeKind.TYPE_CHANGED,
    "B": ChangeKind.BINARY, "X": ChangeKind.UNAVAILABLE,
}


def resolve_revision(root: Path, expr: str) -> str | None:
    """Resolve HEAD~N/branch/sha → sha completo; None se inválido."""
    res = gitbin.run_git(["rev-parse", "--verify", "--quiet",
                          f"{expr}^{{commit}}"], cwd=root)
    if res.returncode != 0:
        return None
    return res.stdout.decode("utf-8", "replace").strip() or None


def dirty_worktree(root: Path) -> bool:
    res = gitbin.run_git(["status", "--porcelain", "-z"], cwd=root)
    return res.returncode == 0 and bool(res.stdout.strip(b"\0"))


def _parse_name_status_z(raw: bytes) -> list[FileChange]:
    """Parse `git diff --name-status -M -z` → FileChange[]."""
    fields = [f for f in raw.split(b"\0") if f]
    out: list[FileChange] = []
    i = 0
    while i < len(fields):
        status = fields[i].decode("ascii", "replace")
        i += 1
        letter = status[0]
        sim = int(status[1:]) if letter in "RC" and status[1:] else None
        kind = _KIND_BY_STATUS.get(letter, ChangeKind.UNAVAILABLE)
        old_p: str | None
        new_p: str | None
        if letter in "RC":
            old_p = fields[i].decode("utf-8", "replace")
            new_p = fields[i + 1].decode("utf-8", "replace")
            i += 2
        else:
            old_p = fields[i].decode("utf-8", "replace")
            new_p = old_p if letter != "D" else None
            if letter == "A":
                old_p, new_p = None, fields[i].decode("utf-8", "replace")
            i += 1
        out.append(FileChange(kind=kind, old_path=old_p,
                              new_path=new_p, similarity=sim,
                              binary=letter == "B"))
    return out


def read_diff(
    root: Path, base: str, target: str | None, *,
    staged: bool = False,
) -> tuple[ChangeSet | None, tuple[ImpactDiagnostic, ...]]:
    """ChangeSet para base e target; target=None → worktree (dirty)."""
    diags: list[ImpactDiagnostic] = []
    base_sha = resolve_revision(root, base)
    if base_sha is None:
        return None, (ImpactDiagnostic(
            "invalid-revision", f"revisão base inválida: {base!r}"),)
    target_sha = (
        resolve_revision(root, target) if target else None)
    if target and target_sha is None:
        return None, (ImpactDiagnostic(
            "invalid-revision", f"revisão alvo inválida: {target!r}"),)

    args = ["diff", "--name-status", "-M", "-z"]
    if staged:
        args.append("--cached")
    if target_sha:
        args += [base_sha, target_sha]
    else:
        args.append(base_sha)
    res = gitbin.run_git(args, cwd=root)
    if res.returncode != 0:
        return None, (ImpactDiagnostic(
            "git-error",
            res.stderr.decode("utf-8", "replace")[:200]),)
    files = tuple(_parse_name_status_z(res.stdout))
    return ChangeSet(
        base_revision=base_sha,
        target_revision=target_sha or "worktree",
        files=files,
        source="git",
        dirty_worktree=dirty_worktree(root),
        commit_ids=(base_sha, *( (target_sha,) if target_sha else ())),
    ), tuple(diags)
