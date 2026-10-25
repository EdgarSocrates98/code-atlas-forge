"""Orquestrador do Impact Engine (Phases 1-7 reunidas).

Pipeline: ChangeSet (git) → symbol_diff (snapshots) → contract
analysis → blast radius → test selection → risk. Sem snapshot READY
comparável → diagnóstico explícito, nunca fallback silencioso.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from code_atlas.impact import (
    contract_analyzer,
    diff_reader,
    domain_traversal,
    revision_binding,
    risk_classifier,
    symbol_diff,
    test_selector,
)
from code_atlas.impact.contracts import (
    ANALYZER_VERSION,
    IMPACT_SCHEMA_VERSION,
    EvidenceStrength,
    ImpactCandidate,
    ImpactDiagnostic,
    ImpactRequest,
    ImpactResult,
    SymbolChange,
    SymbolChangeKind,
)
from code_atlas.impact.revision_binding import ImpactRevisionPlan
from code_atlas.impact.snapshot_resolver import (
    ImpactSnapshotResolver,
    ResolvedImpactSnapshots,
)

# cap do lookup de seeds what-if por símbolo — declarado como
# ``symbol-seed-limit`` quando cheio (lf-0151).
_SYMBOL_SEED_LIMIT = 64


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
    ) -> ImpactRevisionPlan:
        """lf-0077: binding vira plano — MISMATCH/UNAVAILABLE bloqueia
        a análise semântica (Phase 1 hard invariant)."""
        p = revision_binding.plan(
            self._conn, root, pair.repository_id,
            req.base_revision, req.target_revision,
            pair.base_snapshot_id, pair.target_snapshot_id)
        diags.extend(p.diagnostics)
        return p

    def _reconcile(
        self, removed_impacts: tuple[ImpactCandidate, ...],
        sym_changes: tuple[SymbolChange, ...],
        target_sid: int, repo_id: int,
    ) -> tuple[ImpactCandidate, ...]:
        """lf-0078: classifica o consumidor histórico no destino.

        still_present | consumer_modified | also_removed |
        unresolved_in_target — existência histórica separada do risco.
        lf-0120: candidatos de domínio reconciliam por ``identity_key``
        (presença de ocorrência no alvo / row do repositório).
        """
        if not removed_impacts:
            return removed_impacts
        target_keys = {
            f"{r['language']}:{r['kind']}:{r['qualified_name']}"
            for r in self._conn.execute(
                "SELECT s.language, s.kind, s.qualified_name"
                " FROM symbol_occurrences o JOIN symbols s"
                " ON s.id = o.logical_symbol_id"
                " WHERE o.snapshot_id = ?", (target_sid,))}
        removed_keys = {s.symbol_key for s in sym_changes
                        if s.change == SymbolChangeKind.REMOVED}
        changed_keys = {s.symbol_key for s in sym_changes}
        domain_states: dict[str, set[str]] = {"still_present": set(),
                                              "repo": set()}
        if any(i.node_type == "domain" for i in removed_impacts):
            domain_states["still_present"] = {
                str(r["identity_key"]) for r in self._conn.execute(
                    "SELECT DISTINCT de.identity_key"
                    " FROM domain_entity_occurrences deo"
                    " JOIN domain_entities de ON de.id = deo.entity_id"
                    " WHERE deo.snapshot_id = ?", (target_sid,))}
            domain_states["repo"] = {
                str(r["identity_key"]) for r in self._conn.execute(
                    "SELECT identity_key FROM domain_entities"
                    " WHERE repository_id = ?", (repo_id,))}
        out = []
        for i in removed_impacts:
            if i.node_type == "domain":
                if i.symbol_key in domain_states["still_present"]:
                    state = "still_present"
                elif i.symbol_key in domain_states["repo"]:
                    state = "also_removed"
                else:
                    state = "unresolved_in_target"
            elif i.symbol_key in removed_keys:
                state = "also_removed"
            elif i.symbol_key in changed_keys:
                state = "consumer_modified"
            elif i.symbol_key in target_keys:
                state = "still_present"
            else:
                state = "unresolved_in_target"
            out.append(replace(i, consumer_state=state))
        return tuple(out)

    def _blast(
        self, req: ImpactRequest, pair: ResolvedImpactSnapshots,
        sym_changes: tuple[SymbolChange, ...],
        base_sid: int, target_sid: int,
        unknown: tuple[str, ...],
        diags: list[ImpactDiagnostic],
    ) -> tuple[
            tuple[ImpactCandidate, ...], set[str], tuple[str, ...]]:
        """Passo 6: blast misto (código + domínio) nos dois snapshots.

        Origens: ocorrências vivas de símbolos alterados no alvo;
        REMOVIDAS na base (lf-0074). lf-0120 soma seeds de entidade —
        ``entity`` explícito; ``symbol`` também resolve refs de
        entidade (fiação mínima — handlers que já passam
        ``symbol``/``ident`` aceitam seeds de domínio sem mudança de
        assinatura). Retorna ``(impactos, razões_de_truncamento,
        unknown_coverage)`` — lf-0151: cada bound que descartou
        trabalho entra no conjunto de razões, nunca silencioso.
        """
        reasons: set[str] = set()
        origins_target, origins_base = self._origins(sym_changes)
        # lf-0120: ``symbol`` também é seed de símbolo — "se ESTE
        # símbolo mudar" cobre ocorrências não-alteradas (what-if),
        # não só as que o diff marcou como origens.
        if req.symbol:
            seeds_sym, seed_capped = self._symbol_seeds(
                req.symbol, target_sid)
            if seed_capped:
                reasons.add("symbol-seed-limit")
                diags.append(ImpactDiagnostic(
                    "symbol-seed-limit",
                    f"lookup de seeds por símbolo atingiu o cap de "
                    f"{_SYMBOL_SEED_LIMIT} ocorrências — ocorrências "
                    "além do cap não viraram origem de blast"))
            for key, occ in seeds_sym.items():
                origins_target.setdefault(key, occ)

        refs: list[tuple[str, bool]] = []
        if req.entity:
            refs.append((req.entity, True))
        if req.symbol:
            refs.append((req.symbol, False))
        seeds = domain_traversal.resolve_seeds(
            self._conn, pair.repository_id, tuple(refs),
            base_sid, target_sid)
        diags.extend(seeds.diagnostics)

        # Estado dos dados de domínio no alvo — flag de ausência
        # honesta (spec: parcial marcado, nunca erro silencioso).
        unknown_list = list(unknown) + self._domain_flags(target_sid)

        t_out = domain_traversal.blast(
            self._conn, target_sid,
            sym_origins=origins_target,
            ent_origins=seeds.target_origins, limits=req.limits)
        impacts = t_out.candidates
        reasons |= set(t_out.reasons)
        unc = dict(t_out.uncertain)
        cross_snap = t_out.cross_snapshot
        removed_impacts: tuple[ImpactCandidate, ...] = ()
        if base_sid != target_sid and (origins_base or
                                       seeds.base_origins):
            r_out = domain_traversal.blast(
                self._conn, base_sid,
                sym_origins=origins_base,
                ent_origins=seeds.base_origins, limits=req.limits,
                reason_suffix=" (base snapshot — símbolo removido)")
            removed_impacts = r_out.candidates
            reasons |= set(r_out.reasons)
            cross_snap += r_out.cross_snapshot
            for st, n in r_out.uncertain.items():
                unc[st] = unc.get(st, 0) + n
        removed_impacts = self._reconcile(
            removed_impacts, sym_changes, target_sid,
            pair.repository_id)
        impacts = impacts + removed_impacts
        for st in sorted(unc):
            unknown_list.append(f"domain-{st}-deps:{unc[st]}")
        if cross_snap:
            unknown_list.append(f"domain-cross-snapshot:{cross_snap}")
        return impacts, reasons, tuple(unknown_list)

    @staticmethod
    def _origins(
        sym_changes: tuple[SymbolChange, ...],
    ) -> tuple[dict[str, int], dict[str, int]]:
        """``(origens no alvo, origens na base)`` a partir do diff."""
        target: dict[str, int] = {}
        base: dict[str, int] = {}
        for s in sym_changes:
            if s.change == SymbolChangeKind.REMOVED:
                if s.old_occurrence_id is not None:
                    base[s.symbol_key] = s.old_occurrence_id
            else:
                occ = s.new_occurrence_id or s.old_occurrence_id
                if occ is not None:
                    target[s.symbol_key] = occ
        return target, base

    def _symbol_seeds(
        self, ref: str, target_sid: int,
    ) -> tuple[dict[str, int], bool]:
        """Ocorrências-alvo cujo ``symbol_key``/``qualified_name``
        casa ``ref`` — seeds de símbolo para análise what-if.

        Retorna ``(seeds, atingiu_cap)``: o lookup é bounded por
        ``_SYMBOL_SEED_LIMIT`` com uma linha de sonda +1 — só overflow
        real declara o cap (ocorrências além do prefixo
        ``ORDER BY o.id`` nunca viraram origem, lf-0151)."""
        out: dict[str, int] = {}
        rows = self._conn.execute(
            "SELECT o.id AS occ, s.language, s.kind,"
            " s.qualified_name FROM symbol_occurrences o"
            " JOIN symbols s ON s.id = o.logical_symbol_id"
            " WHERE o.snapshot_id = ? AND ("
            "  s.language || ':' || s.kind || ':'"
            "   || s.qualified_name = ?"
            "  OR instr(s.qualified_name, ?) > 0)"
            " ORDER BY o.id LIMIT ?",
            (target_sid, ref, ref,
             _SYMBOL_SEED_LIMIT + 1)).fetchall()
        capped = len(rows) > _SYMBOL_SEED_LIMIT
        for r in rows[:_SYMBOL_SEED_LIMIT]:
            key = (f"{r['language']}:{r['kind']}:"
                   f"{r['qualified_name']}")
            out.setdefault(key, int(r["occ"]))
        return out, capped

    def _domain_flags(self, target_sid: int) -> list[str]:
        """Flag de ausência/parcialidade dos dados de domínio."""
        dstate = domain_traversal.domain_state(
            self._conn, target_sid, self._snapshot_versions(target_sid))
        if dstate == "adapters-disabled":
            return ["domain-adapters-disabled"]
        if dstate == "empty":
            return ["domain-data-empty"]
        return []

    def _snapshot_versions(self, sid: int) -> dict[str, str]:
        """``analyzer_versions`` de um snapshot — ``{}`` se corrompido."""
        row = self._conn.execute(
            "SELECT analyzer_versions FROM snapshots WHERE id = ?",
            (sid,)).fetchone()
        if row is None:
            return {}
        try:
            v = json.loads(str(row["analyzer_versions"] or "{}"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(v, dict):
            return {}
        return {str(k): str(x) for k, x in v.items()}

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

        rev_plan = self._bind_revisions(req, root, pair, diags)
        if not rev_plan.semantic_analysis_allowed:
            # Phase 1: ChangeSet Git-only preservado; sem SymbolDiff/
            # Blast/TestSelector sob aparência de resultado válido.
            return ImpactResult(
                schema_version=IMPACT_SCHEMA_VERSION,
                analyzer_version=ANALYZER_VERSION,
                repository=req.repository_root,
                base_revision=req.base_revision or pair.base_revision
                or f"snapshot:{base_sid}",
                target_revision=req.target_revision or pair.target_revision
                or f"snapshot:{target_sid}",
                base_snapshot_id=base_sid, target_snapshot_id=target_sid,
                change_set=change_set, symbol_changes=(),
                contract_changes=(), impacts=(), test_candidates=(),
                risk=risk_classifier.assess((), (), (), ()),
                unknown_coverage=("semantic-blocked",),
                truncated=False, diagnostics=tuple(diags))

        # 3) Symbol diff.
        sym_changes, unknown, diff_trunc = symbol_diff.diff_snapshots(
            self._conn, base_sid, target_sid,
            max_changes=req.limits.max_changes)
        reasons: set[str] = {"max-changes"} if diff_trunc else set()

        # 4) Seleção explícita de símbolo restringe a análise.
        if req.symbol:
            sym_changes = tuple(
                s for s in sym_changes
                if req.symbol in s.qualified_name)

        # 5) Contracts.
        contract_changes = contract_analyzer.analyze(sym_changes)

        # 6) Blast radius: origens vivas no alvo; REMOVIDAS na base
        # (lf-0074 — a ocorrência atual não existe mais). lf-0120: o
        # blast é misto — relações de código + relações de domínio —
        # e seeds podem ser entidades (``req.entity``/``req.symbol``).
        impacts, b_reasons, unknown = self._blast(
            req, pair, sym_changes, base_sid, target_sid, unknown,
            diags)
        reasons |= b_reasons
        if req.known_only or not req.include_possible:
            impacts = tuple(
                i for i in impacts
                if i.strength == EvidenceStrength.VERIFIED)

        # 7) Testes + risco. lf-0151: o cap de candidatos também é
        # bound declarado — saída cheia não prova que não havia mais.
        tests = test_selector.select(
            self._conn, target_sid, sym_changes, impacts,
            base_snapshot_id=base_sid)
        if len(tests) >= test_selector.MAX_TEST_CANDIDATES:
            reasons.add("test-candidates")
        risk = risk_classifier.assess(
            sym_changes, contract_changes, impacts, unknown)
        truncation_reasons = tuple(sorted(reasons))

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
            truncated=bool(truncation_reasons),
            diagnostics=tuple(diags),
            truncation_reasons=truncation_reasons)

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
