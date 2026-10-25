"""lf-0135 (Cycle 8 Phase 9) — blast radius contra verdade-terreno rotulada.

Corpus de cenários em ``evaluation/impact_truth/scenarios.json``: cada
cenário rotula **à mão** o conjunto esperado de impacto
(``direct``/``transitive``/``absent``) a partir da leitura da fixture e
da semântica declarada de travessia — arestas ``*_DEPENDS_ON`` propagam
``to → from``, arestas acopladas (``WRITES_TO``, ``TRANSFORMS``,
``PROVISIONS``, ``TRIGGERS``, ``DEPLOYS``, ``DEFINES``) propagam nos
dois sentidos, ``REFERENCES`` só quando é par de identidade.

O que é medido por cenário (pipeline REAL — o binário
``code-atlas``/``cli.main`` dirigido in-process com
``CODE_ATLAS_DATA_DIR``/``CODE_ATLAS_CONFIG_DIR`` isolados num
workspace temporário; nada é executado dos repos indexados):

- ``index <fixture> --domains <ids> [--cross-domain]`` — indexação real;
- ``impact symbol <seed> --root <fixture> --format json`` — modo de
  domínio: a ref de entidade atravessa ``resolve_seeds`` (via
  ``ImpactRequest.symbol``, a fiação mínima do lf-0120);
- precision/recall do conjunto alcançável (deduplicado por nó) vs
  rótulo — por classe (direct/transitive) e agregado;
- **false-confidence**: todo candidato ``verified`` precisa ter
  ``fully_verified`` + hops ``resolved``; itens rotulados possíveis
  jamais podem subir a verified — orçamento ZERO, asserção dura;
- **evidence validity**: todo ``edge_refs`` aponta para uma row real do
  snapshot alvo (``dom:N`` → ``domain_relations``, ``rel:N`` →
  ``relations``), ``hops == len(edge_refs) == len(statuses)`` e a
  ocorrência do candidato existe no snapshot;
- **truncation honesty**: re-run com ``--max-paths 1`` — ``truncated``
  declarado sse o run completo tem mais candidatos que o limite;
- ``unknown_coverage`` e ``diagnostics`` reportados verbatim.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCENARIO_SCHEMA = 1
RESULTS_SCHEMA = 1

_ENV_KEYS = ("CODE_ATLAS_DATA_DIR", "CODE_ATLAS_CONFIG_DIR")

# Classe de impacto em ordem decrescente de "alcançável verificado".
_IMPACT_RANK = {"direct": 0, "transitive": 1, "possible": 2, "unknown": 3}
_STRENGTH_RANK = {"verified": 0, "supported": 1, "possible": 2,
                  "unknown": 3}

#: Folga acima do total de linhas no probe "over-cap" — o traversal
#: termina naturalmente, ``truncated`` deve ser False.
TRUNCATION_OVERCAP_SLACK = 3


@dataclass(frozen=True)
class ImpactScenario:
    """Cenário rotulado — fixture + seed + conjuntos esperados."""

    scenario_id: str
    prompt_class: str
    fixture: str
    domains: tuple[str, ...]
    cross_domain: bool
    seed: str
    task: str
    expected_direct: tuple[str, ...]
    expected_transitive: tuple[str, ...]
    expected_absent: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class CliRun:
    """Resultado de uma invocação in-process do CLI real."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


