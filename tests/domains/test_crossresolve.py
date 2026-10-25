"""Resolução cross-domain (lf-0117, Cycle 7 Phase 9).

Fixture-driven: estados persistidos são montados via
``persist_domain_result`` com resultados de adapters sintéticos
(``domain_id`` é só string — spark/aws ainda não existem), depois
``CrossDomainResolver.plan`` + ``Store.persist_cross_resolutions``.

Regra sob teste: **nunca casar por nome nu** — todo vínculo exige
acordo de identidade qualificada (catálogo/namespace/env + nome,
conta+região+recurso para ARN, method+path literal para endpoints).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.contracts.enums import ResolutionStatus
from code_atlas.domains import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainEntityKind,
    DomainExtractionRequest,
    DomainExtractionResult,
    DomainIdentity,
    DomainRegistry,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.crossresolve import (
    CROSS_RESOLVER_ID,
    CrossDomainResolver,
)
from code_atlas.domains.evidence import evidence_record
from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.persist import persist_domain_result
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store

REPO_KEY = "git-remote:git@example/warehouse"


def _ident(domain: str, **kw: object) -> DomainIdentity:
    base: dict[str, object] = {
        "domain": domain,
        "repository": REPO_KEY,
        "qualified_name": "orders",
    }
    base.update(kw)
    return DomainIdentity(**base)  # type: ignore[arg-type]


def _entity(
    ident: DomainIdentity, kind: DomainEntityKind, name: str,
    path: str, attrs: dict[str, str] | None = None,
) -> ExtractedDomainEntity:
    return ExtractedDomainEntity(
        identity=ident, kind=kind, name=name, path=path,
        attributes=attrs or {}, evidence=(0,),
    )


def _result(
    domain_id: str,
    *,
    entities: tuple[ExtractedDomainEntity, ...] = (),
    relations: tuple[ExtractedDomainRelation, ...] = (),
    evidence_paths: tuple[tuple[str, str], ...] = (),
) -> DomainExtractionResult:
    """Resultado sintético; ``evidence_paths`` = (path, notes)."""
    return DomainExtractionResult(
        domain_id=domain_id,
        adapter_version="0.1.0",
        entities=entities,
        relations=relations,
        evidence=tuple(
            evidence_record(
                adapter_domain=domain_id, adapter_version="0.1.0",
                path=p, content_hash=f"sha256:{p}", notes=notes,
            )
            for p, notes in evidence_paths
        ),
    )


def _persist(
    store: Store,
    repo_id: int,
    snap: int,
    file_ids: dict[str, int],
    result: DomainExtractionResult,
) -> dict[str, int]:
    return persist_domain_result(store, repo_id, snap, file_ids, result)


def _rel_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, type, from_entity_occ_id, from_symbol_occ_id,"
        " to_entity_occ_id, to_symbol_occ_id, to_unresolved,"
        " resolution_status, confidence_class, resolver, evidence_ids"
        " FROM domain_relations ORDER BY id"
    ).fetchall()


def _occ_id(store: Store, repo_id: int, snap: int, ident: DomainIdentity) -> int:
    eid = store.domain_entity_id_for(repo_id, ident.logical_key())
    assert eid is not None
    occs = store.domain_occurrences_for(snap, eid)
    assert occs
    return occs[0]


@pytest.fixture()
def workspace(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, Store, int, int, dict[str, int]]:
    """Corpus indexado: ``(conn, store, repo_id, snapshot_id, file_ids)``.

    Arquivos: ``q.sql`` (inventário puro), ``mod.py`` (símbolo
    ``mod.target``), ``jobs/etl.py``, ``infra/main.tf`` e
    ``openapi/spec.yaml`` — todos viram símbolos FILE.
    """
    root = tmp_path / "corpus"
    (root / "jobs").mkdir(parents=True)
    (root / "infra").mkdir()
    (root / "openapi").mkdir()
    (root / "q.sql").write_text("SELECT 1\n")
    (root / "mod.py").write_text("def target():\n    return 1\n")
    (root / "jobs" / "etl.py").write_text("def run():\n    pass\n")
    (root / "infra" / "main.tf").write_text("# tf fixture\n")
    (root / "openapi" / "spec.yaml").write_text("openapi: 3.0.0\n")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    out = IndexCoordinator(conn).index(root)
    assert out.status == "success" and out.snapshot_id is not None
    store = Store(conn)
    snap = store.snapshot_by_id(out.snapshot_id)
    assert snap is not None
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?",
            (snap.repository_id,),
        )
    }
    return conn, store, snap.repository_id, out.snapshot_id, file_ids


# ---------------------------------------------------------------------
# M1 — re-resolução de rows unresolved
# ---------------------------------------------------------------------


def test_qualified_identity_resolves_cross_domain(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """SQL declara ``cat1.analytics.orders``; PySpark lê o nome
    qualificado → row ``READS_FROM`` vira resolved na entidade SQL."""
    conn, store, repo_id, snap, file_ids = workspace
    table = _ident(
        "sql", catalog="cat1", namespace="analytics",
        qualified_name="orders",
    )
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "sql",
        entities=(
            _entity(table, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="jobs/etl.py",
                from_identity=job, to_unresolved="cat1.analytics.orders",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    assert len(plan.updates) == 1
    upd = plan.updates[0]
    assert upd.resolution_status is ResolutionStatus.RESOLVED
    assert upd.to_entity_occ_id == _occ_id(store, repo_id, snap, table)
    counts = store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    assert counts["cross_updated"] == 1

    row = _rel_rows(conn)[0]
    assert row["resolution_status"] == "resolved"
    assert row["resolver"] == CROSS_RESOLVER_ID
    assert row["confidence_class"] == "resolved"
    assert row["to_entity_occ_id"] == _occ_id(store, repo_id, snap, table)
    assert row["to_unresolved"] is None
    # evidência dos DOIS lados: call-site pyspark + observação sql.
    ev = json.loads(row["evidence_ids"])
    extractors = {
        conn.execute(
            "SELECT extractor FROM evidence WHERE id = ?", (e,)
        ).fetchone()["extractor"]
        for e in ev
    }
    assert extractors == {"domain:sql", "domain:pyspark"}
    assert store.cross_snapshot_domain_relations(snap) == []


def test_same_name_different_catalog_is_ambiguous_not_resolved(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """``analytics.orders`` existe em DOIS catálogos (sql e glue) —
    referência parcial diverge de catálogo: ambiguous, candidatos
    preservados, NUNCA resolved."""
    conn, store, repo_id, snap, file_ids = workspace
    t_sql = _ident(
        "sql", catalog="catA", namespace="analytics",
        qualified_name="orders",
    )
    t_glue = _ident(
        "glue", catalog="catB", namespace="analytics",
        qualified_name="orders",
    )
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "sql",
        entities=(
            _entity(t_sql, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "glue",
        entities=(
            _entity(t_glue, DomainEntityKind.ICEBERG_TABLE, "orders",
                    "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="jobs/etl.py",
                from_identity=job, to_unresolved="analytics.orders",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    rows = _rel_rows(conn)
    rel = next(r for r in rows if r["type"] == "READS_FROM")
    assert rel["resolution_status"] == "ambiguous"
    assert rel["to_entity_occ_id"] is None
    # ambos os candidatos preservados no texto honesto.
    assert "catA" in rel["to_unresolved"]
    assert "catB" in rel["to_unresolved"]
    # nenhuma aresta resolved entre as duas tabelas — catálogos
    # divergentes são identidades distintas por construção.
    resolved_refs = [
        r for r in rows
        if r["type"] == "REFERENCES" and r["resolution_status"] == "resolved"
    ]
    assert resolved_refs == []


def test_terraform_provisions_spark_job_via_qualified_path(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """``aws_glue_job.etl`` (terraform, ``script_location=jobs/etl.py``)
    ↔ entidade do job observada em ``jobs/etl.py`` por outro adapter —
    vínculo por path qualificado, ``PROVISIONS`` resolved."""
    conn, store, repo_id, snap, file_ids = workspace
    glue_res = _ident(
        "terraform", qualified_name="aws_glue_job.etl",
    )
    job = _ident("pyspark", qualified_name="etl")
    _persist(store, repo_id, snap, file_ids, _result(
        "terraform",
        entities=(
            _entity(
                glue_res, DomainEntityKind.TERRAFORM_RESOURCE,
                "aws_glue_job.etl", "infra/main.tf",
                attrs={
                    "resource_type": "aws_glue_job",
                    "resource_name": "etl",
                    "resource_class": "glue",
                    "provider": "aws",
                    "script_location": "jobs/etl.py",
                },
            ),
        ),
        evidence_paths=(("infra/main.tf", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "etl", "jobs/etl.py"),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    rows = _rel_rows(conn)
    prov = [r for r in rows if r["type"] == "PROVISIONS"]
    assert len(prov) == 1
    row = prov[0]
    assert row["resolution_status"] == "resolved"
    assert row["resolver"] == CROSS_RESOLVER_ID
    assert row["from_entity_occ_id"] == _occ_id(store, repo_id, snap, glue_res)
    assert row["to_entity_occ_id"] == _occ_id(store, repo_id, snap, job)
    ev = json.loads(row["evidence_ids"])
    paths = {
        conn.execute(
            "SELECT path FROM evidence WHERE id = ?", (e,)
        ).fetchone()["path"]
        for e in ev
    }
    assert paths == {"infra/main.tf", "jobs/etl.py"}


def test_consumer_literal_resolves_to_contract_endpoint(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """``fetch("/v1/pets")`` (CALLS_ENDPOINT unresolved, method=GET)
    casa literal method+path com o ``APIEndpoint`` do contrato."""
    conn, store, repo_id, snap, file_ids = workspace
    ep = _ident(
        "api", qualified_name="GET /v1/pets", namespace="petstore",
    )
    sym_occ = store.symbol_occurrences_for_name(snap, "mod.target")
    assert len(sym_occ) == 1
    _persist(store, repo_id, snap, file_ids, _result(
        "api",
        entities=(
            _entity(
                ep, DomainEntityKind.API_ENDPOINT, "GET /v1/pets",
                "openapi/spec.yaml",
                attrs={"method": "GET", "full_path": "/v1/pets"},
            ),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="CALLS_ENDPOINT", path="mod.py",
                from_symbol="mod.target", to_unresolved="/v1/pets",
                evidence=(1,),
            ),
        ),
        evidence_paths=(
            ("openapi/spec.yaml", ""), ("mod.py", "method=GET"),
        ),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = next(r for r in _rel_rows(conn) if r["type"] == "CALLS_ENDPOINT")
    assert row["resolution_status"] == "resolved"
    assert row["resolver"] == CROSS_RESOLVER_ID
    assert row["from_symbol_occ_id"] == sym_occ[0]
    assert row["to_entity_occ_id"] == _occ_id(store, repo_id, snap, ep)


def test_consumer_literal_without_method_is_ambiguous(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """Path igual mas method não declarado + dois endpoints
    (GET e POST no mesmo path) → ambiguous com ambos os candidatos."""
    conn, store, repo_id, snap, file_ids = workspace
    ep_get = _ident(
        "api", qualified_name="GET /v1/pets", namespace="petstore",
    )
    ep_post = _ident(
        "api", qualified_name="POST /v1/pets", namespace="petstore",
    )
    _persist(store, repo_id, snap, file_ids, _result(
        "api",
        entities=(
            _entity(ep_get, DomainEntityKind.API_ENDPOINT, "GET /v1/pets",
                    "openapi/spec.yaml",
                    attrs={"method": "GET", "full_path": "/v1/pets"}),
            _entity(ep_post, DomainEntityKind.API_ENDPOINT,
                    "POST /v1/pets", "openapi/spec.yaml",
                    attrs={"method": "POST", "full_path": "/v1/pets"}),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="CALLS_ENDPOINT", path="mod.py",
                from_symbol="mod.target", to_unresolved="/v1/pets",
                evidence=(1,),
            ),
        ),
        evidence_paths=(("openapi/spec.yaml", ""), ("mod.py", "")),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = next(r for r in _rel_rows(conn) if r["type"] == "CALLS_ENDPOINT")
    assert row["resolution_status"] == "ambiguous"
    assert row["to_entity_occ_id"] is None
    assert "GET /v1/pets" in row["to_unresolved"]
    assert "POST /v1/pets" in row["to_unresolved"]


def test_absolute_url_stays_external(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """URL absoluta é lado só-externo — vira ``external``, nunca casa
    com endpoint local de mesmo path."""
    conn, store, repo_id, snap, file_ids = workspace
    ep = _ident("api", qualified_name="GET /v1/pets", namespace="petstore")
    _persist(store, repo_id, snap, file_ids, _result(
        "api",
        entities=(
            _entity(ep, DomainEntityKind.API_ENDPOINT, "GET /v1/pets",
                    "openapi/spec.yaml",
                    attrs={"method": "GET", "full_path": "/v1/pets"}),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="CALLS_ENDPOINT", path="mod.py",
                from_symbol="mod.target",
                to_unresolved="https://api.other.example/v1/pets",
                evidence=(1,),
            ),
        ),
        evidence_paths=(("openapi/spec.yaml", ""), ("mod.py", "")),
    ))
    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = next(r for r in _rel_rows(conn) if r["type"] == "CALLS_ENDPOINT")
    assert row["resolution_status"] == "external"
    assert row["to_entity_occ_id"] is None


def test_arn_resolves_by_account_region_resource(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """ARN ``…:glue:us-east-1:123:job/etl`` ↔ entidade cujo catálogo
    carrega conta+região e cujo nome é o recurso — resolved."""
    conn, store, repo_id, snap, file_ids = workspace
    job_ent = _ident(
        "awsdata", catalog="aws:123:us-east-1", qualified_name="etl",
    )
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "awsdata",
        entities=(
            _entity(job_ent, DomainEntityKind.GLUE_JOB, "etl", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="USES_RESOURCE", path="jobs/etl.py",
                from_identity=job,
                to_unresolved="arn:aws:glue:us-east-1:123:job/etl",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))
    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = next(r for r in _rel_rows(conn) if r["type"] == "USES_RESOURCE")
    assert row["resolution_status"] == "resolved"
    assert row["to_entity_occ_id"] == _occ_id(store, repo_id, snap, job_ent)


def test_name_only_collision_stays_unresolved_diagnostic(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """Nome nu sem qualificador → continua unresolved + diagnóstico;
    par de entidades homônimas sem qualificadores → diagnóstico, sem aresta."""
    conn, store, repo_id, snap, file_ids = workspace
    t_sql = _ident("sql", qualified_name="orders")
    t_glue = _ident("glue", qualified_name="orders")
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "sql",
        entities=(
            _entity(t_sql, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "glue",
        entities=(
            _entity(t_glue, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="jobs/etl.py",
                from_identity=job, to_unresolved="orders", evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))

    plan = CrossDomainResolver().plan(store, snap)
    counts = store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    assert counts == {"cross_updated": 0, "cross_inserted": 0}
    codes = {d.code for d in plan.diagnostics}
    assert "unqualified-target" in codes
    assert "name-only-collision" in codes
    rows = _rel_rows(conn)
    assert len(rows) == 1
    assert rows[0]["resolution_status"] == "unresolved"


def test_repo_path_target_resolves_to_file_symbol(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """Alvo textual que é path inventariado sem entidade de domínio
    naquele arquivo → ocorrência do símbolo FILE (ponte domínio→código)."""
    conn, store, repo_id, snap, file_ids = workspace
    job = _ident("cicd", qualified_name="build")
    _persist(store, repo_id, snap, file_ids, _result(
        "cicd",
        entities=(
            _entity(job, DomainEntityKind.CICD_JOB, "build",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="DEPLOYS", path="jobs/etl.py",
                from_identity=job, to_unresolved="q.sql", evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))
    file_occ = store.symbol_occurrences_for_name(snap, "q.sql")
    assert len(file_occ) == 1
    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = next(r for r in _rel_rows(conn) if r["type"] == "DEPLOYS")
    assert row["resolution_status"] == "resolved"
    assert row["to_symbol_occ_id"] == file_occ[0]


def test_same_domain_target_not_cross_resolved(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """Alvo qualificado no MESMO domínio do produtor → fora do escopo
    cross-domain (decisão do próprio adapter), fica unresolved."""
    conn, store, repo_id, snap, file_ids = workspace
    table = _ident(
        "sql", catalog="cat1", namespace="analytics",
        qualified_name="orders",
    )
    job = _ident("sql", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "sql",
        entities=(
            _entity(table, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
            _entity(job, DomainEntityKind.DATA_PIPELINE, "daily_load",
                    "q.sql"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="q.sql",
                from_identity=job, to_unresolved="cat1.analytics.orders",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    plan = CrossDomainResolver().plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    row = _rel_rows(conn)[0]
    assert row["resolution_status"] == "unresolved"


# ---------------------------------------------------------------------
# isolamento por snapshot
# ---------------------------------------------------------------------


def test_resolution_confined_to_one_snapshot(tmp_path: Path) -> None:
    """Entidade é lógica por repo mas ocorrência é por snapshot:
    num snapshot sem a ocorrência da entidade-alvo, o mesmo texto
    qualificado fica unresolved — nada vaza entre snapshots."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "q.sql").write_text("SELECT 1\n")
    (root / "job.py").write_text("def run():\n    pass\n")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    coord = IndexCoordinator(conn)
    out1 = coord.index(root)
    assert out1.status == "success" and out1.snapshot_id is not None
    store = Store(conn)
    snap1 = out1.snapshot_id
    repo_id = store.snapshot_by_id(snap1).repository_id  # type: ignore[union-attr]
    fids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute("SELECT id, relative_path FROM source_files")
    }
    table = _ident(
        "sql", catalog="cat1", namespace="analytics",
        qualified_name="orders",
    )
    _persist(store, repo_id, snap1, fids, _result(
        "sql",
        entities=(
            _entity(table, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap1, fids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "job.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="job.py",
                from_identity=job, to_unresolved="cat1.analytics.orders",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("job.py", ""),),
    ))
    plan1 = CrossDomainResolver().plan(store, snap1)
    assert len(plan1.updates) == 1
    store.persist_cross_resolutions(
        snap1, plan1.update_rows(), plan1.insert_rows(snap1)
    )
    conn.commit()

    # segundo snapshot — conteúdo novo; a entidade sql existe logicamente
    # mas NÃO tem ocorrência em snap2 (só o job + a relação chegam).
    (root / "extra.py").write_text("x = 1\n")
    out2 = coord.index(root)
    assert out2.status == "success" and out2.snapshot_id is not None
    snap2 = out2.snapshot_id
    assert snap2 != snap1
    _persist(store, repo_id, snap2, fids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "job.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="job.py",
                from_identity=job, to_unresolved="cat1.analytics.orders",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("job.py", ""),),
    ))
    plan2 = CrossDomainResolver().plan(store, snap2)
    assert plan2.updates == ()
    assert plan2.edges == ()
    row2 = conn.execute(
        "SELECT resolution_status FROM domain_relations"
        " WHERE snapshot_id = ?",
        (snap2,),
    ).fetchone()
    assert row2["resolution_status"] == "unresolved"
    # auditoria de isolamento limpa nos dois snapshots.
    assert store.cross_snapshot_domain_relations(snap1) == []
    assert store.cross_snapshot_domain_relations(snap2) == []


