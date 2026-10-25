"""lf-0121 — domain context packs: seed entity, vizinhos, budget,
expansion refs resolvíveis, estados honestos.

Fixture espelha ``tests/domains/test_crossresolve.py``: corpus indexado
+ ``persist_domain_result`` de resultados sintéticos + plano do
``CrossDomainResolver`` aplicado — a aresta job→table fica ``resolved``
exatamente como em produção.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas.context import (
    DOMAIN_PACK_SCHEMA_VERSION,
    DomainContextEngine,
    DomainPackRequest,
    render_json,
    render_markdown,
    resolve_domain_ref,
)
from code_atlas.domains import (
    DomainEntityKind,
    DomainExtractionResult,
    DomainIdentity,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.crossresolve import CrossDomainResolver
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


@pytest.fixture()
def workspace(tmp_path: Path):
    """Corpus indexado com domínio semeado e cross-resolvido.

    Estado: ``cat1.analytics.orders`` (data_table, sql) é lida por
    ``daily_load`` (spark_job, pyspark) — aresta READS_FROM re-resolvida
    pelo CrossDomainResolver — mais um WRITE para alvo ``dynamic:`` e
    um alvo não-resolvido real. Retorna
    ``(conn, store, repo_id, snap, table_key, job_key, root)``.
    """
    root = tmp_path / "corpus"
    (root / "jobs").mkdir(parents=True)
    (root / "q.sql").write_text("SELECT 1\n")
    (root / "mod.py").write_text("def target():\n    return 1\n")
    (root / "jobs" / "etl.py").write_text("def run():\n    pass\n")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    out = IndexCoordinator(conn).index(root)
    assert out.status == "success" and out.snapshot_id is not None
    store = Store(conn)
    snap = store.snapshot_by_id(out.snapshot_id)
    assert snap is not None
    repo_id, sid = snap.repository_id, out.snapshot_id
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?",
            (repo_id,),
        )
    }
    table = _ident(
        "sql", catalog="cat1", namespace="analytics",
        qualified_name="orders",
    )
    job = _ident("pyspark", qualified_name="daily_load")
    persist_domain_result(store, repo_id, sid, file_ids, _result(
        "sql",
        entities=(
            _entity(table, DomainEntityKind.DATA_TABLE, "orders", "q.sql"),
        ),
        evidence_paths=(("q.sql", ""),),
    ))
    persist_domain_result(store, repo_id, sid, file_ids, _result(
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
            ExtractedDomainRelation(
                relation_type="WRITES_TO", path="jobs/etl.py",
                from_identity=job,
                to_unresolved="dynamic:env.OUTPUT_TABLE",
                evidence=(0,),
            ),
            ExtractedDomainRelation(
                relation_type="READS_FROM", path="jobs/etl.py",
                from_identity=job,
                to_unresolved="cat1.analytics.customers",
                evidence=(0,),
            ),
        ),
        evidence_paths=(("jobs/etl.py", ""),),
    ))
    plan = CrossDomainResolver().plan(store, sid)
    store.persist_cross_resolutions(
        sid, plan.update_rows(), plan.insert_rows(sid))
    conn.commit()
    yield conn, store, repo_id, sid, table.logical_key(), \
        job.logical_key(), str(root)
    conn.close()


@pytest.fixture()
def bare_repo(tmp_path: Path):
    """Repo indexado SEM dados de domínio — estado 'absent' honesto."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    return 1\n")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    IndexCoordinator(conn).index(root)
    yield conn, str(root)
    conn.close()


def _pack(ws, seed, *, budget=8000, max_neighbors=20):
    conn, _store, _repo, _sid, _tk, _jk, root = ws
    return DomainContextEngine(conn).build_domain(DomainPackRequest(
        seed=seed, repository_root=root, budget_tokens=budget,
        max_neighbors=max_neighbors))


# ---------------------------------------------------------------------


def test_pack_on_table_seed_card_and_job_neighbor(workspace) -> None:
    _conn, _store, repo_id, sid, table_key, job_key, _root = workspace
    p = _pack(workspace, table_key)
    d = p.domain
    assert d is not None and d.status == "ok"
    assert d.schema_version == DOMAIN_PACK_SCHEMA_VERSION
    assert p.manifest.schema_version == 2  # envelope aditivo, v2

    card = d.entity
    assert card is not None
    assert card.kind == "data_table"
    assert card.identity_key == table_key
    assert card.qualified_name == "orders"
    assert card.catalog == "cat1" and card.namespace == "analytics"
    assert card.snapshot_id == sid and card.repository_id == repo_id
    assert card.source_path == "q.sql"

    kinds = {i.kind for i in p.items}
    assert "domain-entity" in kinds
    nb_items = [i for i in p.items if i.kind == "domain-neighbor"]
    assert [i.item_id for i in nb_items] == [job_key]
    assert nb_items[0].expandable

    assert len(d.neighbors) == 1
    nb = d.neighbors[0]
    assert nb.ref == job_key
    assert nb.entity_kind == "spark_job"
    assert nb.direction == "incoming"          # job READS_FROM table
    assert nb.relation_types == ("READS_FROM",)
    assert nb.resolution_status == "resolved"
    assert nb.confidence_class == "resolved"
    assert nb.edge_count == 1

    # contagens agregam TODAS as arestas da seed, não só as entregues
    assert d.counts["incoming"] == 1 and d.counts["outgoing"] == 0
    assert d.counts["resolved"] == 1
    assert "read by 1 (daily_load)" in d.summary
    assert "/domains/entities" in d.requery


