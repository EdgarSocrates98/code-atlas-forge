"""Contrato do ``GET /diagnostics`` + honestidade de erro (lf-0141).

Dois pilares do Cycle 8 Phase 15:

- ``/diagnostics`` é o painel do operador — versão, schema, contagens
  por repo, inventário de adapters, FTS, tamanho do índice, uptime —
  sempre read-only e barato (metadados persistidos + ``stat()``).
- todo erro da API é JSON estruturado ``{"error": {"code","message"}}``
  — nunca traceback nem corpo vazio.
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import code_atlas.api.handlers as handlers_mod
from code_atlas import __version__
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


def _empty_db(tmp: Path) -> Path:
    """Banco migrado sem nenhum repo — cobre o caso "nada indexado"."""
    db = tmp / "atlas.db"
    from code_atlas.storage import connect, migrate
    conn = connect(db)
    migrate(conn)
    conn.close()
    return db


def _req(url: str, path: str, method: str = "GET",
         body: dict | bytes | None = None) -> tuple[int, dict]:
    data: bytes | None = None
    if isinstance(body, dict):
        data = json.dumps(body).encode()
    elif isinstance(body, bytes):
        data = body
    r = urllib.request.Request(url + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class _ServerCase(unittest.TestCase):
    empty = False

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        if cls.empty:
            cls.db = _empty_db(tmp)
        else:
            cls.db, _ = _index_db(tmp)
        cls.server = serve(str(cls.db), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


class TestDiagnostics(_ServerCase):
    def test_shape_minima(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "code-atlas-api")
        self.assertEqual(body["api_version"], 1)
        self.assertEqual(body["version"], __version__)
        self.assertTrue(body["readonly"])

    def test_uptime_e_runtime(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["uptime_s"], (int, float))
        self.assertGreaterEqual(body["uptime_s"], 0.0)
        self.assertTrue(body["runtime"]["python"])
        self.assertTrue(body["runtime"]["sqlite"])

    def test_schema_e_database(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(body["schema_version"], 11)
        db = body["database"]
        self.assertEqual(Path(db["path"]).name, "atlas.db")
        self.assertGreater(db["size_bytes"], 0)
        self.assertGreaterEqual(db["wal_bytes"], 0)
        self.assertEqual(db["journal_mode"], "wal")
        self.assertGreater(db["page_count"], 0)

    def test_fts_e_search(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["fts"]["fts5_build"], bool)
        # build de CI tem FTS5 — as três tabelas/vtables existem
        self.assertTrue(body["fts"]["fts_occurrences"])
        self.assertIn("structural", body["search"]["strategies"])

    def test_repositorios_counts_e_job(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        repos = body["repositories"]
        self.assertEqual(len(repos), 1)
        repo = repos[0]
        self.assertEqual(repo["snapshots"]["ready"], 1)
        self.assertEqual(repo["latest_ready"], 1)
        self.assertGreater(repo["counts"]["file_inventory"], 0)
        self.assertGreater(repo["counts"]["symbol_occurrences"], 0)
        # analyzer_versions é a fonte autoritativa de "o que construiu"
        self.assertIn("scanner", repo["analyzer_versions"])
        self.assertTrue(
            any(k.startswith("parser:") for k in repo["analyzer_versions"]))
        job = repo["last_job"]
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "success")
        # stage_ms persistido vira campo inspecionável do diagnóstico
        self.assertIn("discovery", job["stage_ms"])
        self.assertIn("parse", job["stage_ms"])
        self.assertIn("persist_publish", job["stage_ms"])

    def test_inventario_adapters(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        dom = body["adapters"]["domain"]
        ids = {a["domain_id"] for a in dom}
        self.assertGreaterEqual(
            ids, {"sql", "pyspark", "aws-data", "terraform", "cicd", "api"})
        for a in dom:
            self.assertTrue(a["adapter_version"])
        self.assertEqual(body["adapters"]["error"], "")

    def test_observability_reflete_env(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertIsNone(body["observability"]["slow_ms"])

    def test_post_recusado(self) -> None:
        code, _ = _req(self.server.url, "/diagnostics",
                       method="POST", body={})
        self.assertEqual(code, 404)

    def test_barato(self) -> None:
        """O endpoint é metadados + stat — nunca um scan do workspace."""
        _req(self.server.url, "/diagnostics")  # aquece imports lazy
        t0 = time.monotonic()
        for _ in range(5):
            code, _ = _req(self.server.url, "/diagnostics")
            self.assertEqual(code, 200)
        per_call_ms = (time.monotonic() - t0) * 1000 / 5
        self.assertLess(per_call_ms, 2000)


class TestDiagnosticsEmptyDb(_ServerCase):
    """Banco migrado sem repo — resposta honesta, nunca erro 500."""
    empty = True

    def test_sem_repos(self) -> None:
        code, body = _req(self.server.url, "/diagnostics")
        self.assertEqual(code, 200)
        self.assertEqual(body["repositories"], [])
        self.assertGreater(body["schema_version"], 0)
        self.assertEqual(body["federation"]["workspaces"], 0)


class TestErrorHonesty(_ServerCase):
    """Todo erro é JSON estruturado — nunca traceback nem HTML."""

    def _assert_error(self, code: int, body: dict,
                      expected_code: str | None = None) -> None:
        self.assertIn("error", body)
        self.assertIsInstance(body["error"]["code"], str)
        self.assertIsInstance(body["error"]["message"], str)
        if expected_code is not None:
            self.assertEqual(body["error"]["code"], expected_code)
        raw = json.dumps(body)
        self.assertNotIn("Traceback", raw)
        self.assertNotIn('File "', raw)
        self.assertNotEqual(body["error"]["code"], "internal")
        self.assertLess(code, 500)

    def test_param_ausente_400(self) -> None:
        code, body = _req(self.server.url, "/search")
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-param")

    def test_param_malformado_400(self) -> None:
        code, body = _req(self.server.url, "/search?q=x&limit=abc")
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-param")

    def test_occurrence_malformado_400(self) -> None:
        code, body = _req(
            self.server.url, "/graph/neighbors?occurrence_id=abc")
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-param")

    def test_occurrence_ausente_400(self) -> None:
        code, body = _req(self.server.url, "/graph/neighbors")
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-param")

    def test_repo_inexistente_404(self) -> None:
        code, body = _req(
            self.server.url, "/graph/overview?repository_id=99999")
        self.assertEqual(code, 404)
        self._assert_error(code, body, "repo-not-found")

    def test_snapshot_inexistente_404(self) -> None:
        code, body = _req(
            self.server.url,
            "/graph/overview?repository_id=1&snapshot_id=99999")
        self.assertEqual(code, 404)
        self._assert_error(code, body, "snapshot-not-found")

    def test_symbol_id_malformado_404(self) -> None:
        code, body = _req(self.server.url, "/symbols/abc")
        self.assertEqual(code, 404)
        self._assert_error(code, body, "not-found")

    def test_json_malformado_400(self) -> None:
        code, body = _req(self.server.url, "/context", method="POST",
                          body=b"{not json")
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-json")

    def test_body_sem_task_400(self) -> None:
        code, body = _req(self.server.url, "/context", method="POST",
                          body={})
        self.assertEqual(code, 400)
        self._assert_error(code, body, "bad-param")

    def test_rota_inexistente_404(self) -> None:
        code, body = _req(self.server.url, "/no-such-route")
        self.assertEqual(code, 404)
        self._assert_error(code, body, "not-found")


class TestSlowRequestGuard(_ServerCase):
    """``CODE_ATLAS_SLOW_MS`` — requests acima do teto viram DEBUG."""

    def _capture_logger(self) -> tuple[logging.Logger, io.StringIO]:
        stream = io.StringIO()
        log = logging.getLogger("test.atlas.api.slow")
        log.handlers.clear()
        log.addHandler(logging.StreamHandler(stream))
        log.setLevel(logging.DEBUG)
        log.propagate = False
        return log, stream

    def test_slow_request_logada_em_debug(self) -> None:
        log, stream = self._capture_logger()
        env = {"CODE_ATLAS_SLOW_MS": "0.0001"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(handlers_mod, "_LOG", log):
            code, _ = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        out = stream.getvalue()
        self.assertIn("slow request", out)
        self.assertIn("GET /api/v1/health", out)
        self.assertIn("elapsed_ms=", out)

    def test_guard_desligado_sem_env(self) -> None:
        log, stream = self._capture_logger()
        with mock.patch.dict(os.environ, {"CODE_ATLAS_SLOW_MS": ""}), \
                mock.patch.object(handlers_mod, "_LOG", log):
            code, _ = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        self.assertEqual(stream.getvalue(), "")

    def test_abaixo_do_teto_nao_loga(self) -> None:
        log, stream = self._capture_logger()
        env = {"CODE_ATLAS_SLOW_MS": "60000"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(handlers_mod, "_LOG", log):
            code, _ = _req(self.server.url, "/health")
        self.assertEqual(code, 200)
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
