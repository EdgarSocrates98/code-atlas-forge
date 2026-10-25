"""Contrato de hardening — headers, teto de resposta, rate limit, timeout."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import code_atlas.api.server as server_mod
from code_atlas.api.server import serve


def _index_db(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    (root / "mod.py").write_text("def helper(x):\n    return x + 1\n")
    db = tmp / "atlas.db"
    from code_atlas.indexing.coordinator import IndexCoordinator
    from code_atlas.storage import connect, migrate
    conn = connect(db)
    migrate(conn)
    IndexCoordinator(conn).index(root)
    conn.close()
    return db


def _req(url: str, path: str) -> tuple[int, dict, dict]:
    r = urllib.request.Request(url + path)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


class _ServerCase(unittest.TestCase):
    rate_limit: int | None = None
    request_timeout: float | None = None
    with_static = False

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        db = _index_db(tmp)
        static = None
        if cls.with_static:
            static = tmp / "static"
            static.mkdir()
            (static / "index.html").write_text("<html>atlas</html>")
        cls.server = serve(
            str(db), port=0,
            rate_limit=cls.rate_limit or server_mod.RATE_LIMIT,
            request_timeout=(cls.request_timeout
                             or server_mod.REQUEST_TIMEOUT),
            static_root=static)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


class TestHardening(_ServerCase):
    def test_security_headers_on_ok(self) -> None:
        code, _, headers = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_security_headers_on_error(self) -> None:
        code, _, headers = _req(self.server.url, "/no-route")
        self.assertEqual(code, 404)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_response_cap_typed_error(self) -> None:
        with mock.patch.object(server_mod, "MAX_RESPONSE", 8):
            code, body, _ = _req(self.server.url, "/health")
        self.assertEqual(code, 500)
        self.assertEqual(body["error"]["code"], "response-too-large")

    def test_rate_limiter_unit_window(self) -> None:
        rl = server_mod._RateLimiter(2, window=60.0)
        self.assertEqual(rl.allow("c1"), (True, 0.0))
        self.assertEqual(rl.allow("c1"), (True, 0.0))
        ok, retry = rl.allow("c1")
        self.assertFalse(ok)
        self.assertGreaterEqual(retry, 1.0)
        # outro cliente não é afetado
        self.assertEqual(rl.allow("c2"), (True, 0.0))


class TestRateLimit(_ServerCase):
    rate_limit = 3
    with_static = True

    def test_429_after_threshold(self) -> None:
        codes = [
            _req(self.server.url, "/health")[0] for _ in range(3)]
        self.assertEqual(codes, [200, 200, 200])
        code, body, headers = _req(self.server.url, "/health")
        self.assertEqual(code, 429)
        self.assertEqual(body["error"]["code"], "rate-limited")
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)

    def test_static_exempt_from_rate_limit(self) -> None:
        for _ in range(4):  # estoura o teto de API
            _req(self.server.url, "/health")
        base = self.server.url.removesuffix("/api/v1")
        with urllib.request.urlopen(base + "/", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn(b"atlas", resp.read())


class TestRequestTimeout(_ServerCase):
    request_timeout = 0.5

    def test_idle_keepalive_reaped(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port,
                                          timeout=10)
        conn.request("GET", "/api/v1/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.read()
        time.sleep(1.0)  # acima do timeout da conexão
        with self.assertRaises((OSError, http.client.HTTPException)):
            conn.request("GET", "/api/v1/health")
            conn.getresponse().read()
        conn.close()
        # servidor continua íntegro para conexões novas
        code, body, _ = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")


if __name__ == "__main__":
    unittest.main()