def test_job_seed_shows_dynamic_and_unresolved_honestly(workspace) -> None:
    _c, _s, _r, _sid, table_key, job_key, _root = workspace
    p = _pack(workspace, job_key)
    d = p.domain
    assert d is not None and d.status == "ok"
    # job → table (resolved), job → dynamic:, job → customers (unresolved)
    assert d.counts["outgoing"] == 3
    assert d.counts["resolved"] == 1
    assert d.counts["dynamic"] == 1
    assert d.counts["unresolved"] == 1
    assert "domain-unresolved:1" in p.diagnostics.codes
    assert "domain-dynamic:1" in p.diagnostics.codes
    assert "cat1.analytics.customers" in d.unresolved_targets
    # marcador dynamic NÃO aparece como pendência
    assert all("dynamic:" not in t for t in d.unresolved_targets)
    # a tabela é vizinha outgoing do job
    assert d.neighbors[0].ref == table_key
    assert d.neighbors[0].direction == "outgoing"
    assert "reads 1 (orders)" in d.summary
    assert "1 unresolved" in d.summary and "1 dynamic" in d.summary
    # pendências honestas sobem para unresolved_refs do envelope
    assert "cat1.analytics.customers" in p.unresolved_refs


def test_expansion_refs_resolve_via_resolver_fn(workspace) -> None:
    conn, _s, repo_id, sid, table_key, job_key, _root = workspace
    # budget mínimo: vizinho cai para expansion_refs
    p = _pack(workspace, table_key, budget=256)
    assert p.manifest.truncated or p.expansion_refs
    dom_refs = [r for r in p.expansion_refs
                if r.ref_id.startswith("dom:")]
    assert dom_refs, "vizinho omitido deve virar expansion ref"
    for r in dom_refs:
        card = resolve_domain_ref(conn, repo_id, sid, r.ref_id)
        assert card is not None, r.ref_id
        assert card.identity_key in (table_key, job_key)
        assert card.snapshot_id == sid


def test_beyond_topn_neighbors_become_expansion_refs(workspace) -> None:
    _c, _s, _r, _sid, _tk, job_key, _root = workspace
    p = _pack(workspace, job_key, max_neighbors=0)
    d = p.domain
    assert d is not None and d.status == "ok"
    assert d.neighbors == ()
    assert d.neighbors_total == 1
    assert d.neighbors_omitted == 1
    refs = {r.ref_id for r in p.expansion_refs}
    assert d.entity is not None
    assert d.entity.identity_key not in refs
    assert any(r.startswith("dom:") for r in refs)
    assert "domain-neighbors-capped:0/1" in p.diagnostics.codes


def test_budget_accounting_honest(workspace) -> None:
    _c, _s, _r, _sid, table_key, _jk, _root = workspace
    for budget in (64, 128, 256, 1024, 8000):
        p = _pack(workspace, table_key, budget=budget)
        spent = sum(i.est_tokens for i in p.items)
        assert spent <= p.budget.available_items, (budget, spent)
        assert p.manifest.tokens_payload > 0
        d = p.domain
        assert d is not None
        # seção sempre reflete os itens entregues
        delivered = {i.item_id for i in p.items}
        assert {n.item_id for n in d.neighbors} <= delivered
        assert d.neighbors_omitted == d.neighbors_total - len(d.neighbors)
        if p.diagnostics.insufficient_budget:
            assert "insufficient-budget" in p.diagnostics.codes
            assert p.diagnostics.suggested_minimum_budget > 0


def test_absent_domain_data_is_honest_empty(bare_repo) -> None:
    conn, root = bare_repo
    p = DomainContextEngine(conn).build_domain(DomainPackRequest(
        seed="orders", repository_root=root))
    d = p.domain
    assert d is not None
    assert d.status == "absent"
    assert d.entity is None
    assert d.neighbors == () and d.neighbors_total == 0
    assert d.counts["resolved"] == 0 and d.counts["unresolved"] == 0
    assert "domain-absent" in p.diagnostics.codes
    assert d.summary == ""
    # envelope ainda é um pack válido versionado
    assert p.manifest.schema_version == 2
    assert p.diagnostics.codes


