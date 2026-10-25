"""Avaliação medida da extração de domínio (lf-0124, Cycle 7 Phase 16).

Framework evidence-first de precisão medida: cada fixture versionada é
indexada pela **pipeline real** — ``IndexCoordinator`` com
``DomainRegistry`` (e ``CrossDomainResolver`` no cenário cross-domain) —
e o estado persistido (``domain_entities`` + ``domain_relations`` +
``domain_entity_occurrences``) é comparado contra goldens curados.

Dois níveis medidos, sem inventar nada:

- **extração**: ``adapter.extract`` sobre os artefatos que o coordinator
  descobriria (``supported_artifacts`` ∩ scan) — entidades, relações e
  ``DomainDiagnostic`` (que nunca são persistidos por desenho);
- **persistido**: as rows reais do snapshot — ``resolution_status``
  final (``resolved``/``ambiguous``/``unresolved``/``external``),
  ``edges_skipped`` (origem sem âncora) e arestas produzidas pelo
  resolver cross-domain (``resolver='cross-domain'``).

Goldens (``evaluation/golden/*.json``) são inventários **exaustivos**
de arestas + contagens exatas + listas de entidades proibidas — a
comparação é conjunto-a-conjunto (``matched``/``missing``/``unexpected``),
nunca amostragem otimista. ``run_eval.py --bless`` regenera; qualquer
diff vira falha legível no pytest.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from code_atlas.discovery.identity import identity_key
from code_atlas.discovery.scanner import scan as default_scan
from code_atlas.domains.contracts import (
    ArtifactSupport,
    DiscoveredArtifact,
    DomainAdapter,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.crossresolve import CrossDomainResolver
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.registry import DomainRegistry
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store

GOLDEN_SCHEMA = 1
RESULTS_SCHEMA = 1

#: Prefixes que distinguem o tipo de endpoint na forma renderizada da
#: aresta: entidade ``dom:cat.ns.name@env``, símbolo ``sym:qname`` e
#: alvo textual honesto ``txt:...`` (unresolved/external declarado).
SYM_PREFIX = "sym:"
TXT_PREFIX = "txt:"

AdapterFactory = Callable[[Path], DomainAdapter]


def _sql(fx: Path) -> DomainAdapter:
    from code_atlas.domains.sql import SqlDomainAdapter

    return SqlDomainAdapter(base_path=fx)


def _spark(fx: Path) -> DomainAdapter:
    from code_atlas.domains.spark import PySparkAdapter

    return PySparkAdapter(base_path=fx)


def _aws(fx: Path) -> DomainAdapter:
    from code_atlas.domains.aws import AwsDataAdapter

    return AwsDataAdapter(base_path=fx)


def _api(fx: Path) -> DomainAdapter:
    from code_atlas.domains.api import ApiContractAdapter

    return ApiContractAdapter(workspace_root=fx)


def _terraform(fx: Path) -> DomainAdapter:
    from code_atlas.domains.terraform import TerraformAdapter

    return TerraformAdapter(base_path=fx)


def _cicd(fx: Path) -> DomainAdapter:
    from code_atlas.domains.cicd import GitHubActionsAdapter

    return GitHubActionsAdapter.for_workspace(fx)


_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "sql": _sql,
    "pyspark": _spark,
    "aws-data": _aws,
    "api": _api,
    "terraform": _terraform,
    "cicd": _cicd,
}


@dataclass(frozen=True)
class DomainSpec:
    """Cenário de avaliação: fixture + adapters + resolução cross.

    ``adapter_ids`` são os ``domain_id`` reais registrados no
    ``DomainRegistry`` — para cenários cross-domain mais de um adapter
    observa o mesmo snapshot. ``cross=True`` injeta o
    ``CrossDomainResolver`` na pipeline (lf-0117).
    """

    eval_id: str
    fixture: str  # relativo à raiz do repositório
    adapter_ids: tuple[str, ...]
    cross: bool = False
    note: str = ""


#: Cenários avaliados — um por fixture versionada + o workspace
#: cross-domain (lf-0124). Ordem estável = ordem do relatório.
DOMAIN_SPECS: tuple[DomainSpec, ...] = (
    DomainSpec(
        "sql", "tests/fixtures/sql_warehouse", ("sql",),
        note="pipeline landing→staging→mart + arquivo malformado",
    ),
    DomainSpec(
        "pyspark", "tests/fixtures/pyspark_repo", ("pyspark",),
        note="jobs DataFrame/spark.sql/temp views + dinâmicos honestos",
    ),
    DomainSpec(
        "aws", "tests/fixtures/aws_repo", ("aws-data",),
        note="Glue/Athena/Iceberg/EMR + catálogo cross-account + configs",
    ),
    DomainSpec(
        "api", "tests/fixtures/api_repo", ("api",),
        note="OpenAPI + FastAPI/Express + consumidores (2 edges_skipped "
        "— web.legacy sem parser JS)",
    ),
    DomainSpec(
        "terraform", "tests/fixtures/terraform_infra", ("terraform",),
        note="HCL + tf.json + tfvars + módulo local/registry + broken/",
    ),
    DomainSpec(
        "cicd", "tests/fixtures/cicd_repo", ("cicd",),
        note="GitHub Actions workflows/jobs/deps + workflow malformado",
    ),
    DomainSpec(
        "crossdomain", "tests/fixtures/crossdomain_ws",
        ("sql", "pyspark", "api"),
        cross=True,
        note="workspace multi-adapter: spark↔sql identidade plena e "
        "parcial, consumidor↔endpoint OpenAPI (lf-0117)",
    ),
)


def spec_by_id(eval_id: str) -> DomainSpec:
    for spec in DOMAIN_SPECS:
        if spec.eval_id == eval_id:
            return spec
    raise KeyError(f"unknown eval domain: {eval_id!r}")


def _display(identity_key_str: str) -> str:
    """``identity_key`` canônica → forma legível ``dom:cat.ns.name@env``."""
    segs = DomainIdentity.parse_key(identity_key_str)
    if segs is None or len(segs) < 7:
        return identity_key_str
    domain, repository, root, catalog, namespace, env, qname = segs[:7]
    return DomainIdentity(
        domain=domain, repository=repository, root=root, catalog=catalog,
        namespace=namespace, environment=env, qualified_name=qname,
    ).display()


def _discover_artifacts(
    files: Mapping[str, str], support: ArtifactSupport
) -> tuple[DiscoveredArtifact, ...]:
    """Mesmo recorte que ``IndexCoordinator._extract_domains`` aplica:
    ``supported_artifacts`` ∩ inventário, ordenado, com sha do scan."""
    return tuple(
        DiscoveredArtifact(path=p, sha256=files[p])
        for p in sorted(p for p in files if support.matches(p))
    )


def _extract_for_diagnostics(
    adapters: Mapping[str, DomainAdapter],
    files: Mapping[str, str],
    repository: str,
    snapshot: str,
) -> dict[str, DomainExtractionResult]:
    """Re-roda ``extract`` com o MESMO request do coordinator.

    ``DomainDiagnostic`` nunca é persistido (desenho da v9) — para
    medi-lo reexecutamos os adapters, que são puros/determinísticos
    sobre o mesmo recorte (evidence-first, sem I/O fora do request).
    """
    results: dict[str, DomainExtractionResult] = {}
    for domain_id, adapter in adapters.items():
        artifacts = _discover_artifacts(files, adapter.supported_artifacts())
        results[domain_id] = adapter.extract(
            DomainExtractionRequest(
                repository=repository,
                artifacts=artifacts,
                snapshot=snapshot,
            )
        )
    return results


def _read_persisted(
    conn: sqlite3.Connection, store: Store, snapshot_id: int
) -> dict[str, object]:
    """Estado persistido do snapshot → relatório de domínio."""
    occ_display: dict[int, str] = {}
    occ_kind: dict[int, str] = {}
    for row in store.domain_occurrences_snapshot(snapshot_id):
        occ_display[int(row["occ_id"])] = _display(str(row["identity_key"]))
        occ_kind[int(row["occ_id"])] = str(row["kind"])
    sym_qname: dict[int, str] = {
        int(r["id"]): str(r["qualified_name"])
        for r in conn.execute(
            "SELECT so.id, s.qualified_name FROM symbol_occurrences so"
            " JOIN symbols s ON s.id = so.logical_symbol_id"
            " WHERE so.snapshot_id = ?",
            (snapshot_id,),
        )
    }
    kind_counts = {
        str(r["kind"]): int(r["c"])
        for r in conn.execute(
            "SELECT kind, COUNT(*) c FROM domain_entities GROUP BY kind"
        )
    }
    entities = sorted(
        _display(str(r["identity_key"]))
        for r in conn.execute(
            "SELECT identity_key FROM domain_entities ORDER BY identity_key"
        )
    )
    rels = store.domain_relations_snapshot(snapshot_id)

    def _frm(r: sqlite3.Row) -> str:
        fe = r["from_entity_occ_id"]
        if fe is not None:
            return occ_display.get(int(fe), f"?e{fe}")
        fs = r["from_symbol_occ_id"]
        return f"{SYM_PREFIX}{sym_qname.get(int(fs), fs)}"

    def _to(r: sqlite3.Row) -> str:
        te = r["to_entity_occ_id"]
        if te is not None:
            return occ_display.get(int(te), f"?e{te}")
        ts = r["to_symbol_occ_id"]
        if ts is not None:
            return f"{SYM_PREFIX}{sym_qname.get(int(ts), ts)}"
        return f"{TXT_PREFIX}{r['to_unresolved']}"

    edges = [
        {
            "type": str(r["type"]),
            "from": _frm(r),
            "to": _to(r),
            "status": str(r["resolution_status"]),
            "confidence": str(r["confidence_class"]),
            "resolver": str(r["resolver"]),
        }
        for r in rels
    ]
    edges.sort(
        key=lambda e: (e["type"], e["from"], e["to"], e["status"],
                       e["confidence"], e["resolver"])
    )
    return {
        "entities": entities,
        "by_kind": kind_counts,
        "occurrences": len(occ_display),
        "edges": edges,
        "by_type": dict(Counter(e["type"] for e in edges)),
        "by_status": dict(Counter(e["status"] for e in edges)),
        "by_confidence": dict(Counter(e["confidence"] for e in edges)),
        "by_resolver": dict(Counter(e["resolver"] for e in edges)),
    }


def run_domain_eval(
    repo_root: Path, spec: DomainSpec, work_dir: Path
) -> dict[str, object]:
    """Executa a pipeline real sobre a fixture e devolve o relatório.

    ``work_dir`` recebe o ``atlas.db`` temporário do cenário (um por
    domínio — isolamento de snapshot/repositório entre fixtures).
    """
    fixture = Path(repo_root) / spec.fixture
    scan = default_scan(fixture)
    files = {f.path: (f.sha256 or "") for f in scan.files}
    adapters: dict[str, DomainAdapter] = {
        aid: _ADAPTER_FACTORIES[aid](fixture) for aid in spec.adapter_ids
    }
    registry = DomainRegistry()
    for adapter in adapters.values():
        registry.register(adapter)

    conn = connect(Path(work_dir) / f"{spec.eval_id}.db")
    migrate(conn)
    try:
        coord = IndexCoordinator(
            conn,
            domain_registry=registry,
            # cross rodado manualmente abaixo — mesmas chamadas do
            # coordinator, mas capturando o plano (diagnósticos+stats).
            cross_resolver=None,
        )
        outcome = coord.index(fixture)
        if outcome.status != "success" or outcome.snapshot_id is None:
            return {
                "domain": spec.eval_id,
                "fixture": spec.fixture,
                "index_status": outcome.status,
                "error": outcome.error,
            }
        snapshot_id = outcome.snapshot_id
        store = Store(conn)

        cross_info: dict[str, object] = {}
        if spec.cross:
            # Réplica fiel de ``_run_cross_resolution``: mesmo resolver,
            # mesma ordem (plan → persist). Roda pós-publish numa
            # transação própria — o estado final é idêntico ao do
            # caminho inline; a diferença é só observável: o plano
            # (diagnósticos + stats) fica disponível para medir.
            resolver = CrossDomainResolver()
            with conn:
                xplan = resolver.plan(store, snapshot_id)
                applied = store.persist_cross_resolutions(
                    snapshot_id, xplan.update_rows(),
                    xplan.insert_rows(snapshot_id),
                )
            cross_info = {
                "stats": dict(xplan.stats),
                "applied": dict(applied),
                "diagnostics": [
                    {"severity": d.severity, "code": d.code,
                     "message": d.message}
                    for d in xplan.diagnostics
                ],
            }
        persisted = _read_persisted(conn, store, snapshot_id)

        # Mesmo request do coordinator (repository=ikey, snapshot=digest,
        # artifacts por supported_artifacts) — re-run só para medir os
        # diagnósticos, que não são persistidos por desenho.
        repository = identity_key(scan.git, fixture)
        digest = scan.manifest_sha256()
        extractions = _extract_for_diagnostics(
            adapters, files, repository, digest
        )
    finally:
        conn.close()

    diagnostics = [
        {
            "severity": d.severity,
            "code": d.code,
            "path": d.path,
            "adapter": d.adapter_id or domain_id,
        }
        for domain_id, res in extractions.items()
        for d in res.diagnostics
    ]
    diagnostics.extend(
        {
            "severity": str(d["severity"]),
            "code": str(d["code"]),
            "path": "",
            "adapter": "cross-domain",
        }
        for d in cross_info.get("diagnostics", ())  # type: ignore[union-attr]
    )
    diagnostics.sort(key=lambda d: (d["path"], d["code"], d["severity"]))
    artifacts = {
        domain_id: [a.path for a in _discover_artifacts(
            files, adapters[domain_id].supported_artifacts()
        )]
        for domain_id in adapters
    }
    stats: dict[str, int] = {}
    for domain_id, res in extractions.items():
        for k, v in res.stats.items():
            stats[f"{domain_id}.{k}"] = stats.get(f"{domain_id}.{k}", 0) + int(v)

    # contagens de domínio/cross do job de indexação — medido, não derivado
    job_counts = {
        k: int(v) for k, v in (outcome.stats or {}).items()
        if k.startswith(("domain_", "cross_"))
    }
    edges_skipped = sum(
        int(v) for k, v in (outcome.stats or {}).items()
        if k.startswith("domain_") and k.endswith("_edges_skipped")
    )

    return {
        "domain": spec.eval_id,
        "fixture": spec.fixture,
        "note": spec.note,
        "index_status": outcome.status,
        "adapters": sorted(adapters),
        "artifacts": artifacts,
        "entities": {
            "total": len(persisted["entities"]),
            "by_kind": persisted["by_kind"],
            "occurrences": persisted["occurrences"],
            "names": persisted["entities"],
        },
        "relations": {
            "total": len(persisted["edges"]),
            "by_type": persisted["by_type"],
            "by_status": persisted["by_status"],
            "by_confidence": persisted["by_confidence"],
            "by_resolver": persisted["by_resolver"],
            "edges_skipped": edges_skipped,
        },
        "diagnostics": diagnostics,
        "edges": persisted["edges"],
        "cross": cross_info,
        "job_counts": job_counts,
        "extract_stats": stats,
    }


# ---------------------------------------------------------------------
# goldens — comparação conjunto-a-conjunto
# ---------------------------------------------------------------------


def _edge_key(e: Mapping[str, object]) -> tuple[str, str, str, str, str, str]:
    return (
        str(e["type"]), str(e["from"]), str(e["to"]), str(e["status"]),
        str(e["confidence"]), str(e["resolver"]),
    )


def _fmt_edge(e: Mapping[str, object]) -> str:
    return (
        f"{e['type']} {e['from']} -> {e['to']} "
        f"[{e['status']}|{e['confidence']}|{e['resolver']}]"
    )


def _diag_key(d: Mapping[str, object]) -> tuple[str, str, str]:
    return (str(d["severity"]), str(d["code"]), str(d["path"]))


def _fmt_diag(d: Mapping[str, object]) -> str:
    return f"{d['severity']}:{d['code']}:{d['path'] or '-'}"


def diff_report(
    report: Mapping[str, object], golden: Mapping[str, object]
) -> list[str]:
    """Diff legível report↔golden; ``[]`` = cenário dentro do esperado.

    Checagens: contagens exatas (entidades/relations por kind/type/
    status), amostras de entidades esperadas/proibidas, inventário
    exaustivo de arestas (``missing``/``unexpected``) e diagnósticos.
    """
    diffs: list[str] = []

    def _count(actual: int, want: object, label: str) -> None:
        if want is None:
            return
        if actual != int(want):
            diffs.append(f"{label}: expected {want}, got {actual}")

    g_entities = golden.get("entities", {})
    if isinstance(g_entities, Mapping):
        r_entities = report["entities"]  # type: ignore[index]
        _count(
            int(r_entities["total"]), g_entities.get("total"),  # type: ignore[index]
            "entities.total",
        )
        want_kind = g_entities.get("by_kind")
        if want_kind is not None and dict(
            r_entities["by_kind"]  # type: ignore[index]
        ) != dict(want_kind):
            diffs.append(
                "entities.by_kind: expected "
                f"{dict(want_kind)}, got {dict(r_entities['by_kind'])}"  # type: ignore[index]
            )
        names = set(r_entities["names"])  # type: ignore[index]
        for want in g_entities.get("expected", ()):
            if want not in names:
                diffs.append(f"entity expected but absent: {want}")
        for bad in g_entities.get("forbidden", ()):
            if bad in names:
                diffs.append(f"entity forbidden but present: {bad}")
        # inventário exato de nomes — quando presente no golden,
        # rename/removal/addition vira diff explícito, não só delta
        # agregado de total/by_kind
        want_names = g_entities.get("names")
        if want_names is not None:
            for missing in sorted(set(want_names) - names):
                diffs.append(f"missing entity: {missing}")
            for extra in sorted(names - set(want_names)):
                diffs.append(f"unexpected entity: {extra}")

    g_relations = golden.get("relations", {})
    if isinstance(g_relations, Mapping):
        r_relations = report["relations"]  # type: ignore[index]
        _count(
            int(r_relations["total"]), g_relations.get("total"),  # type: ignore[index]
            "relations.total",
        )
        for key in ("by_type", "by_status", "by_confidence", "by_resolver"):
            want = g_relations.get(key)
            if want is not None and dict(
                r_relations[key]  # type: ignore[index]
            ) != dict(want):
                diffs.append(
                    f"relations.{key}: expected {dict(want)}, "
                    f"got {dict(r_relations[key])}"  # type: ignore[index]
                )
        _count(
            int(r_relations["edges_skipped"]),  # type: ignore[index]
            g_relations.get("edges_skipped"),
            "relations.edges_skipped",
        )

    # inventário exaustivo de arestas — matched/missing/unexpected
    want_edges = {_edge_key(e) for e in golden.get("edges", ())}
    got_edges = {_edge_key(e) for e in report["edges"]}  # type: ignore[index]
    by_key = {_edge_key(e): e for e in golden.get("edges", ())}
    by_key.update({_edge_key(e): e for e in report["edges"]})  # type: ignore[index]
    for key in sorted(want_edges - got_edges):
        diffs.append(f"missing edge: {_fmt_edge(by_key[key])}")
    for key in sorted(got_edges - want_edges):
        diffs.append(f"unexpected edge: {_fmt_edge(by_key[key])}")

    # diagnósticos como multiconjunto (severity, code, path)
    want_diags = Counter(
        _diag_key(d) for d in golden.get("diagnostics", ())
    )
    got_diags = Counter(
        _diag_key(d) for d in report["diagnostics"]  # type: ignore[index]
    )
    by_dkey: dict[tuple[str, str, str], Mapping[str, object]] = {
        _diag_key(d): d for d in golden.get("diagnostics", ())
    }
    by_dkey.update(
        {_diag_key(d): d for d in report["diagnostics"]}  # type: ignore[index]
    )
    for key in sorted(set(want_diags) | set(got_diags)):
        if want_diags[key] != got_diags[key]:
            diffs.append(
                f"diagnostic {_fmt_diag(by_dkey[key])}: expected "
                f"{want_diags[key]}x, got {got_diags[key]}x"
            )
    return diffs


def load_golden(golden_dir: Path, eval_id: str) -> dict[str, object] | None:
    path = Path(golden_dir) / f"{eval_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_all(
    repo_root: Path,
    golden_dir: Path | None = None,
    *,
    work_dir: Path | None = None,
) -> dict[str, object]:
    """Roda todos os ``DOMAIN_SPECS`` e compara com goldens presentes."""
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="domain-eval-")
        work_dir = Path(tmp.name)
    try:
        domains: dict[str, object] = {}
        ok = True
        for spec in DOMAIN_SPECS:
            report = run_domain_eval(Path(repo_root), spec, Path(work_dir))
            golden = (
                load_golden(Path(golden_dir), spec.eval_id)
                if golden_dir is not None
                else None
            )
            check: dict[str, object] = {"golden": golden is not None}
            if golden is not None:
                diffs = diff_report(report, golden)
                check["diffs"] = diffs
                check["pass"] = not diffs
                ok = ok and not diffs
            report["check"] = check
            domains[spec.eval_id] = report
        return {
            "schema": RESULTS_SCHEMA,
            "domains": domains,
            "summary": {
                "scenarios": len(domains),
                "golden_compared": sum(
                    1 for d in domains.values()
                    if isinstance(d, dict)
                    and isinstance(d.get("check"), dict)
                    and d["check"].get("golden")  # type: ignore[index]
                ),
                "all_pass": ok,
            },
        }
    finally:
        if tmp is not None:
            tmp.cleanup()


def report_to_golden(report: Mapping[str, object]) -> dict[str, object]:
    """Recorte persistível de ``report`` no schema de golden (bless).

    ``--bless`` é a ferramenta de curadoria: a saída DEVE ser revista
    linha a linha contra a fixture antes de commitar — golden gerado
    sem revisão é só um eco do comportamento corrente, não expectativa.
    """
    entities = report["entities"]  # type: ignore[index]
    relations = report["relations"]  # type: ignore[index]
    return {
        "schema": GOLDEN_SCHEMA,
        "domain": report["domain"],
        "fixture": report["fixture"],
        "entities": {
            "total": entities["total"],  # type: ignore[index]
            "by_kind": entities["by_kind"],  # type: ignore[index]
            "names": entities["names"],  # type: ignore[index]
            "expected": [],
            "forbidden": [],
        },
        "relations": {
            "total": relations["total"],  # type: ignore[index]
            "by_type": relations["by_type"],  # type: ignore[index]
            "by_status": relations["by_status"],  # type: ignore[index]
            "by_confidence": relations["by_confidence"],  # type: ignore[index]
            "by_resolver": relations["by_resolver"],  # type: ignore[index]
            "edges_skipped": relations["edges_skipped"],  # type: ignore[index]
        },
        "diagnostics": report["diagnostics"],
        "edges": report["edges"],
    }
