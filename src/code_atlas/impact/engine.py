"""Orquestrador do Impact Engine (Phases 1-7 reunidas).

Pipeline: ChangeSet (git) → symbol_diff (snapshots) → contract
analysis → blast radius → test selection → risk. Sem snapshot READY
comparável → diagnóstico explícito, nunca fallback silencioso.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from code_atlas.impact import (
    blast_radius,
    contract_analyzer,
    diff_reader,
    risk_classifier,
    symbol_diff,
    test_selector,
)
from code_atlas.impact.contracts import (
    ANALYZER_VERSION,
    IMPACT_SCHEMA_VERSION,
    ImpactDiagnostic,
    ImpactRequest,
    ImpactResult,
)


class ImpactEngine:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.row_factory = sqlite3.Row

    def _snapshots(self, repo_id: int) -> list[int]:
        """Snapshots READY **deste repositório** (lf-0071/P1: antes era
        global — outro repo podia ser escolhido como base/alvo)."""
        rows = self._conn.execute(
            "SELECT id FROM snapshots"
            " WHERE status = 'READY' AND repository_id = ?"
            " ORDER BY id", (repo_id,)).fetchall()
        return [int(r["id"]) for r in rows]

    def _repo_id(self, root: Path) -> int | None:
        from code_atlas.search.structural import StructuralSearch
        return StructuralSearch(self._conn).repo_for_root(str(root))

    def _resolve_snapshots(
        self, req: ImpactRequest, root: Path,
        diags: list[ImpactDiagnostic],
    ) -> tuple[int, int] | None:
        repo_id = self._repo_id(root)
        if repo_id is None:
            diags.append(ImpactDiagnostic(
                "repo-not-indexed",
                f"repositório não indexado: {root}"))
            return None
        base_sid = req.base_snapshot_id
        target_sid = req.target_snapshot_id
        if target_sid is None:
            ready = self._snapshots(repo_id)
            target_sid = ready[-1] if ready else None
        if base_sid is None and target_sid is not None:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE id < ?"
                " AND repository_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (target_sid, repo_id)).fetchone()
            base_sid = int(row["id"]) if row else target_sid
        for sid in (base_sid, target_sid):
            if sid is None:
                continue
            own = self._conn.execute(
                "SELECT repository_id FROM snapshots WHERE id = ?",
                (sid,)).fetchone()
            if own is None:
                diags.append(ImpactDiagnostic(
                    "snapshot-missing", f"snapshot {sid} ausente"))
            elif int(own["repository_id"]) != repo_id:
                diags.append(ImpactDiagnostic(
                    "cross-repo-snapshot",
                    f"snapshot {sid} pertence a outro repositório"))
                return None
        if base_sid is None or target_sid is None:
            diags.append(ImpactDiagnostic(
                "snapshot-unavailable",
                "nenhum snapshot READY — indexe antes de analisar"))
            return None
        return base_sid, target_sid

    def analyze(self, req: ImpactRequest) -> ImpactResult:
        diags: list[ImpactDiagnostic] = []
        root = Path(req.repository_root)

        # 1) ChangeSet via git (se revisões informadas).
        change_set = None
        if req.base_revision:
            change_set, d = diff_reader.read_diff(
                root, req.base_revision, req.target_revision or None)
            diags.extend(d)

        # 2) Snapshots — SEMPRE escopados ao repositório do root.
        pair = self._resolve_snapshots(req, root, diags)
        if pair is None:
            return self._empty(req, diags)
        base_sid, target_sid = pair

        # 3) Symbol diff.
        sym_changes, unknown, trunc = symbol_diff.diff_snapshots(
            self._conn, base_sid, target_sid,
            max_changes=req.limits.max_changes)

        # 4) Seleção explícita de símbolo restringe a análise.
        if req.symbol:
            sym_changes = tuple(
                s for s in sym_changes
                if req.symbol in s.qualified_name)

        # 5) Contracts.
        contract_changes = contract_analyzer.analyze(sym_changes)

        # 6) Blast radius no snapshot alvo.
        origins: dict[str, int] = {}
        for s in sym_changes:
            occ = s.new_occurrence_id or s.old_occurrence_id
            if occ is not None:
                origins[s.symbol_key] = occ
        impacts, b_trunc = blast_radius.blast(
            self._conn, target_sid, origins, req.limits)
        if req.known_only or not req.include_possible:
            impacts = tuple(
                i for i in impacts if i.strength.value == "verified")
        trunc = trunc or b_trunc

        # 7) Testes + risco.
        tests = test_selector.select(
            self._conn, target_sid, sym_changes, impacts)
        risk = risk_classifier.assess(
            sym_changes, contract_changes, impacts, unknown)

        return ImpactResult(
            schema_version=IMPACT_SCHEMA_VERSION,
            analyzer_version=ANALYZER_VERSION,
            repository=req.repository_root,
            base_revision=req.base_revision or f"snapshot:{base_sid}",
            target_revision=req.target_revision
            or f"snapshot:{target_sid}",
            base_snapshot_id=base_sid, target_snapshot_id=target_sid,
            change_set=change_set,
            symbol_changes=sym_changes,
            contract_changes=contract_changes,
            impacts=impacts,
            test_candidates=tests,
            risk=risk,
            unknown_coverage=unknown,
            truncated=trunc,
            diagnostics=tuple(diags))

    def _empty(
        self, req: ImpactRequest, diags: list[ImpactDiagnostic],
    ) -> ImpactResult:
        return ImpactResult(
            schema_version=IMPACT_SCHEMA_VERSION,
            analyzer_version=ANALYZER_VERSION,
            repository=req.repository_root,
            base_revision=req.base_revision,
            target_revision=req.target_revision,
            base_snapshot_id=None, target_snapshot_id=None,
            change_set=None, symbol_changes=(),
            contract_changes=(), impacts=(), test_candidates=(),
            risk=risk_classifier.assess((), (), (), ()),
            unknown_coverage=("no-snapshot",),
            truncated=False, diagnostics=tuple(diags))