def load_scenarios(path: Path) -> list[ImpactScenario]:
    """Carrega ``scenarios.json`` — validação estrutural mínima."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema", 0)) != SCENARIO_SCHEMA:
        raise ValueError(f"impact truth schema != {SCENARIO_SCHEMA}")
    out: list[ImpactScenario] = []
    for s in raw["scenarios"]:
        exp = s["expected"]
        out.append(ImpactScenario(
            scenario_id=str(s["id"]),
            prompt_class=str(s.get("prompt_class", "")),
            fixture=str(s["fixture"]),
            domains=tuple(str(d) for d in s["domains"]),
            cross_domain=bool(s.get("cross_domain", False)),
            seed=str(s["seed"]),
            task=str(s.get("task", "")),
            expected_direct=tuple(str(x) for x in exp.get("direct", ())),
            expected_transitive=tuple(
                str(x) for x in exp.get("transitive", ())),
            expected_absent=tuple(str(x) for x in exp.get("absent", ())),
            notes=str(s.get("notes", "")),
        ))
    return out


def validate_scenario(repo_root: Path, sc: ImpactScenario) -> list[str]:
    """Higiene do rótulo: conjuntos disjuntos, fixture existente."""
    errs: list[str] = []
    if not sc.seed.strip():
        errs.append("seed vazia")
    if not sc.domains:
        errs.append("domains vazio — o cenário precisa de adapters")
    if sc.cross_domain and len(sc.domains) < 2:
        errs.append("cross_domain com <2 adapters")
    if not (repo_root / sc.fixture).is_dir():
        errs.append(f"fixture ausente: {sc.fixture}")
    seen: dict[str, str] = {}
    for label, items in (
        ("direct", sc.expected_direct),
        ("transitive", sc.expected_transitive),
        ("absent", sc.expected_absent),
    ):
        for name in items:
            if name in seen:
                errs.append(
                    f"'{name}' rotulado em {seen[name]} e {label}")
            seen[name] = label
    return errs


# ---------------------------------------------------------------------
# harness — invoca o CLI real in-process, isolado por env
# ---------------------------------------------------------------------


def run_cli(
    argv: Sequence[str], *, data_dir: Path, config_dir: Path
) -> CliRun:
    """Executa ``code-atlas <argv>`` in-process (argparse → run_* →
    stdout), com ``CODE_ATLAS_DATA_DIR``/``CODE_ATLAS_CONFIG_DIR``
    apontados para o workspace temporário. É a mesma superfície do
    binário — só o transporte é in-process."""
    from code_atlas.cli import main as cli_main

    old = {k: os.environ.get(k) for k in _ENV_KEYS}
    os.environ["CODE_ATLAS_DATA_DIR"] = str(data_dir)
    os.environ["CODE_ATLAS_CONFIG_DIR"] = str(config_dir)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = int(cli_main(list(argv)))
    finally:
        for key, val in old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    return CliRun(tuple(argv), code, out_buf.getvalue(),
                  err_buf.getvalue())


def index_fixture(
    fixture: Path, domains: Sequence[str], cross_domain: bool,
    *, data_dir: Path, config_dir: Path,
) -> CliRun:
    """``index <fixture> --domains ...`` — pipeline real de indexação."""
    argv = ["index", str(fixture)]
    if domains:
        argv += ["--domains", ",".join(domains)]
    if cross_domain:
        argv += ["--cross-domain"]
    argv.append("--json")
    return run_cli(argv, data_dir=data_dir, config_dir=config_dir)


def run_impact(
    fixture: Path, seed: str, *, data_dir: Path, config_dir: Path,
    extra_args: Sequence[str] = (),
) -> tuple[CliRun, dict[str, Any] | None]:
    """``impact symbol <seed> --root <fixture> --format json`` → payload."""
    argv = ["impact", "symbol", seed, "--root", str(fixture),
            "--format", "json", *extra_args]
    run = run_cli(argv, data_dir=data_dir, config_dir=config_dir)
    payload: dict[str, Any] | None = None
    text = run.stdout.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    return run, payload


# ---------------------------------------------------------------------
# dedup + métricas
# ---------------------------------------------------------------------


@dataclass
class _Node:
    """Vista deduplicada de um nó impactado (várias linhas por
    caminhos alternativos colapsam aqui)."""

    name: str
    node_type: str
    kind: str
    best_impact: str = "unknown"
    best_strength: str = "unknown"
    min_hops: int = 0
    min_verified_hops: int | None = None
    rows: int = 0
    fully_verified_any: bool = False

    def absorb(self, cand: Mapping[str, Any]) -> None:
        impact = str(cand.get("impact", "unknown"))
        strength = str(cand.get("strength", "unknown"))
        path = cand.get("path") or {}
        hops = int(path.get("hops") or 0)
        verified = bool(path.get("fully_verified"))
        if _IMPACT_RANK.get(impact, 9) < _IMPACT_RANK.get(
                self.best_impact, 9):
            self.best_impact = impact
        if _STRENGTH_RANK.get(strength, 9) < _STRENGTH_RANK.get(
                self.best_strength, 9):
            self.best_strength = strength
        if self.rows == 0 or hops < self.min_hops:
            self.min_hops = hops
        if verified and (
                self.min_verified_hops is None
                or hops < self.min_verified_hops):
            self.min_verified_hops = hops
        self.fully_verified_any = self.fully_verified_any or verified
        self.rows += 1


def dedup_candidates(
    payload: Mapping[str, Any],
) -> dict[tuple[str, str], _Node]:
    """Colapsa linhas de ``impacts`` por ``(node_type, qualified_name)``."""
    out: dict[tuple[str, str], _Node] = {}
    for cand in payload.get("impacts") or ():
        if not isinstance(cand, Mapping):
            continue
        key = (str(cand.get("node_type", "symbol")),
               str(cand.get("qualified_name", "")))
        node = out.get(key)
        if node is None:
            node = _Node(name=key[1], node_type=key[0],
                         kind=str(cand.get("kind", "")))
            out[key] = node
        node.absorb(cand)
    return out


def _pr(tp: int, fp: int, fn: int) -> dict[str, Any]:
    """precision/recall/f1 — ``None`` quando indefinido (nunca 1.0
    fabricado por denominador vazio)."""
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else
          (0.0 if precision == 0.0 or recall == 0.0 else None))
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


def _class_diff(
    actual: set[str], expected: Sequence[str], other_expected: set[str],
) -> dict[str, Any]:
    """Diff de uma classe: ``other_expected`` evita que um item
    esperado na OUTRA classe vire FP aqui — vira class_mismatch."""
    exp = set(expected)
    tp = actual & exp
    fp = actual - exp - other_expected
    fn = exp - actual
    mismatched = sorted((actual - exp) & other_expected)
    rep = _pr(len(tp), len(fp), len(fn))
    rep["missing"] = sorted(fn)
    rep["unexpected"] = sorted(fp)
    rep["class_mismatches"] = mismatched
    return rep


# ---------------------------------------------------------------------
# invariantes — false-confidence + evidência + truncamento
# ---------------------------------------------------------------------


def false_confidence_leaks(payload: Mapping[str, Any]) -> list[str]:
    """Invasões de confiança — ORÇAMENTO ZERO.

    - ``strength=verified`` exige ``fully_verified`` e todos os hops
      ``resolved``;
    - ``impact=direct|transitive`` implica ``strength=verified``;
    - ``impact=possible|unknown`` nunca pode ter ``strength=verified``.
    """
    leaks: list[str] = []
    for cand in payload.get("impacts") or ():
        name = str(cand.get("qualified_name", "?"))
        strength = str(cand.get("strength", "unknown"))
        impact = str(cand.get("impact", "unknown"))
        path = cand.get("path") or {}
        statuses = [str(s) for s in (path.get("resolution_statuses")
                                     or ())]
        verified_path = bool(path.get("fully_verified")) and all(
            s == "resolved" for s in statuses)
        if strength == "verified" and not verified_path:
            leaks.append(
                f"{name}: strength=verified mas path não provado "
                f"(statuses={statuses}, "
                f"fully_verified={path.get('fully_verified')})")
        if impact in ("direct", "transitive") and strength != "verified":
            leaks.append(
                f"{name}: impact={impact} com strength={strength}")
        if impact in ("possible", "unknown") and strength == "verified":
            leaks.append(
                f"{name}: impact={impact} promovido a verified")
    return leaks


def _snapshot_id_sets(
    conn: sqlite3.Connection, snapshot_id: int,
) -> dict[str, set[int]]:
    """Ids reais de cada tabela de evidência do snapshot alvo."""
    return {
        "rel": {int(r[0]) for r in conn.execute(
            "SELECT id FROM relations WHERE snapshot_id = ?",
            (snapshot_id,))},
        "dom": {int(r[0]) for r in conn.execute(
            "SELECT id FROM domain_relations WHERE snapshot_id = ?",
            (snapshot_id,))},
        "sym_occ": {int(r[0]) for r in conn.execute(
            "SELECT id FROM symbol_occurrences WHERE snapshot_id = ?",
            (snapshot_id,))},
        "dom_occ": {int(r[0]) for r in conn.execute(
            "SELECT id FROM domain_entity_occurrences"
            " WHERE snapshot_id = ?",
            (snapshot_id,))},
    }


def _check_path(
    name: str, key: str, cand: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Consistência interna do ``ImpactPath`` — retorna
    ``(erros, edge_refs, statuses)``."""
    errors: list[str] = []
    path = cand.get("path")
    if not isinstance(path, Mapping):
        return [f"{name}: sem ImpactPath"], [], []
    edge_refs = [str(r) for r in (path.get("edge_refs") or ())]
    statuses = [str(s) for s in (path.get("resolution_statuses") or ())]
    hops = int(path.get("hops") or 0)
    if hops != len(edge_refs):
        errors.append(
            f"{name}: hops={hops} != {len(edge_refs)} edge_refs")
    if hops != len(statuses):
        errors.append(
            f"{name}: hops={hops} != {len(statuses)} statuses")
    if str(path.get("affected_symbol_key", "")) != key:
        errors.append(
            f"{name}: affected_symbol_key "
            f"{path.get('affected_symbol_key')!r} != symbol_key {key!r}")
    mids = [str(m) for m in (path.get("intermediate_keys") or ())]
    if mids and mids[-1] != key:
        errors.append(
            f"{name}: intermediate_keys[-1]={mids[-1]!r} != "
            f"symbol_key {key!r}")
    if not str(path.get("origin_symbol_key", "")):
        errors.append(f"{name}: origin_symbol_key vazio")
    return errors, edge_refs, statuses


