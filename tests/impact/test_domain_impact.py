"""lf-0120 (Cycle 7 Phase 12) — Impact Engine atravessa ``domain_relations``.

Cenário: um mini-repo com um ``.sql`` (tabela ``staging.orders`` +
INSERT linhado) e dois jobs PySpark — um resolvido de ponta a ponta e
outro com uma fonte dinâmica não-resolvível (linhagem parcial honesta).
A seed ``staging.orders`` casa as entidades dos dois adapters
(``entity-seed-multi``) e o blast misto deve alcançar transformações,
tabelas derivadas e os ``spark_job``s que as contêm — com estados
honestos: hop ``ambiguous``/``unresolved`` nunca promove a cadeia.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.spark import PySparkAdapter
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.impact.contracts import ImpactRequest
from code_atlas.impact.engine import ImpactEngine
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

_SQL = """\
CREATE TABLE staging.orders (
    order_id    BIGINT,
    customer_id BIGINT,
    amount      DECIMAL(10,2),
    status      STRING
)
STORED AS PARQUET;

CREATE TABLE staging.customers (
    customer_id BIGINT,
    region      STRING
)
STORED AS PARQUET;

INSERT INTO staging.enriched
SELECT o.order_id, o.customer_id, c.region
FROM staging.orders o
JOIN staging.customers c ON c.customer_id = o.customer_id;
"""

_ETL = """\
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.getOrCreate()

orders = spark.read.table("staging.orders")
extra = spark.read.parquet("s3://lake/extra/")

enriched = (
    orders.join(extra, orders.customer_id == extra.id, "left")
    .filter(F.col("status") == "paid")
)
enriched.write.mode("overwrite").saveAsTable("mart.enriched_orders")
"""

_DYNAMIC = """\
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

