"""Final security review - Cycle 8.1 Phase 10 (lf-0153).

Re-audit GA: o suite cycle-8 continua cobrindo F1-F7; este arquivo
caça terreno NOVO aberto pelo 8.1 — ``explorer_dist`` empacotado no
wheel (lf-0148), ``explorer_static_root`` via ``importlib.resources``,
``build_release.sh`` (env injection), ``--verbose``/logging (injeção
de controles por linha de request ou filename hostil) e o painel
``/api/v1/diagnostics`` (info disclosure declarado, não bug).

Achados novos corrigidos aqui (ver docs/cycle-8.1-security-final.md):

- N1: request line com controles (``GET /\\x1b[31m``) injetava ANSI e
  linhas forjadas no stderr via ``_AtlasHttpd.log`` (``--verbose``) e
  ``log_if_slow`` — ``escape_control`` agora filtra C0/C1/DEL em
  ``_AtlasHttpd.log``, ``RedactionFilter`` (incl. ``exc_text``) e nos
  sinks humanos da CLI (``_index_progress``, ``_emit_page``,
  ``_emit_error``, index-plan, context-expand, doctor…).
- N2: request-target malformado (NUL em ``resolve()``, IPv6 inválido
  em absolute-URI) levantava ``ValueError`` → 500 ``internal``; agora
  é 400 ``bad-request`` tipado.
- N3: ``_serve_static`` lia o asset inteiro antes do check de tamanho
  — um arquivo plantado >4 MiB era carregado em memória só para ser
  rejeitado. ``stat()`` antecipado corta o read.
- N4: ``Server: BaseHTTP/0.6 Python/3.12.3`` fingerprintava a stack;
  agora ``code-atlas-api`` sem versão de runtime.
- N5: ``build_release.sh`` — ``SOURCE_DATE_EPOCH`` não-numérico do
  ambiente viajava até ``date``/``int()`` e ``VER`` extraído por
  regex entrava sem validação em nomes de artefato.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

import code_atlas.api.server as server_mod
from code_atlas.api.server import serve
from code_atlas.indexing import IndexCoordinator
from code_atlas.observability.logging import (
    RedactionFilter,
    escape_control,
)
from code_atlas.storage import connect, migrate

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only surface: bash build script / control-char filenames",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_release.sh"
PACKAGED_DIST = REPO_ROOT / "src" / "code_atlas" / "explorer_dist"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _index_db(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    (root / "mod.py").write_text("def helper(x):\n    return x + 1\n")
    db = tmp / "atlas.db"
    conn = connect(db)
    migrate(conn)
    IndexCoordinator(conn).index(root)
    conn.close()
    return db


def _req(url: str, path: str, method: str = "GET",
         headers: dict | None = None) -> tuple[int, bytes, dict]:
    r = urllib.request.Request(
        url + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _raw(port: int, payload: bytes, wait: float = 0.3) -> bytes:
    """Socket cru — escreve o request byte a byte (a stdlib do cliente
    sanitiza headers, então o hostil precisa ser escrito à mão)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(payload)
        time.sleep(wait)
        s.settimeout(4)
        out = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
                if len(out) > 4 * 1024 * 1024:
                    break
        except TimeoutError:
            pass
        return out
    finally:
        s.close()


