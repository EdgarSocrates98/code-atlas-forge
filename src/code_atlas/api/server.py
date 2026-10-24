"""Servidor HTTP local — stdlib only, loopback, hardening de segurança.

Decisão (ADR cycle-6): ``http.server`` + ``ThreadingHTTPServer`` em vez de
FastAPI/Starlette — zero dependências novas (regra do repo), distribuição
mínima, superfície pequena e auditável. Read-only por contrato: a conexão
SQLite abre com ``read_only=True`` e nenhum handler escreve.

Segurança implementada:
- bind somente em ``127.0.0.1`` (host configurável mas nunca ``0.0.0.0``
  por default);
- allowlist de ``Host`` (127.0.0.1/localhost[:porta]) — anti DNS-rebinding;
- ``Origin`` estrangeiro → 403 (CORS restritivo; sem Origin → permitido,
  clientes locais/curl);
- ``Content-Length`` limitado; corpo JSON limitado; resposta limitada;
- erros tipados (``atlas-error``), nunca traceback ao cliente;
- ``X-Content-Type-Options``, ``Cache-Control: no-store``, sem cookies;
- sem execução de código indexado, sem leitura de arquivos arbitrária.
"""

from __future__ import annotations

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from code_atlas.storage.db import connect

API_PREFIX = "/api/v1"
MAX_BODY = 64 * 1024  # 64 KiB — payloads de context/impact são pequenos
MAX_RESPONSE = 4 * 1024 * 1024  # 4 MiB
_ALLOWED_HOSTS = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


class ApiError(Exception):
    """Erro tipado retornado ao cliente."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _security_headers(h: BaseHTTPRequestHandler, *, cors: bool) -> None:
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Referrer-Policy", "no-referrer")
    if cors:
        h.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        h.send_header("Vary", "Origin")


class _AtlasHandler(BaseHTTPRequestHandler):
    server: _AtlasHttpd
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        self.server.log(format(fmt % args)) if self.server.verbose else None

    # --- plumbing -----------------------------------------------------
    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        if len(body) > MAX_RESPONSE:
            err = {"error": {"code": "response-too-large",
                             "message": "resposta excedeu o limite interno"}}
            body = json.dumps(err).encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        _security_headers(self, cors=self._origin_ok())
        self.end_headers()
        self.wfile.write(body)

    def _error(self, e: Exception) -> None:
        if isinstance(e, ApiError):
            self._json({"error": {"code": e.code, "message": e.message}},
                       e.status)
            return
        self._json({"error": {"code": "internal", "message": str(e)[:200]}}, 500)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlparse(origin).netloc
        return bool(_ALLOWED_HOSTS.match(host))

    def _guard(self) -> None:
        host = self.headers.get("Host", "")
        if not _ALLOWED_HOSTS.match(host):
            raise ApiError("bad-host", "Host não permitido", 403)
        if not self._origin_ok():
            raise ApiError("bad-origin", "Origin não permitida", 403)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("payload-too-large", "corpo excede 64 KiB", 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError("bad-json", "corpo não é JSON válido") from exc
        if not isinstance(data, dict):
            raise ApiError("bad-json", "corpo deve ser um objeto JSON")
        return data

    def _serve_static(self, path: str) -> None:
        """Assets compilados do Explorer — allowlist de tipos, sem traversal."""
        import mimetypes
        assert self.server.static_root is not None
        root = self.server.static_root.resolve()
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            # SPA fallback — rotas do Angular caem no index.html
            target = root / "index.html"
            if not target.is_file():
                raise ApiError("not-found", "asset inexistente", 404)
        allowed = {".html", ".js", ".css", ".json", ".svg", ".png", ".ico",
                   ".woff", ".woff2", ".txt", ".map"}
        if target.suffix.lower() not in allowed:
            raise ApiError("not-found", "tipo de asset não permitido", 404)
        body = target.read_bytes()
        if len(body) > MAX_RESPONSE:
            raise ApiError("response-too-large", "asset excede o limite", 500)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        _security_headers(self, cors=False)
        self.end_headers()
        self.wfile.write(body)

    # --- verbs ---------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        if not self._origin_ok():
            self._json({"error": {"code": "bad-origin",
                                  "message": "Origin não permitida"}}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "300")
        _security_headers(self, cors=True)
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        try:
            self._guard()
            path = urlparse(self.path).path
            if not path.startswith(API_PREFIX):
                if method == "GET" and self.server.static_root:
                    self._serve_static(path)
                    return
                raise ApiError("not-found", "rota inexistente", 404)
            route = path[len(API_PREFIX):]
            from code_atlas.api.handlers import handle
            self._json(handle(self.server, method, route, self))
        except ApiError as e:
            self._error(e)
        except Exception as e:  # nunca expor traceback
            self._error(e)
        except BrokenPipeError:
            pass


class _AtlasHttpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], db_path: str,
                 verbose: bool = False,
                 static_root: Path | None = None) -> None:
        super().__init__(addr, _AtlasHandler)
        self.db_path = db_path
        self.verbose = verbose
        self.static_root = static_root

    def log(self, msg: str) -> None:
        print(f"[atlas-api] {msg}", flush=True)

    def connect(self) -> sqlite3.Connection:
        conn = connect(Path(self.db_path), read_only=True)
        conn.execute("PRAGMA query_only = ON")
        return conn


class AtlasApiServer:
    """Wrapper de ciclo de vida — usado pela CLI e testes."""

    def __init__(self, db_path: str, host: str = "127.0.0.1",
                 port: int = 8741, verbose: bool = False,
                 static_root: Path | None = None) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ApiError("unsafe-bind",
                           "bind público proibido — use 127.0.0.1")
        self._httpd = _AtlasHttpd(
            (host, port), db_path, verbose, static_root=static_root)
        self.port = self._httpd.server_address[1]
        self.host = host

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{API_PREFIX}"

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8741,
          verbose: bool = False,
          static_root: Path | None = None) -> AtlasApiServer:
    """Sobe o servidor — retorna handle para shutdown limpo."""
    return AtlasApiServer(db_path, host=host, port=port, verbose=verbose,
                          static_root=static_root)