def _check_edge_refs(
    name: str, edge_refs: list[str], ids: Mapping[str, set[int]],
    snapshot_id: int,
) -> list[str]:
    """Cada ``dom:N``/``rel:N`` aponta para row real do snapshot."""
    errors: list[str] = []
    for ref in edge_refs:
        prefix, sep, num = ref.partition(":")
        if not sep or not num.isdigit():
            errors.append(f"{name}: edge_ref malformado {ref!r}")
            continue
        table = ids.get(prefix)
        if table is None:
            errors.append(
                f"{name}: edge_ref de tipo desconhecido {ref!r}")
        elif int(num) not in table:
            errors.append(
                f"{name}: edge_ref {ref} não existe no snapshot "
                f"{snapshot_id}")
    return errors


def _check_occurrence(
    name: str, cand: Mapping[str, Any], ids: Mapping[str, set[int]],
) -> list[str]:
    """A ocorrência do candidato existe no snapshot alvo (espaço de
    ids certo para ``node_type``)."""
    if str(cand.get("node_type", "symbol")) == "domain":
        occ = cand.get("domain_occurrence_id")
        errors = [] if occ is not None and int(occ) in ids["dom_occ"] \
            else [f"{name}: domain_occurrence_id {occ} fora do "
                  "snapshot"]
        if cand.get("occurrence_id") is not None:
            errors.append(
                f"{name}: nó de domínio com occurrence_id de símbolo")
        return errors
    occ = cand.get("occurrence_id")
    if occ is None or int(occ) not in ids["sym_occ"]:
        return [f"{name}: occurrence_id {occ} fora do snapshot"]
    return []