orders = spark.read.table("staging.orders")
src = load_frame()
joined = orders.join(src, "id")
joined.write.parquet("s3://lake/out/")
"""


def _make_repo(root: Path) -> Path:
    (root / "staging").mkdir(parents=True)
    (root / "jobs").mkdir(parents=True)
    (root / "staging" / "01_staging.sql").write_text(_SQL)
    (root / "jobs" / "etl.py").write_text(_ETL)
    (root / "jobs" / "dynamic.py").write_text(_DYNAMIC)
    return root


def _index(conn: sqlite3.Connection, root: Path, *,
           with_domains: bool = True) -> int:
    reg = None
    if with_domains:
        reg = DomainRegistry()
        reg.register(SqlDomainAdapter(base_path=root))
        reg.register(PySparkAdapter(base_path=root))
    out = IndexCoordinator(conn, domain_registry=reg).index(root)
    assert out.status == "success" and out.snapshot_id is not None
    return out.snapshot_id


@pytest.fixture()
def indexed(tmp_path: Path):
    """Repo indexado com adapters sql+pyspark (snapshot único)."""
    root = _make_repo(tmp_path / "repo")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    snap = _index(conn, root)
    yield conn, root, snap
    conn.close()


def _ent_occ(conn: sqlite3.Connection, snapshot_id: int, domain: str,
             namespace: str, qname: str) -> int:
    row = conn.execute(
        "SELECT deo.id FROM domain_entity_occurrences deo"
        " JOIN domain_entities de ON de.id = deo.entity_id"
        " WHERE deo.snapshot_id = ? AND de.domain = ?"
        " AND de.namespace = ? AND de.qualified_name = ?",
        (snapshot_id, domain, namespace, qname)).fetchone()
    assert row is not None, f"{domain}:{namespace}.{qname} sem ocorrência"
    return int(row["id"])


def _add_ambiguous_consumer(
    conn: sqlite3.Connection, snapshot_id: int,
) -> None:
    """``audit.report DERIVES_FROM staging.orders`` com status
    ``ambiguous`` — o hop existe mas não está provado."""
    repo_id = conn.execute(
        "SELECT repository_id FROM snapshots WHERE id = ?",
        (snapshot_id,)).fetchone()["repository_id"]
    conn.execute(
        "INSERT INTO domain_entities"
        " (repository_id, domain, kind, identity_key, qualified_name,"
        "  namespace) VALUES"
        " (?, 'sql', 'data_table',"
        "  'dom:sql/test///audit//report', 'report', 'audit')",
        (repo_id,))
    eid = int(conn.execute(
        "SELECT max(id) AS i FROM domain_entities").fetchone()["i"])
    conn.execute(
        "INSERT INTO domain_entity_occurrences (snapshot_id, entity_id)"
        " VALUES (?, ?)", (snapshot_id, eid))
    occ = int(conn.execute(
        "SELECT max(id) AS i FROM domain_entity_occurrences"
    ).fetchone()["i"])
    orders = _ent_occ(conn, snapshot_id, "sql", "staging", "orders")
    conn.execute(
        "INSERT INTO domain_relations"
        " (snapshot_id, from_entity_occ_id, to_entity_occ_id, type,"
        "  resolution_status, confidence_class, resolver)"
        " VALUES (?, ?, ?, 'DERIVES_FROM', 'ambiguous', 'syntactic',"
        "  'test-injected')", (snapshot_id, occ, orders))
    conn.commit()


def test_table_seed_impacts_downstream(indexed) -> None:
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders"))
    by_name = {i.qualified_name: i for i in res.impacts}
    # os dois adapters extraíram ``staging.orders`` — multiplicidade
    # honesta diagnosticada, ambos viram seed
    assert any(d.code == "entity-seed-multi"
               for d in res.diagnostics)
    # blast misto: transformações, tabelas derivadas e os jobs
    assert "pyspark:mart.enriched_orders" in by_name
    assert "sql:staging.enriched" in by_name
    jobs = [i for i in res.impacts if i.kind == "spark_job"]
    assert {i.qualified_name for i in jobs} >= {
        "pyspark:jobs/etl", "pyspark:jobs/dynamic"}
    transforms = [
        i for i in res.impacts if i.kind == "data_transformation"]
    assert any("etl.py#orders" in i.qualified_name
               for i in transforms)
    # tudo é nó de domínio com proveniência tipada por hop
    for i in res.impacts:
        assert i.node_type == "domain"
        assert i.domain_occurrence_id is not None
        assert i.path is not None and i.path.hops >= 1
        assert i.path.edge_refs
        assert all(r.startswith("dom:") for r in i.path.edge_refs)
    # cadeias totalmente resolvidas são verified/direct|transitive
    direct = by_name["pyspark:mart.enriched_orders"]
    assert direct.strength.value == "verified"
    assert direct.impact.value in ("direct", "transitive")
    assert direct.path is not None
    assert set(direct.path.resolution_statuses) == {"resolved"}
    assert direct.path.fully_verified


def test_unresolved_dep_is_uncertainty_not_blast(indexed) -> None:
    """``joined = orders.join(src, …)``: ``src`` não resolve → a
    transformação é impactada (hops resolved) mas a linhagem parcial
    vira ``uncertain_deps`` + contador agregado — nunca blast."""
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders"))
    joined = [i for i in res.impacts
              if "dynamic.py#joined" in i.qualified_name]
    assert joined, "transformação joined ausente do blast"
    for i in joined:
        assert i.path is not None
        assert set(i.path.resolution_statuses) == {"resolved"}
        assert i.strength.value == "verified"
        assert any(d.startswith("DERIVES_FROM→src")
                   and "(unresolved)" in d
                   for d in i.uncertain_deps)
    # nenhum candidato fabricado para o alvo não-resolvido
    assert not any(i.qualified_name == "src" or i.symbol_key == "src"
                   for i in res.impacts)
    assert any(u.startswith("domain-unresolved-deps:")
               for u in res.unknown_coverage)


def test_ambiguous_hop_stays_uncertain(indexed) -> None:
    conn, root, snap = indexed
    _add_ambiguous_consumer(conn, snap)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders"))
    report = [i for i in res.impacts
              if i.qualified_name.endswith("audit.report")]
    assert report, "audit.report deveria aparecer como possível"
    for i in report:
        assert i.strength.value == "possible"
        assert i.impact.value == "possible"
        assert i.path is not None
        assert "ambiguous" in i.path.resolution_statuses
        assert not i.path.fully_verified


def test_disabled_adapters_flag_not_error(tmp_path: Path) -> None:
    """Sem ``domain_registry``: dados de domínio ausentes → flag
    honesta em ``unknown_coverage``, diagnóstico de seed — nunca erro."""
    root = _make_repo(tmp_path / "repo")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    _index(conn, root, with_domains=False)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders"))
    assert res.impacts == ()
    assert "domain-adapters-disabled" in res.unknown_coverage
    assert any(d.code == "entity-seed-not-found"
               for d in res.diagnostics)
    conn.close()


def test_symbol_param_accepts_entity_seed(indexed) -> None:
    """Fiação mínima: ``ImpactRequest.symbol`` casa refs de entidade —
    handlers existentes (API ``_impact``, CLI ``--ident``, MCP)
    aceitam seeds de domínio sem mudança de assinatura."""
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      symbol="staging.orders"))
    domain_impacts = [i for i in res.impacts
                      if i.node_type == "domain"]
    assert domain_impacts
    assert any(i.kind == "spark_job" for i in domain_impacts)
    # nenhum símbolo casa "staging.orders" → só nós de domínio
    assert not any(i.node_type == "symbol" for i in res.impacts)


def test_symbol_param_seeds_symbol_whatif(indexed) -> None:
    """Seed por chave/nome de símbolo: ``symbol`` que casa ocorrência
    de código vira origem do blast (análise what-if), produzindo nós
    ``symbol`` com proveniência ``rel:`` — misto com domínio quando
    a mesma ref também casa entidade."""
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root), symbol="jobs.etl"))
    sym_impacts = [i for i in res.impacts if i.node_type == "symbol"]
    assert sym_impacts, "seed de símbolo deveria alcançar o file pai"
    for i in sym_impacts:
        assert i.path is not None
        assert all(r.startswith("rel:") for r in i.path.edge_refs)


def test_write_coupling_reaches_writer(indexed) -> None:
    """``WRITES_TO`` é acoplado: seed numa tabela-destino alcança a
    transformação que a escreve (o writer depende do estado do
    alvo) — mas ``DERIVES_FROM`` continua direcional: as fontes da
    tabela derivada NÃO são impactadas por ela."""
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.enriched"))
    names = {i.qualified_name for i in res.impacts}
    assert "sql:staging/01_staging.sql#2" in names
    assert "sql:staging.orders" not in names
    assert "sql:staging.customers" not in names


def test_identity_key_and_unknown_seed(indexed) -> None:
    conn, root, _snap = indexed
    key = str(conn.execute(
        "SELECT identity_key FROM domain_entities"
        " WHERE domain = 'pyspark' AND namespace = 'staging'"
        " AND qualified_name = 'orders'").fetchone()["identity_key"])
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root), entity=key))
    assert any(i.node_type == "domain" for i in res.impacts)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="no.such.entity"))
    assert res.impacts == ()
    assert any(d.code == "entity-seed-not-found"
               for d in res.diagnostics)


def test_cross_snapshot_edge_never_traversed(
        tmp_path: Path) -> None:
    """Guarda de federação: endpoint de outro snapshot é pulado e
    contado — jamais atravessado (isolamento por snapshot)."""
    root = _make_repo(tmp_path / "repo")
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    snap1 = _index(conn, root)
    # segunda revisão: nova ocorrência para cada entidade
    (root / "jobs" / "etl.py").write_text(_ETL + "\n# touch\n")
    snap2 = _index(conn, root)
    assert snap1 != snap2
    # injeta aresta no snapshot 2 cujo consumidor vive no snapshot 1
    # — dado corrompido/adversarial: a leitura não pode federar.
    orders_s1 = _ent_occ(conn, snap1, "sql", "staging", "orders")
    orders_s2 = _ent_occ(conn, snap2, "sql", "staging", "orders")
    conn.execute(
        "INSERT INTO domain_relations"
        " (snapshot_id, from_entity_occ_id, to_entity_occ_id, type,"
        "  resolution_status, confidence_class, resolver)"
        " VALUES (?, ?, ?, 'DERIVES_FROM', 'resolved', 'syntactic',"
        "  'test-injected')", (snap2, orders_s1, orders_s2))
    conn.commit()
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders"))
    # a seed resolve a ocorrência do snapshot alvo (snap2); a aresta
    # corrompida é vista mas o endpoint de snap1 nunca atravessa —
    # staging.orders de snap1 não vira candidato de si mesmo.
    assert not any(d.code in ("cross-snapshot-traversed",)
                   for d in res.diagnostics)
    assert any(u.startswith("domain-cross-snapshot:")
               for u in res.unknown_coverage)
    # blast do snapshot alvo segue íntegro
    assert any(i.kind == "spark_job" for i in res.impacts)
    conn.close()


def test_entity_and_symbol_seeds_combine(indexed) -> None:
    """``entity`` + ``symbol`` juntos: ambas as refs viram seeds —
    consumidores das duas tabelas entram no mesmo blast."""
    conn, root, _snap = indexed
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root),
                      entity="staging.orders",
                      symbol="staging.customers"))
    assert not any(d.code == "entity-seed-not-found"
                   for d in res.diagnostics)
    # sql:staging.enriched lê staging.orders E staging.customers —
    # alcançada pelas duas origens; spark_job só vem da seed orders.
    names = {i.qualified_name for i in res.impacts}
    assert "sql:staging.enriched" in names
    assert any(i.kind == "spark_job" for i in res.impacts)
    origins = {i.path.origin_symbol_key for i in res.impacts
               if i.path is not None}
    assert any("staging" in o and o.endswith("orders")
               for o in origins)
    assert any("staging" in o and o.endswith("customers")
               for o in origins)
