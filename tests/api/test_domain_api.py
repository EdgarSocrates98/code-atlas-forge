"""lf-0122 Phase 14 — rotas GET read-only de domínio.

Cobre /domains, /domains/entities (+filtros/paginação), ego
/domains/entities/{id}/neighbors, /domains/relations por tipo e a
travessia bounded /domains/lineage — incluindo isolamento de snapshot,
erros tipados e a recusa honesta de mutação (POST → 404).
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from code_atlas.api.server import serve
from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.cicd import GitHubActionsAdapter
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

OPENAPI_SPEC = """\
openapi: 3.0.0
info:
  title: Orders API
  version: 1.0.0
servers:
  - url: https://api.orders.example/v1
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        '200':
          description: all orders
"""

WORKFLOW_YML = """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make build
  deploy:
    needs: build
    environment: prod
    steps:
      - run: make deploy
"""

MAIN_TF = """\
module "net" {
  source = "./net"
}

resource "aws_s3_bucket" "data" {
  bucket = "data-bucket"
}

resource "aws_iam_role" "etl" {
  name = "etl-role"
  assume_role_policy = "{}"
}
"""


def _index(tmp: Path) -> tuple[Path, int]:
    root = tmp / "repo"
    (root / "q.sql").parent.mkdir(parents=True, exist_ok=True)
    (root / "q.sql").write_text(
        "CREATE VIEW mart.v_orders AS SELECT * FROM mart.orders;\n")
    (root / "spec").mkdir(exist_ok=True)
    (root / "spec" / "openapi.yaml").write_text(OPENAPI_SPEC)
    (root / "infra").mkdir(exist_ok=True)
    (root / "infra" / "main.tf").write_text(MAIN_TF)
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "ci.yml").write_text(WORKFLOW_YML)
    db = tmp / "atlas.db"
    conn = connect(db)
    migrate(conn)
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=root))
    reg.register(ApiContractAdapter(workspace_root=root))
    reg.register(TerraformAdapter(base_path=root))
    reg.register(GitHubActionsAdapter.for_workspace(root))
    out = IndexCoordinator(conn, domain_registry=reg).index(root)
    assert out.status == "success" and out.snapshot_id is not None, out.error
    snap_id = out.snapshot_id
    conn.commit()
    conn.close()
    return db, snap_id


def _req(url: str, path: str, method: str = "GET",
         body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}


def _entity_id(url: str, kind: str, qname: str) -> int:
    code, body = _req(url, f"/domains/entities?kind={kind}&limit=200")
    assert code == 200, body
    for item in body["items"]:
        if item["qualified_name"] == qname or item["name"] == qname:
            return int(item["id"])
    raise AssertionError(f"{kind} {qname} not found in {body['items']}")


class TestDomainApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        db, cls.snap = _index(Path(cls._tmp.name))
        cls.server = serve(str(db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    # --- /domains ----------------------------------------------------
    def test_domains_inventory(self) -> None:
        code, body = _req(self.server.url, "/domains")
        self.assertEqual(code, 200, body)
        domains = {d["domain"]: d for d in body["items"]}
        for expected in ("sql", "api", "terraform", "cicd"):
            self.assertIn(expected, domains)
        sql_kinds = {k["kind"] for k in domains["sql"]["kinds"]}
        self.assertIn("data_table", sql_kinds)
        api_kinds = {k["kind"] for k in domains["api"]["kinds"]}
        self.assertEqual(
            {"api_contract", "api_service", "api_endpoint"} <= api_kinds,
            True)
        for dom in body["items"]:
            for k in dom["kinds"]:
                self.assertGreaterEqual(k["entities"], 1)
                self.assertGreaterEqual(k["occurrences"], 1)

    def test_domains_scoped_by_repository(self) -> None:
        code, body = _req(self.server.url, "/domains?repository_id=99999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "repo-not-found")

    # --- /domains/entities --------------------------------------------
    def test_entities_filter_by_domain_and_kind(self) -> None:
        code, body = _req(self.server.url, "/domains/entities?domain=sql")
        self.assertEqual(code, 200, body)
        self.assertTrue(body["items"])
        self.assertTrue(
            all(i["domain"] == "sql" for i in body["items"]))
        code, body = _req(
            self.server.url, "/domains/entities?kind=data_table")
        self.assertEqual(code, 200)
        self.assertTrue(
            all(i["kind"] == "data_table" for i in body["items"]))

    def test_entities_search_and_pagination(self) -> None:
        code, body = _req(self.server.url, "/domains/entities?q=v_orders")
        self.assertEqual(code, 200, body)
        self.assertTrue(
            any("v_orders" in i["qualified_name"] for i in body["items"]))
        code, page = _req(
            self.server.url, "/domains/entities?limit=1&offset=0")
        self.assertEqual(code, 200)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["truncated"])
        self.assertGreater(page["total"], 1)
        code, page2 = _req(
            self.server.url, "/domains/entities?limit=1&offset=1")
        self.assertNotEqual(
            page["items"][0]["id"], page2["items"][0]["id"])

    def test_entities_bad_filter_is_typed_error(self) -> None:
        code, body = _req(
            self.server.url, "/domains/entities?domain=sql%20OR%201=1")
        self.assertEqual(code, 400)
        self.assertEqual(body["error"]["code"], "bad-param")

    # --- /domains/entities/{id}/neighbors ------------------------------
    def test_entity_neighbors_ego(self) -> None:
        eid = _entity_id(self.server.url, "data_view", "v_orders")
        code, body = _req(
            self.server.url, f"/domains/entities/{eid}/neighbors")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["entity"]["qualified_name"], "v_orders")
        self.assertEqual(body["entity"]["namespace"], "mart")
        types = {e["type"] for e in body["edges"]}
        self.assertIn("DERIVES_FROM", types)
        edge = next(e for e in body["edges"] if e["type"] == "DERIVES_FROM")
        self.assertEqual(edge["to"]["qualified_name"], "orders")
        self.assertEqual(edge["resolution_status"], "resolved")
        self.assertTrue(edge["evidence_ids"])

    def test_entity_neighbors_404_and_scope(self) -> None:
        code, body = _req(
            self.server.url, "/domains/entities/999999/neighbors")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "domain-entity-not-found")
        eid = _entity_id(self.server.url, "data_view", "v_orders")
        code, body = _req(
            self.server.url,
            f"/domains/entities/{eid}/neighbors?repository_id=99999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "repo-not-found")

    # --- /domains/relations --------------------------------------------
    def test_relations_filtered_by_type(self) -> None:
        code, body = _req(
            self.server.url, "/domains/relations?type=DEPENDS_ON")
        self.assertEqual(code, 200, body)
        self.assertTrue(body["edges"])
        self.assertTrue(
            all(e["type"] == "DEPENDS_ON" for e in body["edges"]))
        dep = body["edges"][0]
        self.assertIn("ci.yml#ci/jobs/deploy", dep["from"]["qualified_name"])
        self.assertIn("ci.yml#ci/jobs/build", dep["to"]["qualified_name"])

    def test_relations_unresolved_targets_stay_textual(self) -> None:
        code, body = _req(
            self.server.url, "/domains/relations?type=TRIGGERS")
        self.assertEqual(code, 200, body)
        self.assertTrue(body["edges"])
        trig = body["edges"][0]
        self.assertEqual(trig["to"]["endpoint"], "unresolved")
        self.assertEqual(trig["resolution_status"], "external")
        self.assertIn("push", trig["to"]["label"])

    # --- /domains/lineage -----------------------------------------------
    def test_lineage_upstream_and_downstream(self) -> None:
        vid = _entity_id(self.server.url, "data_view", "v_orders")
        code, body = _req(
            self.server.url, f"/domains/lineage?table={vid}")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["root"]["qualified_name"], "v_orders")
        upstream = [n for n in body["nodes"] if n["lane"] == "upstream"]
        names = {n.get("qualified_name") for n in upstream}
        self.assertIn("orders", names)
        orders_id = _entity_id(self.server.url, "data_table", "orders")
        code, down = _req(
            self.server.url,
            f"/domains/lineage?table={orders_id}&direction=downstream")
        self.assertEqual(code, 200)
        down_names = {n.get("qualified_name") for n in down["nodes"]}
        self.assertIn("v_orders", down_names)

    def test_lineage_by_qualified_name_and_errors(self) -> None:
        code, body = _req(
            self.server.url,
            "/domains/lineage?table=" + urllib.parse.quote("v_orders"))
        self.assertEqual(code, 200, body)
        code, body = _req(self.server.url, "/domains/lineage?table=nope")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "lineage-target-not-found")
        code, body = _req(self.server.url, "/domains/lineage")
        self.assertEqual(code, 400)
        code, body = _req(
            self.server.url, "/domains/lineage?table=v_orders"
            "&direction=sideways")
        self.assertEqual(code, 400)

    def test_lineage_depth_zero_returns_root_only(self) -> None:
        vid = _entity_id(self.server.url, "data_view", "v_orders")
        code, body = _req(
            self.server.url, f"/domains/lineage?table={vid}&depth=0")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["nodes"], [])
        self.assertEqual(body["edges"], [])

    # --- superfície é somente leitura ------------------------------------
    def test_mutation_methods_are_rejected(self) -> None:
        for route in ("/domains", "/domains/entities",
                      "/domains/relations", "/domains/lineage"):
            code, _ = _req(self.server.url, route, method="POST",
                           body={"x": 1})
            self.assertEqual(code, 404, route)


if __name__ == "__main__":
    unittest.main()