def validate_evidence(
    payload: Mapping[str, Any], conn: sqlite3.Connection,
    snapshot_id: int,
) -> list[str]:
    """Cada candidato carrega prova real: path consistente, edge_refs
    resolvíveis no snapshot alvo, ocorrência existente."""
    errors: list[str] = []
    ids = _snapshot_id_sets(conn, snapshot_id)
    for cand in payload.get("impacts") or ():
        name = str(cand.get("qualified_name", "?"))
        key = str(cand.get("symbol_key", ""))
        errs, edge_refs, _st = _check_path(name, key, cand)
        errors.extend(errs)
        errors.extend(
            _check_edge_refs(name, edge_refs, ids, snapshot_id))
        errors.extend(_check_occurrence(name, cand, ids))
    return errors


def _probe_trunc(
    full_rows: int, capped: Mapping[str, Any], limit: int,
) -> dict[str, Any]:
    """Uma sonda de truncamento num ``--max-paths`` dado.

    Semântica medida do engine: ``truncated`` é declarado quando a
    emissão ATINGE o cap (``len(out) >= max_paths``) — o traversal para
    ali e não pode provar que nada mais existia. Honesto =
    ``declared == (capped_rows == limit)`` e ``capped_rows <= limit``.
    """
    capped_rows = len(capped.get("impacts") or ())
    declared = bool(capped.get("truncated"))
    expected = capped_rows == limit
    return {
        "limit": limit,
        "full_rows": full_rows,
        "capped_rows": capped_rows,
        "expected_truncated": expected,
        "declared_truncated": declared,
        "within_limit": capped_rows <= limit,
        "honest": declared == expected and capped_rows <= limit,
    }


