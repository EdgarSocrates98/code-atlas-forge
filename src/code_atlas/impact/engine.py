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

    def _snapshots(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT id FROM snapshots WHERE status = 'READY'"
            " ORDER BY id").fetchall()
        return [int(r["id"]) for r in rows]

    def analyze(self, req: ImpactRequest) -> ImpactResult:
        diags: list[ImpactDiagnostic] = []
        root = Path(req.repository_root)

        # 1) ChangeSet via git (se revisões informadas).
        change_set = None
        if req.base_revision:
            change_set, d = diff_reader.read_diff(
                root, req.base_revision, req.target_revision or None)
            diags.extend(d)

        # 2) Snapshots: alvo = READY mais novo; base = o snapshot
        # imediatamente anterior (STALE vale — guarda o estado antigo).
        base_sid = req.base_snapshot_id
        target_sid = req.target_snapshot_id
        if target_sid is None:
            ready = self._snapshots()
            target_sid = ready[-1] if ready else None
        if base_sid is None and target_sid is not None:
            row = self._conn.execute(
                "SELECT id FROM snapshots WHERE id < ?"
                " ORDER BY id DESC LIMIT 1",
                (target_sid,)).fetchone()
            base_sid = int(row["id"]) if row else target_sid
        if base_sid is None or target_sid is None:
            diags.append(ImpactDiagnostic(
                "snapshot-unavailable",
                "nenhum snapshot READY — indexe antes de analisar"))
            return self._empty(req, diags)

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
