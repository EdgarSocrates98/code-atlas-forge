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
    EvidenceStrength,
    ImpactDiagnostic,
    ImpactRequest,
    ImpactResult,
    SymbolChangeKind,
)
from code_atlas.impact.revision_binding import BindingState, bind
from code_atlas.impact.snapshot_resolver import (
    ImpactSnapshotResolver,
    ResolvedImpactSnapshots,
)


class ImpactEngine:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.row_factory = sqlite3.Row

    def _resolve_snapshots(
        self, req: ImpactRequest, root: Path,
        diags: list[ImpactDiagnostic],
    ) -> ResolvedImpactSnapshots | None:
        resolved, d = ImpactSnapshotResolver(self._conn).resolve(req, root)
        diags.extend(d)
        return resolved

    def _bind_revisions(
        self, req: ImpactRequest, root: Path,
        pair: ResolvedImpactSnapshots, diags: list[ImpactDiagnostic],
    ) -> None:
        """lf-0072: revisões pedidas ↔ snapshots escolhidos."""
        if req.base_revision:
            b, bd = bind(self._conn, root, pair.repository_id,
                         req.base_revision, pair.base_snapshot_id,
                         allow_dirty_check=False)
            if bd is not None:
                diags.append(bd)
            if b.state is BindingState.MISMATCH:
                diags.append(ImpactDiagnostic(
                    "partial-analysis",
                    "base binding divergente — resultados parciais"))
        if req.target_revision:
            b, bd = bind(self._conn, root, pair.repository_id,
                         req.target_revision, pair.target_snapshot_id)
            if bd is not None:
                diags.append(bd)

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
        base_sid = pair.base_snapshot_id
        target_sid = pair.target_snapshot_id

        self._bind_revisions(req, root, pair, diags)

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

        # 6) Blast radius: origens vivas no alvo; REMOVIDAS na base
        # (lf-0074 — a ocorrência atual não existe mais).
        origins_target: dict[str, int] = {}
        origins_base: dict[str, int] = {}
        for s in sym_changes:
            if s.change == SymbolChangeKind.REMOVED:
                if s.old_occurrence_id is not None:
                    origins_base[s.symbol_key] = s.old_occurrence_id
            else:
                occ = s.new_occurrence_id or s.old_occurrence_id
                if occ is not None:
                    origins_target[s.symbol_key] = occ
        impacts, b_trunc = blast_radius.blast(
            self._conn, target_sid, origins_target, req.limits)
        removed_impacts, r_trunc = blast_radius.blast(
            self._conn, base_sid, origins_base, req.limits,
            reason_suffix=" (base snapshot — símbolo removido)")
        b_trunc = b_trunc or r_trunc
        # consumidores históricos perderam a dependência: verified aresta
        # mas o achado é sobre uma relação que deixou de existir.
        impacts = impacts + tuple(removed_impacts)
        if req.known_only or not req.include_possible:
            impacts = tuple(
                i for i in impacts
                if i.strength == EvidenceStrength.VERIFIED)
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
            base_revision=(req.base_revision or pair.base_revision
                           or f"snapshot:{base_sid}"),
            target_revision=(req.target_revision or pair.target_revision
                             or f"snapshot:{target_sid}"),
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