def truncation_check(
    full: Mapping[str, Any],
    at_cap: Mapping[str, Any] | None,
    over_cap: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Honestidade de truncamento — duas sondas por cenário.

    ``at_cap`` (``--max-paths = max(1, full_rows)``): a emissão bate no
    cap — ``truncated=True`` é a resposta honesta mesmo quando o
    conjunto completo tem exatamente ``full_rows`` linhas (prefixo no
    limite é declarado, nunca "completo" duvidoso).

    ``over_cap`` (``--max-paths = full_rows + slack``): o traversal
    termina naturalmente — ``truncated=False`` é obrigatório e todas
    as ``full_rows`` linhas têm que sair (cap solto não pode cortar).
    """
    full_rows = len(full.get("impacts") or ())
    cap = max(1, full_rows)
    probes: dict[str, Any] = {}
    honest = True
    if at_cap is None:
        probes["at_cap"] = {"limit": cap, "error": "sem JSON"}
        honest = False
    else:
        probes["at_cap"] = _probe_trunc(full_rows, at_cap, cap)
        honest = honest and bool(probes["at_cap"]["honest"])
    free = full_rows + TRUNCATION_OVERCAP_SLACK
    if over_cap is None:
        probes["over_cap"] = {"limit": free, "error": "sem JSON"}
        honest = False
    else:
        probes["over_cap"] = _probe_trunc(full_rows, over_cap, free)
        oc = probes["over_cap"]
        # over-cap também exige conjunto completo (cap solto ≠ corte).
        oc["emitted_all"] = oc["capped_rows"] == full_rows
        honest = (honest and bool(oc["honest"])
                  and bool(oc["emitted_all"]))
    return {"probes": probes, "honest": honest}


# ---------------------------------------------------------------------
# avaliação
# ---------------------------------------------------------------------


def _seed_entity_count(
    conn: sqlite3.Connection, repository_id: int, seed: str,
) -> int:
    """Quantas entidades a ref de seed casa — detecta cenário cujo
    blast vazio vem de seed não resolvida (falso negativo de medição)."""
    from code_atlas.impact.domain_traversal import resolve_entity_refs

    rows, _how = resolve_entity_refs(conn, repository_id, seed)
    return len(rows)


def evaluate_scenario(
    repo_root: Path, sc: ImpactScenario, conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Mede um cenário num repositório JÁ indexado (``conn`` aberto no
    ``atlas.db`` do workspace temporário)."""
    out: dict[str, Any] = {
        "id": sc.scenario_id,
        "prompt_class": sc.prompt_class,
        "fixture": sc.fixture,
        "seed": sc.seed,
        "task": sc.task,
        "notes": sc.notes,
        "expected": {
            "direct": len(sc.expected_direct),
            "transitive": len(sc.expected_transitive),
            "absent": len(sc.expected_absent),
        },
    }
    fixture = repo_root / sc.fixture
    data_dir = Path(os.environ["CODE_ATLAS_DATA_DIR"])
    config_dir = Path(os.environ["CODE_ATLAS_CONFIG_DIR"])

    run, payload = run_impact(
        fixture, sc.seed, data_dir=data_dir, config_dir=config_dir)
    out["impact_exit"] = run.exit_code
    if payload is None:
        out["error"] = (
            f"impact não retornou JSON (exit {run.exit_code}): "
            f"{run.stderr.strip()[:200]}")
        return out
    sid = payload.get("target_snapshot_id")
    if not isinstance(sid, int):
        out["error"] = "impact sem target_snapshot_id"
        out["diagnostics"] = [
            d.get("code") for d in payload.get("diagnostics", ())
            if isinstance(d, Mapping)]
        return out

    repo_row = conn.execute(
        "SELECT repository_id FROM snapshots WHERE id = ?",
        (sid,)).fetchone()
    repo_id = int(repo_row[0]) if repo_row else -1

    nodes = dedup_candidates(payload)
    actual_direct = {k[1] for k, n in nodes.items()
                     if n.best_impact == "direct"}
    actual_transitive = {k[1] for k, n in nodes.items()
                         if n.best_impact == "transitive"}
    actual_possible = {k[1] for k, n in nodes.items()
                       if n.best_impact in ("possible", "unknown")}
    actual_all = {k[1] for k in nodes}

    exp_direct = set(sc.expected_direct)
    exp_transitive = set(sc.expected_transitive)
    exp_reachable = exp_direct | exp_transitive
    exp_absent = set(sc.expected_absent)

    direct = _class_diff(actual_direct, sc.expected_direct,
                         exp_transitive)
    transitive = _class_diff(actual_transitive, sc.expected_transitive,
                             exp_direct)
    verified_actual = actual_direct | actual_transitive
    tp_v = len(verified_actual & exp_reachable)
    fp_v = len(verified_actual - exp_reachable)
    fn_v = len(exp_reachable - verified_actual)
    verified = _pr(tp_v, fp_v, fn_v)
    verified["missing"] = sorted(exp_reachable - verified_actual)
    verified["unexpected"] = sorted(verified_actual - exp_reachable)

    leaks = false_confidence_leaks(payload)
    ev_errors = validate_evidence(payload, conn, sid)
    absent_violations = sorted(actual_all & exp_absent)

    # probes de truncamento — re-run no cap exato e com folga.
    full_rows_n = len(payload.get("impacts") or ())
    _run_cap, at_cap = run_impact(
        fixture, sc.seed, data_dir=data_dir, config_dir=config_dir,
        extra_args=("--max-paths", str(max(1, full_rows_n))))
    _run_free, over_cap = run_impact(
        fixture, sc.seed, data_dir=data_dir, config_dir=config_dir,
        extra_args=("--max-paths",
                    str(full_rows_n + TRUNCATION_OVERCAP_SLACK)))
    trunc = truncation_check(payload, at_cap, over_cap)

    out.update({
        "index_independent_check": {
            "seed_entity_rows": _seed_entity_count(conn, repo_id,
                                                   sc.seed),
            "target_snapshot_id": sid,
        },
        "candidates": {
            "rows": len(payload.get("impacts") or ()),
            "unique": len(nodes),
            "by_best_impact": {
                "direct": len(actual_direct),
                "transitive": len(actual_transitive),
                "possible_or_unknown": len(actual_possible),
            },
        },
        "direct": direct,
        "transitive": transitive,
        "verified": verified,
        "possible_actual": sorted(actual_possible),
        "absent_violations": absent_violations,
        "false_confidence": {
            "leaks": leaks,
            "count": len(leaks),
            "rate": (len(leaks) / len(nodes)) if nodes else 0.0,
        },
        "evidence_errors": ev_errors,
        "unknown_coverage": list(payload.get("unknown_coverage") or ()),
        "truncated": bool(payload.get("truncated")),
        "truncation": trunc,
        "risk": dict(payload.get("risk") or {}),
        "diagnostics": [
            {"code": str(d.get("code")), "message": str(d.get("message"))}
            for d in payload.get("diagnostics", ())
            if isinstance(d, Mapping)
        ],
    })
    return out


@dataclass
class _IndexedFixture:
    """Workspace temporário já indexado para uma fixture."""

    key: tuple[str, tuple[str, ...], bool]
    data_dir: Path
    config_dir: Path
    index_run: CliRun
    conn: sqlite3.Connection = field(init=False)

    def open(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(str(self.data_dir / "atlas.db"))
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.conn.close()


def evaluate_all(
    repo_root: Path, scenarios: Sequence[ImpactScenario],
    work_dir: Path,
) -> dict[str, Any]:
    """Roda todos os cenários: uma indexação real por (fixture,
    adapters, cross) seguida dos impacts de cada cenário nela."""
    results: dict[str, Any] = {"schema": RESULTS_SCHEMA,
                               "scenarios": {}}
    indexed: dict[tuple[str, tuple[str, ...], bool],
                  _IndexedFixture] = {}
    try:
        for sc in scenarios:
            key = (sc.fixture, sc.domains, sc.cross_domain)
            fx = indexed.get(key)
            if fx is None:
                slot = work_dir / f"ws-{len(indexed)}"
                data_dir, config_dir = slot / "data", slot / "cfg"
                data_dir.mkdir(parents=True)
                config_dir.mkdir(parents=True)
                run = index_fixture(
                    repo_root / sc.fixture, sc.domains,
                    sc.cross_domain,
                    data_dir=data_dir, config_dir=config_dir)
                fx = _IndexedFixture(key, data_dir, config_dir, run)
                indexed[key] = fx
            if fx.index_run.exit_code != 0:
                results["scenarios"][sc.scenario_id] = {
                    "id": sc.scenario_id,
                    "error": f"index exit {fx.index_run.exit_code}: "
                             f"{fx.index_run.stderr.strip()[:200]}",
                }
                continue
            conn = fx.open()
            os.environ["CODE_ATLAS_DATA_DIR"] = str(fx.data_dir)
            os.environ["CODE_ATLAS_CONFIG_DIR"] = str(fx.config_dir)
            try:
                results["scenarios"][sc.scenario_id] = \
                    evaluate_scenario(repo_root, sc, conn)
            finally:
                for env_key in _ENV_KEYS:
                    os.environ.pop(env_key, None)
                fx.close()
    finally:
        for env_key in _ENV_KEYS:
            os.environ.pop(env_key, None)

    # sumário agregado — micro-média sobre cenários com conjunto
    # esperado não-vazio; vazios honestos contam como cobertura, não
    # como recall inventado.
    total_tp = total_fp = total_fn = 0
    leaks_total = evidence_total = absent_total = 0
    truncation_bad: list[str] = []
    empty_but_seed_ok: list[str] = []
    for sid_name, rep in results["scenarios"].items():
        if "verified" not in rep:
            continue
        total_tp += int(rep["verified"]["tp"])
        total_fp += int(rep["verified"]["fp"])
        total_fn += int(rep["verified"]["fn"])
        leaks_total += int(rep["false_confidence"]["count"])
        evidence_total += len(rep["evidence_errors"])
        absent_total += len(rep["absent_violations"])
        if rep["truncation"].get("honest") is False:
            truncation_bad.append(sid_name)
        if rep["candidates"]["unique"] == 0 and rep[
                "index_independent_check"]["seed_entity_rows"] > 0:
            empty_but_seed_ok.append(sid_name)
    verified_micro = _pr(total_tp, total_fp, total_fn)
    results["summary"] = {
        "scenarios": len(results["scenarios"]),
        "verified_micro": verified_micro,
        "false_confidence_leaks": leaks_total,
        "evidence_errors": evidence_total,
        "absent_violations": absent_total,
        "truncation_dishonest": truncation_bad,
        "empty_verified_blasts": empty_but_seed_ok,
    }
    return results