# ---------------------------------------------------------------------
# integração no IndexCoordinator (opt-in)
# ---------------------------------------------------------------------


class _StubAdapter:
    """Adapter mínimo do contrato — devolve resultado fixo do teste."""

    def __init__(
        self, domain_id: str, ext: str, result_kind: str
    ) -> None:
        self._domain = domain_id
        self._ext = ext
        self._kind = result_kind

    @property
    def domain_id(self) -> str:
        return self._domain

    @property
    def adapter_version(self) -> str:
        return "0.1.0"

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(extensions=frozenset({self._ext}))

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {
                DomainEntityKind.DATA_TABLE,
                DomainEntityKind.SPARK_JOB,
            }
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset({"READS_FROM"})

    def confidence_model(self) -> ConfidenceModel:
        return ConfidenceModel()

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return ()

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        repo = request.repository
        if self._kind == "sql":
            table = DomainIdentity(
                domain="sql", repository=repo, catalog="cat1",
                namespace="analytics", qualified_name="orders",
            )
            return _result(
                "sql",
                entities=(
                    _entity(table, DomainEntityKind.DATA_TABLE,
                            "orders", "a.sqx"),
                ),
                evidence_paths=(("a.sqx", ""),),
            )
        job = DomainIdentity(
            domain="pyspark", repository=repo,
            qualified_name="daily_load",
        )
        return _result(
            "pyspark",
            entities=(
                _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                        "b.pyx"),
            ),
            relations=(
                ExtractedDomainRelation(
                    relation_type="READS_FROM", path="b.pyx",
                    from_identity=job,
                    to_unresolved="cat1.analytics.orders",
                    evidence=(0,),
                ),
            ),
            evidence_paths=(("b.pyx", ""),),
        )


