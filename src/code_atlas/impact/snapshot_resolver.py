"""ImpactSnapshotResolver — resolução repo-aware de snapshots (lf-0071).

Invariantes:
- base e target pertencem SEMPRE ao mesmo repositório lógico;
- seleção implícita nunca cruza repositórios (ordenação global de ids
  nunca infere histórico entre repos);
- snapshots explícitos são validados contra o repositório;
- repo inexistente/não indexado não herda dados de outro;
- STALE vale como base histórica; FAILED nunca é confiável;
- READY é a única fonte "atual" implícita.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from code_atlas.impact.contracts import ImpactDiagnostic, ImpactRequest


@dataclass(frozen=True)
class ResolvedImpactSnapshots:
    """Par base/target validado para uma análise de impacto."""

    repository_id: int
    base_snapshot_id: int
    target_snapshot_id: int
    base_revision: str
    target_revision: str
    mode: str                     # explicit | implicit | mixed
    base_status: str
    target_status: str


class ImpactSnapshotResolver:
    """Única porta de entrada para escolher snapshots de impacto."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.row_factory = sqlite3.Row

    def _repo_id(self, root: Path) -> int | None:
        from code_atlas.search.structural import StructuralSearch
        return StructuralSearch(self._conn).repo_for_root(str(root))

    def _snap(self, sid: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT id, repository_id, status, commit_sha, git_ref"
            " FROM snapshots WHERE id = ?", (sid,)).fetchone()
        return row

    def _revision(self, sid: int) -> str:
        row = self._snap(sid)
        return str(row["commit_sha"] or "") if row else ""

    def _check(
        self, sid: int | None, role: str, repo_id: int,
        diags: list[ImpactDiagnostic],
    ) -> bool:
        """Valida um snapshot explícito/implícito contra o repo."""
        if sid is None:
            return True
        own = self._snap(sid)
        if own is None:
            diags.append(ImpactDiagnostic(
                "snapshot-missing", f"snapshot {role}={sid} ausente"))
            return True
        if int(own["repository_id"]) != repo_id:
            diags.append(ImpactDiagnostic(
                "cross-repo-snapshot",
                f"snapshot {role}={sid} pertence a outro repositório"))
            return False
        if own["status"] == "FAILED":
            diags.append(ImpactDiagnostic(
                "snapshot-failed",
                f"snapshot {role}={sid} com status FAILED — "
                "não é fonte confiável"))
            return False
        return True

    def resolve(
        self, req: ImpactRequest, root: Path,
    ) -> tuple[ResolvedImpactSnapshots | None, list[ImpactDiagnostic]]:
        diags: list[ImpactDiagnostic] = []
        repo_id = self._repo_id(root)
        if repo_id is None:
            return None, [ImpactDiagnostic(
                "repo-not-indexed",
                f"repositório não indexado: {root}")]

        base_sid = req.base_snapshot_id
        target_sid = req.target_snapshot_id
        mode = "explicit" if base_sid and target_sid else (
            "mixed" if base_sid or target_sid else "implicit")

        if target_sid is None:
            rows = self._conn.execute(
                "SELECT id FROM snapshots"
                " WHERE status = 'READY' AND repository_id = ?"
                " ORDER BY id", (repo_id,)).fetchall()
            target_sid = int(rows[-1]["id"]) if rows else None

        if base_sid is None and target_sid is not None:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE id < ?"
                " AND repository_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (target_sid, repo_id)).fetchone()
            base_sid = int(row["id"]) if row else target_sid

        for sid, role in ((base_sid, "base"), (target_sid, "target")):
            if not self._check(sid, role, repo_id, diags):
                return None, diags

        if base_sid is None or target_sid is None:
            diags.append(ImpactDiagnostic(
                "snapshot-unavailable",
                "nenhum snapshot utilizável — indexe antes de analisar"))
            return None, diags
        if diags:
            return None, diags

        base = self._snap(base_sid)
        target = self._snap(target_sid)
        return ResolvedImpactSnapshots(
            repository_id=repo_id,
            base_snapshot_id=base_sid,
            target_snapshot_id=target_sid,
            base_revision=str(base["commit_sha"] or "") if base else "",
            target_revision=str(target["commit_sha"] or "")
            if target else "",
            mode=mode,
            base_status=str(base["status"]) if base else "",
            target_status=str(target["status"]) if target else "",
        ), diags