def test_seed_not_found_and_ambiguous(workspace) -> None:
    _c, _s, _r, _sid, _tk, _jk, _root = workspace
    p = _pack(workspace, "nonexistent_entity")
    assert p.domain is not None
    assert p.domain.status == "seed-not-found"
    assert "domain-seed-not-found" in p.diagnostics.codes

    # segunda entidade com MESMO qualified_name em outro catálogo →
    # seed por nome qualificado vira ambíguo com candidatos.
    conn, store, repo_id, sid, _tk, _jk, _root = workspace
    t2 = _ident("sql", catalog="catB", namespace="analytics",
                qualified_name="orders")
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?", (repo_id,))}
    persist_domain_result(store, repo_id, sid, file_ids, _result(
        "sql",
        entities=(_entity(t2, DomainEntityKind.DATA_TABLE, "orders",
                          "q.sql"),),
        evidence_paths=(("q.sql", ""),),
    ))
    conn.commit()
    p2 = _pack(workspace, "orders")
    d2 = p2.domain
    assert d2 is not None and d2.status == "seed-ambiguous"
    assert len(d2.candidates) == 2
    assert "domain-seed-ambiguous" in p2.diagnostics.codes
    # mas a seed por identity_key segue exata — sem falso positivo
    p3 = _pack(workspace, _tk)
    assert p3.domain is not None and p3.domain.status == "ok"


def test_symbol_neighbor_via_symbol_endpoint(workspace) -> None:
    """``from_symbol_occ_id`` → vizinho ``sym:`` (ponte código→domínio)."""
    conn, store, repo_id, sid, _tk, _jk, _root = workspace
    ep = _ident("api", qualified_name="GET /v1/pets", namespace="petstore")
    sym_occ = store.symbol_occurrences_for_name(sid, "mod.target")
    assert sym_occ
    file_ids = {
        r["relative_path"]: int(r["id"])
        for r in conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?", (repo_id,))}
    persist_domain_result(store, repo_id, sid, file_ids, _result(
        "api",
        entities=(
            _entity(ep, DomainEntityKind.API_ENDPOINT, "GET /v1/pets",
                    "q.sql",
                    attrs={"method": "GET", "full_path": "/v1/pets"}),
        ),
        relations=(
            ExtractedDomainRelation(
                relation_type="CALLS_ENDPOINT", path="mod.py",
                from_symbol="mod.target", to_unresolved="/v1/pets",
                evidence=(1,),
            ),
        ),
        evidence_paths=(("q.sql", ""), ("mod.py", "method=GET")),
    ))
    plan = CrossDomainResolver().plan(store, sid)
    store.persist_cross_resolutions(
        sid, plan.update_rows(), plan.insert_rows(sid))
    conn.commit()

    p = _pack(workspace, ep.logical_key())
    d = p.domain
    assert d is not None and d.status == "ok"
    sym_nb = [n for n in d.neighbors if n.kind == "symbol"]
    assert len(sym_nb) == 1
    assert sym_nb[0].ref == "sym:mod.target"
    assert sym_nb[0].direction == "incoming"
    assert sym_nb[0].relation_types == ("CALLS_ENDPOINT",)
    assert "called by 1 (mod.target)" in d.summary


def test_deterministic_and_versioned_json(workspace) -> None:
    _c, _s, _r, _sid, table_key, _jk, _root = workspace
    p1 = _pack(workspace, table_key)
    j1 = render_json(p1)
    p2 = _pack(workspace, table_key)
    assert render_json(p2) == j1
    j = json.loads(j1)
    assert j["domain"]["schema_version"] == DOMAIN_PACK_SCHEMA_VERSION
    assert j["manifest"]["schema_version"] == 2
    assert j["domain"]["entity"]["identity_key"] == table_key
    md = render_markdown(p1)
    assert "## Domain — ok" in md


def test_resolve_domain_ref_forms(workspace) -> None:
    conn, _s, repo_id, sid, table_key, _jk, _root = workspace
    by_key = resolve_domain_ref(conn, repo_id, sid, table_key)
    assert by_key is not None and by_key.qualified_name == "orders"
    by_id = resolve_domain_ref(
        conn, repo_id, sid, f"entity:{by_key.entity_id}")
    assert by_id is not None and by_id.identity_key == table_key
    by_num = resolve_domain_ref(conn, repo_id, sid, str(by_key.entity_id))
    assert by_num is not None
    # ref de outro repo / malformado / entidade inexistente → None honesto
    assert resolve_domain_ref(conn, repo_id, sid, "dom:sql/x/") is None
    assert resolve_domain_ref(conn, repo_id + 99, sid, table_key) is None
    assert resolve_domain_ref(conn, repo_id, sid, "sym:mod.target") is None
