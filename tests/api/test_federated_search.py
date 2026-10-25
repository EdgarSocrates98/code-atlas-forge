"""lf-0119 Phase 11 — GET /federation/workspaces/{id}/search + /search?workspace=.

Busca federada read-only: mesma família de envelope da busca
single-repo + identidade da view. Items tagueados
``repository_id``+``snapshot_id``; homônimos cross-repo nunca
colapsam; ``pin=``/``snapshot_id``/``repository_id``/``domain``/
``kind``/``language`` ajustam escopo e facetas honestamente.
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


class TestFederatedSearchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        db = tmp / "atlas.db"
        files_a = dict(REPO_A_FILES)
        files_b = dict(REPO_B_FILES)
        # função homônima nos dois repos — nunca pode colapsar
        files_a["app/orders.py"] = (
            "def process_orders(items):\n    return list(items)\n")
        files_b["app/orders.py"] = (
            "def process_orders(rows):\n    return sorted(rows)\n")
        root_a = write_repo(tmp / "repo_a", files_a)
        root_b = write_repo(tmp / "repo_b", files_b)
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
        cls.root_a = root_a
        fed = Federation(conn)
        cls.ws = fed.create_workspace("orders-fed")
        fed.add_member(cls.ws, cls.repo_a)
        fed.add_member(cls.ws, cls.repo_b)
        conn.commit()
        conn.close()
        cls.server = serve(str(db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _ws_search(self, extra: str = "") -> tuple[int, dict]:
        return _req(
            self.server.url,
            f"/federation/workspaces/{self.ws.id}/search{extra}")

    # --- rota de federação ---------------------------------------------
    def test_workspace_search_contract_in_both_repos(self) -> None:
        code, body = self._ws_search("?q=orders")
        self.assertEqual(code, 200)
        self.assertTrue(body["view_id"].startswith("fedview:"))
        self.assertEqual(body["members_searched"], 2)
        orders = [
            i for i in body["domain_items"]
            if i["qualified_name"] == "orders"
            and i["namespace"] == "mart"]
        self.assertEqual(len(orders), 2)
        self.assertEqual(
            {i["repository_id"] for i in orders},
            {self.repo_a, self.repo_b})
        self.assertEqual(
            {i["snapshot_id"] for i in orders},
            {self.snap_a, self.snap_b})
        self.assertTrue(all(
            i["item_type"] == "domain_entity"
            for i in body["domain_items"]))

    def test_same_name_symbols_tagged_never_collapse(self) -> None:
        code, body = self._ws_search("?q=process_orders&strategy=hybrid")
        self.assertEqual(code, 200)
        hits = [i for i in body["items"]
                if i["qualified_name"].endswith("process_orders")]
        self.assertEqual(
            {h["repository_id"] for h in hits},
            {self.repo_a, self.repo_b})
        snap_map = {m["repository_id"]: m["snapshot_id"]
                    for m in body["members"]}
        for h in hits:
            self.assertEqual(h["snapshot_id"],
                             snap_map[h["repository_id"]])

    def test_repository_id_narrows_member_scope(self) -> None:
        code, body = self._ws_search(
            f"?q=orders&repository_id={self.repo_b}")
        self.assertEqual(code, 200)
        self.assertEqual(body["members_searched"], 1)
        self.assertEqual(body["repository_filter"], self.repo_b)
        self.assertEqual(
            {i["repository_id"] for i in body["domain_items"]},
            {self.repo_b})

    def test_domain_and_kind_filters(self) -> None:
        code, body = self._ws_search("?q=orders&domain=sql")
        self.assertEqual(code, 200)
        self.assertTrue(body["domain_items"])
        self.assertTrue(all(
            i["domain"] == "sql" for i in body["domain_items"]))
        code, body = self._ws_search("?q=orders&kind=data_table")
        self.assertEqual(code, 200)
        self.assertEqual(
            {i["qualified_name"] for i in body["domain_items"]},
            {"orders"})

    def test_language_filter_skips_domain_facet(self) -> None:
        code, body = self._ws_search("?q=orders&language=python")
        self.assertEqual(code, 200)
        self.assertEqual(body["domain_items"], [])
        codes = [d["code"] for d in body["diagnostics"]]
        self.assertIn("domain-facet-skipped", codes)

    def test_pin_override_changes_member_resolution(self) -> None:
        pin = urllib.parse.quote(f"{self.repo_a}:{self.snap_a}")
        code, body = self._ws_search(f"?q=orders&pin={pin}")
        self.assertEqual(code, 200)
        m_a = next(m for m in body["members"]
                   if m["repository_id"] == self.repo_a)
        self.assertEqual(m_a["resolution"], "pin-override")
        code, body = self._ws_search(
            f"?q=orders&snapshot_id={self.snap_a}")
        self.assertEqual(code, 200)
        m_a = next(m for m in body["members"]
                   if m["repository_id"] == self.repo_a)
        self.assertEqual(m_a["resolution"], "pin-override")

    def test_determinism_and_bounded_limit(self) -> None:
        code, a = self._ws_search("?q=orders")
        code, b = self._ws_search("?q=orders")
        key = lambda h: (h["repository_id"], h["occurrence_id"],  # noqa: E731
                         h["qualified_name"])
        self.assertEqual([key(h) for h in a["items"]],
                         [key(h) for h in b["items"]])
        code, body = self._ws_search("?q=orders&limit=1")
        self.assertEqual(code, 200)
        self.assertLessEqual(len(body["items"]), 1)
        self.assertLessEqual(len(body["domain_items"]), 1)

    def test_missing_q_400_and_workspace_404(self) -> None:
        code, _ = self._ws_search("")
        self.assertEqual(code, 400)
        code, body = _req(
            self.server.url, "/federation/workspaces/99999/search?q=x")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "workspace-not-found")

    # --- /search estendida ----------------------------------------------
    def test_plain_search_accepts_workspace_param(self) -> None:
        code, body = _req(
            self.server.url, f"/search?q=orders&workspace={self.ws.id}")
        self.assertEqual(code, 200)
        self.assertTrue(body["view_id"].startswith("fedview:"))
        self.assertEqual(body["members_searched"], 2)
        # por nome também resolve
        code, body = _req(
            self.server.url, "/search?q=orders&workspace=orders-fed")
        self.assertEqual(code, 200)
        self.assertEqual(body["workspace"], "orders-fed")

    def test_plain_search_domain_filter_and_facet(self) -> None:
        code, body = _req(
            self.server.url,
            f"/search?q=orders&repository_id={self.repo_a}&domain=sql")
        self.assertEqual(code, 200)
        self.assertTrue(body["domain_items"])
        self.assertTrue(all(
            i["domain"] == "sql" for i in body["domain_items"]))
        self.assertTrue(all(
            i["item_type"] == "domain_entity"
            for i in body["domain_items"]))
        # items de símbolo seguem no envelope original
        self.assertIn("items", body)
        self.assertIn("snapshot_id", body)

    def test_plain_search_repository_id_selects_repo(self) -> None:
        code, body = _req(
            self.server.url,
            f"/search?q=process_orders&repository_id={self.repo_b}")
        self.assertEqual(code, 200)
        self.assertTrue(all(
            h["repository_id"] == self.repo_b for h in body["items"]))
        code, body = _req(
            self.server.url, "/search?q=x&repository_id=99999")
        self.assertEqual(code, 404)

    def test_mutations_have_no_route(self) -> None:
        for method in ("POST", "PUT", "DELETE"):
            code, _ = _req(
                self.server.url,
                f"/federation/workspaces/{self.ws.id}/search?q=x",
                method=method, body={})
            self.assertIn(code, (404, 405, 501), method)


if __name__ == "__main__":
    unittest.main()
