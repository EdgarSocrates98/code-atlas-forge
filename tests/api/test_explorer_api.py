"""Contrato da API Explorer — servidor HTTP real, porta efêmera, read-only."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from code_atlas.api.server import serve

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "pyapp"


def _index_db(tmp: Path) -> tuple[Path, Path]:
    root = tmp / "repo"
    root.mkdir()
    (root / "mod.py").write_text(
        "def helper(x):\n    return x + 1\n\n"
        "class Service:\n    def run(self):\n        return helper(1)\n")
    (root / "caller.py").write_text(
        "from mod import Service\n\ndef main():\n    return Service().run()\n")
    db = tmp / "atlas.db"
    from code_atlas.indexing.coordinator import IndexCoordinator
    from code_atlas.storage import connect, migrate
    conn = connect(db)
    migrate(conn)
    IndexCoordinator(conn).index(root)
    conn.close()
    return db, root


def _req(url: str, path: str, method: str = "GET",
         body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestExplorerApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        db, cls.root = _index_db(Path(cls._tmp.name))
        cls.server = serve(str(db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def test_health(self) -> None:
        code, body = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")

    def test_capabilities(self) -> None:
        code, body = _req(self.server.url, "/capabilities")
        self.assertEqual(code, 200)
        self.assertTrue(body["readonly"])

    def test_repositories_and_snapshots(self) -> None:
        code, body = _req(self.server.url, "/repositories")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(body["total"], 1)
        rid = body["items"][0]["id"]
        code, snaps = _req(self.server.url, f"/repositories/{rid}/snapshots")
        self.assertEqual(code, 200)
        self.assertTrue(any(s["status"] == "READY" for s in snaps["items"]))

    def test_graph_overview_bounded(self) -> None:
        code, body = _req(self.server.url, "/graph/overview")
        self.assertEqual(code, 200)
        self.assertIn("snapshot_id", body)
        self.assertTrue(any("mod.py" in i["path"] for i in body["items"]))

    def test_search_hybrid(self) -> None:
        code, body = _req(self.server.url, "/search?q=helper&strategy=hybrid")
        self.assertEqual(code, 200)
        self.assertIn("items", body)

    def test_neighbors_of_symbol(self) -> None:
        _, hits = _req(self.server.url, "/search?q=Service&strategy=hybrid")
        occ = [i for i in hits.get("items", []) if i.get("occurrence_id")]
        if not occ:
            self.skipTest("sem hits")
        code, body = _req(
            self.server.url,
            f"/graph/neighbors?occurrence_id={occ[0]['occurrence_id']}"
            "&direction=out")
        self.assertEqual(code, 200)
        self.assertIn("edges", body)

    def test_impact_analyze(self) -> None:
        code, body = _req(self.server.url, "/impact/analyze", "POST",
                          {"repository_root": str(self.root)})
        self.assertEqual(code, 200)
        self.assertIn("diagnostics", body)

    def test_context_pack(self) -> None:
        code, body = _req(self.server.url, "/context", "POST",
                          {"task": "entender Service"})
        self.assertEqual(code, 200)
        self.assertIn("manifest", body)
        self.assertIn("items", body)

    def test_index_status(self) -> None:
        code, body = _req(self.server.url, "/index/status")
        self.assertEqual(code, 200)
        self.assertTrue(body["items"][0]["counts"]["symbol_occurrences"] > 0)

    # --- segurança -------------------------------------------------------
    def test_bad_host_rejected(self) -> None:
        code, _ = _req(self.server.url, "/health",
                       headers={"Host": "evil.example.com"})
        self.assertEqual(code, 403)

    def test_bad_origin_rejected(self) -> None:
        code, _ = _req(self.server.url, "/health",
                       headers={"Origin": "http://evil.example.com"})
        self.assertEqual(code, 403)

    def test_unknown_route_404(self) -> None:
        code, body = _req(self.server.url, "/admin/shell")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "not-found")

    def test_oversized_body_413(self) -> None:
        big = "x" * (70 * 1024)
        r = urllib.request.Request(
            self.server.url + "/context", data=big.encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(r, timeout=10)
            self.fail("deveria rejeitar")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 413)

    def test_unsafe_bind_rejected(self) -> None:
        from code_atlas.api.server import ApiError, AtlasApiServer
        with self.assertRaises(ApiError):
            AtlasApiServer("x.db", host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
