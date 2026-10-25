"""Contrato — GET /files/{id}/occurrences e DTO completo de aresta."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from code_atlas.api.server import serve


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


def _seed_relation_with_evidence(db: Path) -> tuple[int, int]:
    """Semeia relação com evidence_ids — prova real do drill-down."""
    from code_atlas.storage import connect
    conn = connect(db)
    try:
        sid = conn.execute(
            "SELECT id FROM snapshots WHERE status = 'READY' "
            "ORDER BY id DESC LIMIT 1").fetchone()["id"]
        occs = [r["id"] for r in conn.execute(
            "SELECT id FROM symbol_occurrences WHERE snapshot_id = ? "
            "ORDER BY id LIMIT 2", (sid,)).fetchall()]
        fid = conn.execute(
            "SELECT id FROM source_files ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO evidence (snapshot_id, source_file_id, path, span, "
            "content_hash, extractor, extractor_version, notes) "
            "VALUES (?, ?, 'mod.py', '0-5', 'seeded-hash', 'test', '1', '')",
            (sid, fid))
        ev_id = cur.lastrowid
        conn.execute(
            "INSERT INTO relations (snapshot_id, from_id, to_id, "
            "to_unresolved, type, resolution_status, confidence_class, "
            "resolver, evidence_ids) "
            "VALUES (?, ?, ?, NULL, 'CALLS', 'resolved', 'resolved', "
            "'test-resolver', ?)",
            (sid, occs[0], occs[-1], json.dumps([ev_id])))
        conn.commit()
        return int(sid), int(occs[0])
    finally:
        conn.close()


def _req(url: str, path: str, method: str = "GET",
         body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestFileOccurrences(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        db, cls.root = _index_db(tmp)
        cls.snapshot_id, cls.seed_occ = _seed_relation_with_evidence(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            cls.file_id = conn.execute(
                "SELECT id FROM source_files WHERE relative_path = 'mod.py'"
            ).fetchone()["id"]
        finally:
            conn.close()
        cls.server = serve(str(db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    # --- /files/{id}/occurrences --------------------------------------
    def test_occurrences_happy(self) -> None:
        code, body = _req(self.server.url,
                          f"/files/{self.file_id}/occurrences")
        self.assertEqual(code, 200)
        self.assertEqual(body["file_id"], self.file_id)
        self.assertEqual(body["snapshot_id"], self.snapshot_id)
        self.assertEqual(body["path"], "mod.py")
        self.assertGreaterEqual(body["total"], 1)
        keys = {"occurrence_id", "qualified_name", "kind", "span_start",
                "span_end", "line", "col", "content_hash", "parse_status"}
        for item in body["items"]:
            self.assertTrue(keys <= set(item), item)
        ordered = [(i["line"] or -1, i["col"] or -1) for i in body["items"]]
        self.assertEqual(ordered, sorted(ordered))

    def test_occurrences_unknown_file_404(self) -> None:
        code, body = _req(self.server.url, "/files/999999/occurrences")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "file-not-found")

    def test_occurrences_pagination(self) -> None:
        code, first = _req(
            self.server.url,
            f"/files/{self.file_id}/occurrences?limit=1&offset=0")
        self.assertEqual(code, 200)
        self.assertEqual(first["limit"], 1)
        self.assertEqual(len(first["items"]), min(1, first["total"]))
        if first["total"] > 1:
            self.assertTrue(first["truncated"])
            _, second = _req(
                self.server.url,
                f"/files/{self.file_id}/occurrences?limit=1&offset=1")
            self.assertNotEqual(first["items"][0]["occurrence_id"],
                                second["items"][0]["occurrence_id"])

    def test_occurrences_limit_capped_500(self) -> None:
        code, body = _req(
            self.server.url,
            f"/files/{self.file_id}/occurrences?limit=10000")
        self.assertEqual(code, 200)
        self.assertEqual(body["limit"], 500)

    def test_occurrences_explicit_snapshot(self) -> None:
        code, body = _req(
            self.server.url,
            f"/files/{self.file_id}/occurrences"
            f"?snapshot_id={self.snapshot_id}")
        self.assertEqual(code, 200)
        self.assertEqual(body["snapshot_id"], self.snapshot_id)

    def test_occurrences_unknown_snapshot_404(self) -> None:
        code, body = _req(
            self.server.url,
            f"/files/{self.file_id}/occurrences?snapshot_id=999999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "snapshot-not-found")

    def test_occurrences_empty_file(self) -> None:
        from code_atlas.storage import connect
        db = Path(self._tmp.name) / "atlas.db"
        conn = connect(db, read_only=True)
        try:
            row = conn.execute(
                "SELECT f.id FROM source_files f WHERE f.id NOT IN ("
                "SELECT DISTINCT source_file_id FROM symbol_occurrences "
                "WHERE snapshot_id = ?) LIMIT 1",
                (self.snapshot_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            self.skipTest("todo arquivo tem ocorrências")
        code, body = _req(self.server.url, f"/files/{row['id']}/occurrences")
        self.assertEqual(code, 200)
        self.assertEqual(body["items"], [])
        self.assertEqual(body["total"], 0)

    # --- DTO completo de aresta em /graph/neighbors --------------------
    def test_neighbors_edge_dto_complete(self) -> None:
        code, body = _req(
            self.server.url,
            f"/graph/neighbors?occurrence_id={self.seed_occ}&direction=out")
        self.assertEqual(code, 200)
        self.assertTrue(body["edges"])
        required = {"id", "relation_id", "snapshot_id", "from_id", "to_id",
                    "type", "resolution_status", "confidence_class",
                    "resolver", "evidence_ids"}
        for edge in body["edges"]:
            self.assertTrue(required <= set(edge), edge)
            self.assertEqual(edge["relation_id"], edge["id"])
            self.assertEqual(edge["snapshot_id"], body["snapshot_id"])
            self.assertIsInstance(edge["evidence_ids"], list)
            self.assertTrue(edge["confidence_class"])

    def test_neighbors_evidence_ids_present(self) -> None:
        code, body = _req(
            self.server.url,
            f"/graph/neighbors?occurrence_id={self.seed_occ}&direction=out")
        self.assertEqual(code, 200)
        seeded = [e for e in body["edges"]
                  if e["resolver"] == "test-resolver"]
        self.assertTrue(seeded, "relação semeada ausente")
        self.assertTrue(seeded[0]["evidence_ids"])
        ev = seeded[0]["evidence_ids"][0]
        code, evb = _req(self.server.url, f"/evidence/{ev}")
        self.assertEqual(code, 200)
        self.assertEqual(evb["evidence"]["content_hash"], "seeded-hash")


if __name__ == "__main__":
    unittest.main()
