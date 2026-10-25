"""lf-0118 Phase 10 — rotas GET read-only de federação.

A API só lê: workspaces, views, lookups por chave e grafo federado.
Toda mutação (create/add/pin) é superfície CLI — POST/PUT/DELETE não
existe por desenho e deve responder 404/405, nunca 200.
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
from code_atlas.federation import Federation
from code_atlas.storage import connect, migrate
from federation.helpers import (
    REPO_A_FILES,
    REPO_B_FILES,
    index_repo,
    write_repo,
)


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


class TestFederationApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        db = tmp / "atlas.db"
        root_a = write_repo(tmp / "repo_a", dict(REPO_A_FILES))
        root_b = write_repo(tmp / "repo_b", dict(REPO_B_FILES))
        conn = connect(db)
        migrate(conn)
        cls.snap_a = index_repo(conn, root_a)
        cls.snap_b = index_repo(conn, root_b)
        cls.repo_a = conn.execute(
            "SELECT repository_id AS r FROM snapshots WHERE id = ?",
            (cls.snap_a,)).fetchone()["r"]
        cls.repo_b = conn.execute(
            "SELECT repository_id AS r FROM snapshots WHERE id = ?",
            (cls.snap_b,)).fetchone()["r"]
        fed = Federation(conn)
        cls.ws = fed.create_workspace("orders-fed")
        fed.add_member(cls.ws, cls.repo_a)
        fed.add_member(cls.ws, cls.repo_b)
        conn.commit()
        cls.orders_key = conn.execute(
            "SELECT identity_key FROM domain_entities"
            " WHERE namespace = 'mart' AND qualified_name = 'orders'"
            " LIMIT 1").fetchone()["identity_key"]
        conn.close()
        cls.server = serve(str(db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    # --- workspaces ------------------------------------------------
    def test_list_workspaces(self) -> None:
        code, body = _req(self.server.url, "/federation/workspaces")
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["name"], "orders-fed")

    def test_workspace_detail(self) -> None:
        code, body = _req(
            self.server.url, f"/federation/workspaces/{self.ws.id}")
        self.assertEqual(code, 200)
        self.assertEqual(body["name"], "orders-fed")
        repos = {m["repository_id"] for m in body["members"]}
        self.assertEqual(repos, {self.repo_a, self.repo_b})

    def test_workspace_404(self) -> None:
        code, body = _req(self.server.url, "/federation/workspaces/99999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "workspace-not-found")

    # --- view ------------------------------------------------------
    def test_view_resolves_snapshots(self) -> None:
        code, body = _req(
            self.server.url, f"/federation/workspaces/{self.ws.id}/view")
        self.assertEqual(code, 200)
        self.assertTrue(body["view_id"].startswith("fedview:"))
        self.assertEqual(
            {m["repository_id"]: m["snapshot_id"]
             for m in body["members"]},
            {self.repo_a: self.snap_a, self.repo_b: self.snap_b})
        self.assertTrue(all(
            m["resolution"] == "latest-ready" for m in body["members"]))

    def test_view_pin_override(self) -> None:
        pin = urllib.parse.quote(f"{self.repo_a}:{self.snap_a}")
        code, body = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/view?pin={pin}")
        self.assertEqual(code, 200)
        m_a = next(m for m in body["members"]
                   if m["repository_id"] == self.repo_a)
        self.assertEqual(m_a["resolution"], "pin-override")

    def test_view_bad_pin_param(self) -> None:
        code, _ = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/view?pin=garbage")
        self.assertEqual(code, 400)

    def test_view_pin_non_member_400(self) -> None:
        code, _ = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/view?pin=77777:1")
        self.assertEqual(code, 400)

    # --- entities --------------------------------------------------
    def test_entity_occurrences_across_repos(self) -> None:
        key = urllib.parse.quote(self.orders_key, safe="")
        code, body = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}"
            f"/entities/{key}/occurrences")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(body["total"], 2)
        self.assertEqual(
            set(body["repositories_with_entity"]),
            {self.repo_a, self.repo_b})
        self.assertTrue(all(
            i["namespace"] == "mart" for i in body["items"]))

    def test_entity_occurrences_bad_key_400(self) -> None:
        code, _ = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}"
            "/entities/not-a-key/occurrences")
        self.assertEqual(code, 400)

    # --- graph -----------------------------------------------------
    def test_graph_shared_edges(self) -> None:
        code, body = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/graph?depth=0")
        self.assertEqual(code, 200)
        shared = [e for e in body["edges"] if e["kind"] == "shared_entity"]
        self.assertTrue(shared)
        self.assertTrue(all(e["computed"] for e in shared))
        self.assertTrue(all(
            e["from_repository_id"] != e["to_repository_id"]
            for e in shared))
        self.assertFalse(body["truncated"])

    def test_graph_depth_capped(self) -> None:
        code, body = _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/graph?depth=99")
        self.assertEqual(code, 200)
        self.assertEqual(body["depth"], 8)

    # --- read-only por desenho --------------------------------------
    def test_mutations_have_no_route(self) -> None:
        for method in ("POST", "PUT", "DELETE"):
            code, _ = _req(self.server.url, "/federation/workspaces",
                           method=method, body={"name": "x"})
            self.assertIn(code, (404, 405, 501), method)
            code, _ = _req(
                self.server.url,
                f"/federation/workspaces/{self.ws.id}",
                method=method, body={})
            self.assertIn(code, (404, 405, 501), method)


if __name__ == "__main__":
    unittest.main()