def _stub_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corp2"
    root.mkdir()
    (root / "a.sqx").write_text("x\n")
    (root / "b.pyx").write_text("y\n")
    return root


def test_coordinator_runs_cross_resolution_when_opted_in(
    tmp_path: Path,
) -> None:
    """Pipeline completo: dois adapters + ``cross_resolver`` injetado →
    a row sai resolved dentro da transação de publicação."""
    root = _stub_corpus(tmp_path)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    reg = DomainRegistry()
    reg.register(_StubAdapter("sql", ".sqx", "sql"))
    reg.register(_StubAdapter("pyspark", ".pyx", "pyspark"))
    out = IndexCoordinator(
        conn, domain_registry=reg,
        cross_resolver=CrossDomainResolver(),
    ).index(root)
    assert out.status == "success", out.error
    rows = conn.execute(
        "SELECT resolution_status, resolver, to_entity_occ_id"
        " FROM domain_relations WHERE type = 'READS_FROM'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["resolution_status"] == "resolved"
    assert rows[0]["resolver"] == CROSS_RESOLVER_ID
    assert rows[0]["to_entity_occ_id"] is not None
    assert out.stats["cross_resolved"] == 1


def test_coordinator_leaves_unresolved_without_opt_in(
    tmp_path: Path,
) -> None:
    """Default OFF: mesmo registry, sem ``cross_resolver`` → a row
    permanece unresolved exatamente como antes."""
    root = _stub_corpus(tmp_path)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    reg = DomainRegistry()
    reg.register(_StubAdapter("sql", ".sqx", "sql"))
    reg.register(_StubAdapter("pyspark", ".pyx", "pyspark"))
    out = IndexCoordinator(conn, domain_registry=reg).index(root)
    assert out.status == "success", out.error
    row = conn.execute(
        "SELECT resolution_status, resolver FROM domain_relations"
        " WHERE type = 'READS_FROM'"
    ).fetchone()
    assert row["resolution_status"] == "unresolved"
    assert row["resolver"] != CROSS_RESOLVER_ID


def test_plan_is_idempotent_on_persisted_state(
    workspace: tuple[sqlite3.Connection, Store, int, int, dict[str, int]],
) -> None:
    """Re-rodar ``plan`` sobre estado já resolvido converge para plano
    vazio — nenhum update repetido, nenhuma aresta duplicada."""
    _conn, store, repo_id, snap, file_ids = workspace
    table = _ident(
        "sql", catalog="cat1", namespace="analytics",
        qualified_name="orders",
    )
    job = _ident("pyspark", qualified_name="daily_load")
    _persist(store, repo_id, snap, file_ids, _result(
        "sql",
        entities=(
            _entity(table, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    _persist(store, repo_id, snap, file_ids, _result(
        "pyspark",
        entities=(
            _entity(job, DomainEntityKind.SPARK_JOB, "daily_load",
                    "jobs/etl.py"),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="jobs/etl.py",
                from_identity=job, to_unresolved="cat1.analytics.orders",
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))
    resolver = CrossDomainResolver()
    plan = resolver.plan(store, snap)
    store.persist_cross_resolutions(
        snap, plan.update_rows(), plan.insert_rows(snap)
    )
    again = resolver.plan(store, snap)
    assert again.updates == ()
    assert again.edges == ()