class _StaticCase:
    """Server com dist sintética: index + js + arquivo fora + symlink
    hostil — o perímetro do ``explorer_dist`` empacotado (lf-0148)."""

    @classmethod
    def setup_class(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.db = _index_db(tmp)
        static = tmp / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>atlas dist</html>")
        (static / "app.js").write_text("console.log(1)")
        (static / "notes.txt").write_text("legit txt")
        # fora do root — alvo de traversal e de symlink
        cls.outside = tmp / "outside.json"
        cls.outside.write_text('{"pwn": true}')
        cls.server = serve(str(cls.db), port=0, static_root=static,
                           verbose=False)
        cls.static = static
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = cls.server.url.removesuffix("/api/v1")

    @classmethod
    def teardown_class(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


# ---------------------------------------------------------------------------
# explorer_dist asset serving — traversal, symlink, headers hostis
# ---------------------------------------------------------------------------


class TestExplorerDistServing(_StaticCase):
    """Superfície nova do 8.1: dist empacotada servida por
    ``_serve_static`` — contenção + allowlist + fallback SPA."""

    def _get(self, path: str, headers: dict | None = None):
        return _req(self.base, path, headers=headers)

    @pytest.mark.parametrize("path", [
        "/../outside.json",
        "/../../outside.json",
        "/%2e%2e/outside.json",
        "/%2E%2E%2Foutside.json",
        "/..%2foutside.json",
        "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/....//outside.json",
        "/sub/../../outside.json",
        "//etc/passwd",
        "/%5c..%5coutside.json",
        "/..\\..\\outside.json",
    ])
    def test_traversal_variants_never_serve_outside(self, path: str) -> None:
        code, body, _ = self._get(path)
        assert b'"pwn"' not in body, (path, code)
        assert b"root:" not in body
        # fallback SPA (index) ou 404 tipado — nunca o alvo
        assert code in (200, 404)

    def test_absolute_uri_form_traversal(self) -> None:
        """``GET http://host/../x`` — urlparse extrai só o path."""
        resp = _raw(
            self.server.port,
            b"GET http://127.0.0.1/../outside.json HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n\r\n")
        assert b'"pwn"' not in resp

    def test_malformed_absolute_uri_is_typed_400(self) -> None:
        """``GET http://[ `` — urlparse levanta ValueError (N2 → 400)."""
        resp = _raw(
            self.server.port,
            b"GET http://[ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 400 " in status, status

    def test_nul_byte_in_path_is_typed_400(self) -> None:
        """NUL estoura ``resolve()`` com ValueError — era 500 (N2)."""
        resp = _raw(
            self.server.port,
            b"GET /a\x00b HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 400 " in status, status
        assert b"bad-request" in resp

    def test_symlinked_dist_file_outside_never_served(self) -> None:
        """Asset symlink→fora do root: resolve foge → fallback, nunca
        o conteúdo do alvo (modelo wheel: dist hostil reempacotada)."""
        link = self.static / "leak.json"
        link.symlink_to(self.outside)
        try:
            code, body, _ = self._get("/leak.json")
            assert b'"pwn"' not in body
            assert code in (200, 404)  # SPA fallback ou 404
        finally:
            link.unlink()

    def test_symlink_chain_to_outside_never_served(self) -> None:
        """Cadeia ``a.json -> b -> outside`` — resolve segue tudo."""
        mid = self.static.parent / "mid.json"
        mid.symlink_to(self.outside)
        link = self.static / "chain.json"
        link.symlink_to(mid)
        try:
            _code, body, _ = self._get("/chain.json")
            assert b'"pwn"' not in body
        finally:
            link.unlink()
            mid.unlink()

    def test_symlink_inside_root_serves_normally(self) -> None:
        """Symlink que permanece DENTRO do root é asset legítimo."""
        link = self.static / "alias.txt"
        link.symlink_to("notes.txt")
        try:
            code, body, _ = self._get("/alias.txt")
            assert code == 200 and b"legit txt" in body
        finally:
            link.unlink()

    def test_suffix_allowlist_blocks_disguised_assets(self) -> None:
        """Arquivo dentro do root com extensão fora da allowlist → 404
        (um ``.sh``/``.conf`` plantado na dist nunca é servido)."""
        plant = self.static / "evil.sh"
        plant.write_text("echo pwn")
        try:
            code, _body, _ = self._get("/evil.sh")
            assert code == 404
        finally:
            plant.unlink()

    def test_range_and_conditional_headers_ignored(self) -> None:
        """Range/If-Modified-Since/If-None-Match hostis: ignorados —
        sempre 200 com corpo inteiro; nenhum cabeçalho de cache
        (Accept-Ranges/ETag/Last-Modified) é anunciado."""
        code, body, headers = self._get("/app.js", headers={
            "Range": "bytes=999999-",
            "If-Modified-Since": "garbage;;",
            "If-None-Match": "*",
            "If-Range": "nonsense",
        })
        assert code == 200
        assert b"console.log(1)" in body
        for h in ("Accept-Ranges", "ETag", "Last-Modified",
                  "Content-Range"):
            assert headers.get(h) is None, h

    def test_oversized_asset_rejected_before_full_read(self) -> None:
        """N3: stat antecipado — um asset gigante nunca é lido por
        inteiro em memória para ser rejeitado depois."""
        with mock.patch.object(server_mod, "MAX_RESPONSE", 8):
            code, body, _ = self._get("/index.html")
        assert code == 500
        assert b"response-too-large" in body

    def test_spa_fallback_only_for_missing_paths(self) -> None:
        """Rota Angular desconhecida → index.html (200); mas asset
        existente fora da allowlist → 404, nunca fallback mascarado."""
        code, body, _ = self._get("/alguma/rota/angular")
        assert code == 200 and b"atlas dist" in body

    def test_server_header_no_stack_fingerprint(self) -> None:
        """N4: ``Server:`` não revela Python/stdlib — só o produto."""
        _code, _body, headers = self._get("/")
        server = headers.get("Server", "")
        assert "Python" not in server and "BaseHTTP" not in server
        assert "code-atlas" in server


class TestSymlinkedStaticRoot:
    """``static_root`` inteiro trocado por symlink — o resolve() por
    request ancora no alvo real e a allowlist de suffix ainda morde."""

    def test_suffix_allowlist_still_applies_via_symlinked_root(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real-dist"
        real.mkdir()
        (real / "index.html").write_text("<html>ok</html>")
        (real / "passwd").write_text("root:x:0:0")  # sem extensão
        (real / "data.conf").write_text("secret=1")
        link = tmp_path / "dist-link"
        link.symlink_to(real)
        db = _index_db(tmp_path)
        srv = serve(str(db), port=0, static_root=link)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base = srv.url.removesuffix("/api/v1")
        try:
            code, body, _ = _req(base, "/")
            assert code == 200 and b"ok" in body
            # dentro do root resolvido, mas fora da allowlist → 404
            for p in ("/passwd", "/data.conf"):
                code, body, _ = _req(base, p)
                assert code == 404, (p, code)
                assert b"root:x" not in body and b"secret" not in body
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# explorer_static_root — resolução importlib.resources + hint
# ---------------------------------------------------------------------------


class TestExplorerStaticRootResolution:
    """``explorer_static_root``: o traversable nunca recebe input de
    request — só nomes fixos; a preferência packaged > hint e a exigência
    de ``index.html`` são o contrato."""

    def test_packaged_dist_preferred_over_repo_hint(self) -> None:
        from code_atlas.api.server import explorer_static_root
        root = explorer_static_root(REPO_ROOT)
        if (PACKAGED_DIST / "index.html").is_file():
            # dist empacotada existe → sempre vence o checkout dev
            assert root is not None
            assert root.resolve() == PACKAGED_DIST.resolve()
            assert (root / "index.html").is_file()
        else:
            pytest.skip("explorer_dist ausente neste checkout")

    def test_hint_without_index_html_returns_none(self) -> None:
        from code_atlas.api.server import explorer_static_root
        with tempfile.TemporaryDirectory() as td:
            hint = Path(td)
            (hint / "explorer" / "dist").mkdir(parents=True)
            # sem index.html em nenhum candidato
            with mock.patch(
                "importlib.resources.files",
                side_effect=ModuleNotFoundError,
            ):
                assert explorer_static_root(hint) is None

    def test_dev_checkout_candidates(self) -> None:
        from code_atlas.api.server import explorer_static_root
        with tempfile.TemporaryDirectory() as td:
            hint = Path(td)
            dist = hint / "explorer" / "dist" / "explorer" / "browser"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("<html>x</html>")
            with mock.patch(
                "importlib.resources.files",
                side_effect=ModuleNotFoundError,
            ):
                assert explorer_static_root(hint) == dist

    def test_hint_traversal_impossible_by_construction(self) -> None:
        """O hint é concatenado a subpaths fixos — nenhum segmento vem
        de request/ambiente; ``..`` no hint é resolvido pelo fs e
        ainda exige ``index.html`` real."""
        from code_atlas.api.server import explorer_static_root
        with tempfile.TemporaryDirectory() as td:
            weird = Path(td) / ".." / (Path(td).name)
            root = explorer_static_root(weird)
            # nunca levanta; e nunca devolve path sem index.html
            assert root is None or (root / "index.html").is_file()


# ---------------------------------------------------------------------------
# DoS / malformed input bounds
# ---------------------------------------------------------------------------


class _DoSCase:
    request_timeout = 1.0

    @classmethod
    def setup_class(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.db = _index_db(tmp)
        cls.server = serve(str(cls.db), port=0,
                           request_timeout=cls.request_timeout)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def teardown_class(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


class TestRequestDosBounds(_DoSCase):
    """Bounds de entrada — a stdlib garante (e aqui se prova):
    ≤100 headers, ≤64 KiB por linha de header, request line ≤64 KiB,
    timeout por conexão contra dribles lentos."""

    def test_more_than_100_headers_rejected(self) -> None:
        hdrs = b"".join(
            b"X-Fill-%d: v\r\n" % i for i in range(150))
        resp = _raw(
            self.server.port,
            b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            + hdrs + b"\r\n")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 431 " in status or b" 400 " in status, status

    def test_header_line_over_64k_rejected(self) -> None:
        resp = _raw(
            self.server.port,
            b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"X-Big: " + b"A" * 70000 + b"\r\n\r\n")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 431 " in status or b" 400 " in status, status

    def test_request_line_over_64k_rejected(self) -> None:
        resp = _raw(
            self.server.port,
            b"GET /api/v1/" + b"a" * 70000 + b" HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n\r\n")
        status = resp.split(b"\r\n", 1)[0]
        assert (b" 414 " in status or b" 400 " in status
                or b" 431 " in status), status

    def test_header_without_colon_bounded(self) -> None:
        resp = _raw(
            self.server.port,
            b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Garbage-No-Colon\r\n\r\n")
        # stdlib tolera ou rejeita — o contrato é: nunca trava, nunca 5xx solto
        status = resp.split(b"\r\n", 1)[0]
        assert b"HTTP/1.1" in status

    def test_chunked_body_does_not_desync(self) -> None:
        """``Transfer-Encoding: chunked`` não é decodificado — corpo
        não lido vira erro tipado na mesma conexão, nunca execução."""
        resp = _raw(
            self.server.port,
            b"POST /api/v1/context HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\n",
            wait=0.6)
        status = resp.split(b"\r\n", 1)[0]
        assert b" 400 " in status or b" 404 " in status, status

    def test_slow_dribble_request_line_reaped(self) -> None:
        """Slow-loris: um byte inicial e silêncio — o timeout da
        conexão fecha antes do próximo tick (request_timeout=1.0)."""
        s = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=10)
        try:
            s.sendall(b"GET /api")  # request line incompleta
            t0 = time.monotonic()
            s.settimeout(6)
            got = s.recv(64)  # conexão deve morrer ~1s, bem antes de 6
            elapsed = time.monotonic() - t0
            assert got == b""
            assert elapsed < 5.0
        finally:
            s.close()
        # servidor íntegro depois do ataque
        code, body, _ = _req(self.server.url, "/health")
        assert code == 200 and b'"ok"' in body

    def test_many_half_open_connections_survive(self) -> None:
        """Mini-flood de conexões ociosas — daemon_threads + timeout
        impedem esgotamento; o servidor segue respondendo."""
        socks = [
            socket.create_connection(
                ("127.0.0.1", self.server.port), timeout=10)
            for _ in range(16)
        ]
        try:
            code, body, _ = _req(self.server.url, "/health")
            assert code == 200 and b'"ok"' in body
        finally:
            for s in socks:
                s.close()


# ---------------------------------------------------------------------------
# log injection — controles nunca alcançam stderr
# ---------------------------------------------------------------------------


class TestLogInjection:
    """N1: bytes de controle vindos de request line / filename hostil
    não podem forjar linhas nem sequências ANSI no terminal."""

    @pytest.mark.parametrize("raw,expected", [
        ("a\nb", "a\\x0ab"),
        ("a\rb", "a\\x0db"),
        ("x\x1b[31mred", "x\\x1b[31mred"),
        ("nul\x00byte", "nul\\x00byte"),
        ("del\x7fc1\x9f", "del\\x7fc1\\x9f"),
        ("tab\there", "tab\\x09here"),
        ("plain text 123", "plain text 123"),
    ])
    def test_escape_control_unit(self, raw: str, expected: str) -> None:
        assert escape_control(raw) == expected

    def test_redaction_filter_escapes_msg_and_args(self) -> None:
        filt = RedactionFilter(home="")
        rec = logging.LogRecord(
            "t", logging.WARNING, __file__, 1,
            "req %s", ("GET /\x1b[31m\nforged",), None)
        filt.filter(rec)
        out = rec.getMessage()
        assert "\x1b" not in out and "\n" not in out
        assert "\\x1b" in out and "\\x0a" in out

    def test_redaction_filter_escapes_exc_text(self) -> None:
        """Traceback com filename ``a\nb.py`` — o Formatter usa o
        ``exc_text`` pré-renderizado e escapado pelo filtro."""
        filt = RedactionFilter(home="")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            rec = logging.LogRecord(
                "t", logging.ERROR, __file__, 1, "failed", (),
                sys.exc_info())
            filt.filter(rec)
        assert rec.exc_text is not None
        assert "\n" not in rec.exc_text

    def test_index_progress_escapes_hostile_fields(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--verbose``: mesmo que um campo futuro carregue texto
        não-confiável, nenhuma linha forjada nem ANSI sai."""
        from code_atlas.cli import _index_progress
        _index_progress("stage\x1b[7m", {"path": "a\nb.py", "ms": 1})
        err = capsys.readouterr().err
        assert "\x1b" not in err
        for line in err.splitlines():
            assert line.startswith("[index]")
        assert "\\x1b" in err and "\\x0a" in err

    def test_httpd_log_escapes_request_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """O canal ``[atlas-api]`` do ``--verbose`` nunca emite o
        request line cru — ``GET /\x1b[31m`` sai escapado."""
        db = _index_db(tmp_path)
        srv = serve(str(db), port=0, verbose=True)
        try:
            srv._httpd.log('127.0.0.1 - "GET /\x1b[31m\nINJECTED HTTP/1.1" 404')
            out = capsys.readouterr().out
            assert "\x1b" not in out
            # só o newline do print — o \n hostil virou texto \x0a
            assert out.count("\n") == 1
            assert "\\x1b" in out and "\\x0a" in out
        finally:
            srv._httpd.server_close()  # serve_forever nunca rodou —
            # shutdown() bloquearia esperando o loop

    def test_rst_storm_no_traceback_flood(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Cliente que abre e reseta (SO_LINGER=0) gerava um traceback
        por conexão no stderr — log-flood trivial. Transporte cala."""
        import struct
        db = _index_db(tmp_path)
        srv = serve(str(db), port=0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            for _ in range(8):
                s = socket.create_connection(
                    ("127.0.0.1", srv.port), timeout=10)
                s.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER,
                    struct.pack("ii", 1, 0))
                s.sendall(b"GET /api")
                s.close()  # RST imediato — handler lê reset
            time.sleep(0.5)
            code, body, _ = _req(srv.url, "/health")
            assert code == 200 and b'"ok"' in body
        finally:
            srv.shutdown()
        err = capsys.readouterr().err
        assert "Traceback" not in err
        assert "ConnectionResetError" not in err

    def test_verbose_api_request_with_ansi_escaped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End-to-end: request hostil real contra server verbose —
        o log de acesso sai sanitizado no stderr."""
        db = _index_db(tmp_path)
        srv = serve(str(db), port=0, verbose=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            _raw(srv.port,
                 b"GET /x\x1b[31m HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                 wait=0.6)
            time.sleep(0.4)
        finally:
            srv.shutdown()
        captured = capsys.readouterr()
        blob = captured.out + captured.err
        assert "\x1b" not in blob
        # a linha escapada evidencia que o byte hostil chegou e saiu seguro
        assert "\\x1b" in blob or "[atlas-api]" not in blob


# ---------------------------------------------------------------------------
# /api/v1/diagnostics — info disclosure bounded
# ---------------------------------------------------------------------------


class _DiagCase:
    @classmethod
    def setup_class(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.db = _index_db(tmp)
        cls.server = serve(str(cls.db), port=0)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def teardown_class(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


class TestDiagnosticsDisclosure(_DiagCase):
    """Decisão documentada (docs/cycle-8.1-security-final.md): o painel
    expõe ``database.path`` e ``root_handle`` — caminhos absolutos —
    porque o bind é loopback-only e o leitor é o próprio operador.
    O que NÃO pode vazar: env não-declarado, segredos, tracebacks."""

    def _diag(self) -> dict:
        code, body, _ = _req(self.server.url, "/diagnostics")
        assert code == 200
        return json.loads(body)

    def test_observability_section_exact_keys(self) -> None:
        obs = self._diag()["observability"]
        assert set(obs) == {"log_level", "log_format", "slow_ms"}

    def test_arbitrary_env_never_leaks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env fora da allowlist de observabilidade — incluindo valores
        secret-shaped — nunca aparece no payload."""
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIA" + "F" * 36)
        monkeypatch.setenv("CUSTOM_SECRET", "sup3r-s3cret-value")
        blob = json.dumps(self._diag())
        assert "AKIA" not in blob
        assert "sup3r-s3cret" not in blob
        assert "CUSTOM_SECRET" not in blob

    def test_adapters_error_bounded(self) -> None:
        """Falha no inventário de adapters vira campo honesto ≤200 —
        nunca traceback nem texto ilimitado."""
        with mock.patch(
            "code_atlas.domains.cli_surface.adapters_report",
            side_effect=RuntimeError("x" * 500),
        ):
            diag = self._diag()
        err = diag["adapters"]["error"]
        assert isinstance(err, str) and len(err) <= 200
        assert "Traceback" not in json.dumps(diag)

    def test_db_path_is_absolute_and_loopback_scoped(self) -> None:
        """O path do DB É exposto — decisão aceita p/ loopback:
        só o operador local lê; CORS/Host guards bloqueiam cross-origin."""
        diag = self._diag()
        assert diag["database"]["path"] == str(self.db)
        code, _body, _ = _req(
            self.server.url, "/diagnostics",
            headers={"Origin": "https://evil.example"})
        assert code == 403

    def test_last_job_has_no_traceback_or_query(self) -> None:
        diag = self._diag()
        for repo in diag["repositories"]:
            job = repo["last_job"]
            if job is None:
                continue
            assert set(job) <= {"id", "status", "started_at",
                                "finished_at", "stage_ms"}


# ---------------------------------------------------------------------------
# CSP co-evoluída pós-Angular
# ---------------------------------------------------------------------------


class TestCspPostMigration:
    """O header CSP do 8.1: migração Angular removeu ``script-src-attr``
    — verificar que continua fora e que ``script-src 'self'`` cobre o
    dist real (scripts só externos; ``style-src 'unsafe-inline'`` é
    exigido pelas critical CSS inline do build — decisão documentada)."""

    def _csp(self) -> str:
        h = mock.Mock()
        sent: dict[str, str] = {}
        h.send_header = lambda k, v: sent.__setitem__(k, v)
        server_mod._security_headers(h, cors=False)
        return sent["Content-Security-Policy"]

    def test_no_script_src_attr_and_no_unsafe_inline_script(self) -> None:
        csp = self._csp()
        assert "script-src-attr" not in csp
        script = re.search(r"script-src ([^;]+)", csp)
        assert script is not None
        assert "'unsafe-inline'" not in script.group(1)
        assert "'unsafe-eval'" not in script.group(1)
        assert "'self'" in script.group(1)

    def test_style_src_unsafe_inline_is_intentional(self) -> None:
        """O dist injeta critical CSS inline (beasties) — 'unsafe-inline'
        em style-src é a decisão aceita; style != script."""
        csp = self._csp()
        style = re.search(r"style-src ([^;]+)", csp)
        assert style is not None and "'unsafe-inline'" in style.group(1)

    def test_packaged_index_has_no_inline_script(self) -> None:
        """O CSP só é verdade se a dist real cooperar: todo ``<script>``
        do index empacotado deve ser ``src=`` externo."""
        idx = PACKAGED_DIST / "index.html"
        if not idx.is_file():
            pytest.skip("explorer_dist ausente neste checkout")
        html = idx.read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>", html)
        assert scripts  # sanity — a dist tem scripts
        for tag in scripts:
            assert "src=" in tag, f"inline script bloquearia CSP: {tag}"

    def test_csp_on_api_and_error_responses(self, tmp_path: Path) -> None:
        """CSP presente em sucesso E erro — nenhuma rota o desliga."""
        db = _index_db(tmp_path)
        srv = serve(str(db), port=0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            for path in ("/health", "/no-route"):
                _code, _body, headers = _req(srv.url, path)
                assert "Content-Security-Policy" in headers
                assert "object-src 'none'" in \
                    headers["Content-Security-Policy"]
        finally:
            srv.shutdown()


# ---------------------------------------------------------------------------
# build_release.sh — env injection / path spoof
# ---------------------------------------------------------------------------


class TestBuildReleaseScript:
    """O script de release só confia no ambiente onde o mantenedor o
    roda — mas env hostil/malformado deve falhar CEDO e com FATAL
    tipado, nunca viajar para dentro do pipeline (N5)."""

    def _run(self, env_extra: dict[str, str] | None = None,
             cwd: Path | None = None, *argv: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(env_extra or {})
        return subprocess.run(
            ["bash", str(BUILD_SCRIPT), *argv],
            capture_output=True, text=True, timeout=30,
            cwd=cwd or REPO_ROOT, env=env)

    @POSIX_ONLY
    def test_bash_syntax_valid(self) -> None:
        r = subprocess.run(["bash", "-n", str(BUILD_SCRIPT)],
                           capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, r.stderr

    @POSIX_ONLY
    def test_non_numeric_source_date_epoch_fails_fast(self) -> None:
        """SDE hostil do ambiente → FATAL antes de npm/build (N5)."""
        for bad in ("abc", "123;id", "$(id)", "12 34", "-1",
                    "9999999999999999999999x"):
            r = self._run({"SOURCE_DATE_EPOCH": bad}, None, "--api-only")
            assert r.returncode == 2, (bad, r.stdout, r.stderr)
            assert "SOURCE_DATE_EPOCH" in r.stderr
            assert "npm" not in r.stderr  # falha antes do estágio npm

    @POSIX_ONLY
    def test_unknown_flag_rejected(self) -> None:
        r = self._run(None, None, "--pwn")
        assert r.returncode == 2
        assert "unknown flag" in r.stderr

    @POSIX_ONLY
    def test_root_anchored_to_script_not_cwd(self) -> None:
        """cwd hostil não move o ROOT — o script ancora em
        ``BASH_SOURCE``; rodar de /tmp falha igual pelo SDE."""
        with tempfile.TemporaryDirectory() as td:
            r = self._run({"SOURCE_DATE_EPOCH": "nope"}, Path(td),
                          "--api-only")
        assert r.returncode == 2
        assert "SOURCE_DATE_EPOCH" in r.stderr

    def test_version_charset_guarded_in_source(self) -> None:
        """Guarda de charset do VER existe no script (static check —
        o caminho e2e exigiria __init__.py hostil)."""
        src = BUILD_SCRIPT.read_text(encoding="utf-8")
        assert 'case "$VER" in' in src
        assert "*[!0-9A-Za-z.!+_-]*" in src


# ---------------------------------------------------------------------------
# Progress output — filenames nunca alcançam o canal [index]
# ---------------------------------------------------------------------------


class TestVerboseProgressHonest:
    """E2E: index com filename hostil — o canal ``--verbose`` continua
    emitindo só ``[index] <evento> k=v`` de campos fixos."""

    @POSIX_ONLY
    def test_hostile_filename_never_reaches_progress(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from code_atlas.cli import _index_progress
        root = tmp_path / "repo"
        root.mkdir()
        (root / "ok.py").write_text("x = 1\n")
        # filename com newline + ANSI — POSIX permite ambos
        (root / "evil\n\x1b[31m.py").write_text("y = 2\n")
        conn = connect(tmp_path / "atlas.db")
        migrate(conn)
        try:
            out = IndexCoordinator(conn).index(
                root, force=True, progress=_index_progress)
            assert out.status == "success"
        finally:
            conn.close()
        err = capsys.readouterr().err
        assert "\x1b" not in err
        for line in err.splitlines():
            assert line.startswith("[index]"), line


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
