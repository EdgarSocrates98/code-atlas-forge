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
- rate limit por cliente em memória (API somente; assets estáticos isentos)
  — 429 + ``Retry-After`` acima do teto;
- timeout por conexão — keep-alive ocioso e leitura travada são encerrados;
- erros tipados (``atlas-error``), nunca traceback ao cliente;
- ``X-Content-Type-Options``, ``Cache-Control: no-store``, sem cookies;
- sem execução de código indexado, sem leitura de arquivos arbitrária.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from code_atlas.storage.db import connect

API_PREFIX = "/api/v1"
MAX_BODY = 64 * 1024  # 64 KiB — payloads de context/impact são pequenos
MAX_RESPONSE = 4 * 1024 * 1024  # 4 MiB
RATE_LIMIT = 300  # req/min por cliente, rotas /api/v1 apenas
RATE_WINDOW = 60.0
REQUEST_TIMEOUT = 30.0  # s — teto de ociosidade por conexão
_ALLOWED_HOSTS = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


def _content_length(raw: str | None) -> int:
    """``Content-Length`` validado — negativo/não-inteiro nunca chega
    ao ``read()`` (``read(-1)`` bloquearia até o timeout e um header
    malformado virava 500 ``internal`` em vez de 400 tipado)."""
    try:
        length = int(raw or 0)
    except (TypeError, ValueError) as exc:
        raise ApiError("bad-content-length",
                       "Content-Length inválido", 400) from exc
    if length < 0 or length > MAX_BODY:
        raise ApiError("payload-too-large",
                       "corpo fora do limite (0..64 KiB)", 413)
    return length


class ApiError(Exception):
    """Erro tipado retornado ao cliente."""

    def __init__(self, code: str, message: str, status: int = 400,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers or {}


class _RateLimiter:
    """Janela deslizante em memória por chave de cliente (loopback)."""

    def __init__(self, limit: int, window: float = RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """True se o request passa; senão False + segundos de Retry-After."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1.0, self.window - (now - q[0]))
            q.append(now)
            return True, 0.0


def _security_headers(h: BaseHTTPRequestHandler, *, cors: bool) -> None:
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Referrer-Policy", "no-referrer")
    h.send_header("X-Frame-Options", "DENY")
    h.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; worker-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'")
    if cors:
        h.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        h.send_header("Vary", "Origin")


class _AtlasHandler(BaseHTTPRequestHandler):
    server: _AtlasHttpd
    protocol_version = "HTTP/1.1"
    # lf-0153: ``Server: BaseHTTP/0.6 Python/3.12.3`` fingerprintava a
    # stack em toda resposta — versão do produto só, sem a do runtime.
    server_version = "code-atlas-api"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.request_timeout)

    def handle(self) -> None:
        try:
            super().handle()
        except TimeoutError:
            # keep-alive ocioso ou leitura travada — encerra sem ruído
            self.close_connection = True

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        self.server.log(format(fmt % args)) if self.server.verbose else None

    # --- plumbing -----------------------------------------------------
    def _json(self, payload: Any, status: int = 200,
              extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        if len(body) > MAX_RESPONSE:
            err = {"error": {"code": "response-too-large",
                             "message": "resposta excedeu o limite interno"}}
            body = json.dumps(err).encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        _security_headers(self, cors=self._origin_ok())
        self.end_headers()
        self.wfile.write(body)

    def _error(self, e: Exception) -> None:
        if isinstance(e, ApiError):
            self._json({"error": {"code": e.code, "message": e.message}},
                       e.status, e.headers)
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
        length = _content_length(self.headers.get("Content-Length"))
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
        # Contenção real via pathlib — ``startswith`` em string aceita
        # irmãos com prefixo comum (``static-evil`` sob ``static``) e
        # permitia servir arquivos fora do root via ``..`` (lf-c8 F1).
        if not target.is_relative_to(root) or not target.is_file():
            # SPA fallback — rotas do Angular caem no index.html
            target = root / "index.html"
            if not target.is_file():
                raise ApiError("not-found", "asset inexistente", 404)
        allowed = {".html", ".js", ".css", ".json", ".svg", ".png", ".ico",
                   ".woff", ".woff2", ".txt", ".map"}
        if target.suffix.lower() not in allowed:
            raise ApiError("not-found", "tipo de asset não permitido", 404)
        # lf-0153: stat ANTES do read — um asset gigante plantado na
        # dist não pode ser carregado inteiro em memória só para ser
        # rejeitado depois (o check era pós-leitura).
        if target.stat().st_size > MAX_RESPONSE:
            raise ApiError("response-too-large", "asset excede o limite", 500)
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
            allowed, retry = self.server.rate_limiter.allow(
                self.client_address[0])
            if not allowed:
                raise ApiError(
                    "rate-limited",
                    "limite de requisições por minuto excedido", 429,
                    headers={"Retry-After": str(int(retry + 0.5))})
            route = path[len(API_PREFIX):]
            from code_atlas.api.handlers import handle
            self._json(handle(self.server, method, route, self))
        except ApiError as e:
            self._error(e)
        except ValueError:
            # lf-0153: request-target hostil que estoura no parse —
            # NUL em ``resolve()`` (``GET /a\x00b``), IPv6 malformado
            # em absolute-URI (``GET http://[``) — era 500 ``internal``
            # para o que é erro do cliente; agora 400 tipado.
            self._error(ApiError(
                "bad-request", "request-target malformado", 400))
        except TimeoutError:
            with contextlib.suppress(OSError):
                self._error(ApiError(
                    "request-timeout", "tempo de request excedido", 408))
        except Exception as e:  # nunca expor traceback
            self._error(e)


class _AtlasHttpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], db_path: str,
                 verbose: bool = False,
                 static_root: Path | None = None,
                 rate_limit: int = RATE_LIMIT,
                 request_timeout: float = REQUEST_TIMEOUT) -> None:
        super().__init__(addr, _AtlasHandler)
        self.db_path = db_path
        self.verbose = verbose
        # lf-0141: instante de subida para o uptime do ``/diagnostics``.
        self.started_at = time.monotonic()
        self.static_root = static_root
        self.request_timeout = request_timeout
        self.rate_limiter = _RateLimiter(rate_limit)

    def log(self, msg: str) -> None:
        # lf-0153: a request line crua carrega bytes de controle do
        # cliente (``GET /\x1b[31m``) — escape antes de tocar o stderr,
        # senão ``--verbose`` vira canal de ANSI/forged-line injection.
        from code_atlas.observability.logging import escape_control
        print(f"[atlas-api] {escape_control(msg)}", flush=True)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # lf-0153: ``socketserver`` despejava um traceback por conexão
        # RST — um cliente loopback hostil inundava stderr à vontade
        # (log-flood). Erros de transporte são rotina e calam; bugs
        # reais de handler continuam com o traceback default.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def connect(self) -> sqlite3.Connection:
        conn = connect(Path(self.db_path), read_only=True)
        conn.execute("PRAGMA query_only = ON")
        return conn


class AtlasApiServer:
    """Wrapper de ciclo de vida — usado pela CLI e testes."""

    def __init__(self, db_path: str, host: str = "127.0.0.1",
                 port: int = 8741, verbose: bool = False,
                 static_root: Path | None = None,
                 rate_limit: int = RATE_LIMIT,
                 request_timeout: float = REQUEST_TIMEOUT) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ApiError("unsafe-bind",
                           "bind público proibido — use 127.0.0.1")
        self._httpd = _AtlasHttpd(
            (host, port), db_path, verbose, static_root=static_root,
            rate_limit=rate_limit, request_timeout=request_timeout)
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


def explorer_static_root(repo_hint: Path | None = None) -> Path | None:
    """Resolve a dist compilada do Explorer para ``static_root``.

    Ordem (lf-0148, cycle-8.1 Phase 3): ``code_atlas/explorer_dist``
    empacotado no wheel via ``importlib.resources`` → checkout do repo
    em modo dev (``explorer/dist/explorer/browser`` ou
    ``explorer/dist`` relativo a ``repo_hint``). Retorna ``None``
    quando nenhum candidato tem ``index.html`` — o servidor sobe
    API-only nesse caso.

    Wheels são sempre instaladas descompactadas, então o traversable é
    um ``Path`` real; loaders exóticos (zipimport) caem no fallback em
    vez de quebrar.
    """
    try:
        from importlib.resources import files
        packaged = files("code_atlas").joinpath("explorer_dist")
        if (isinstance(packaged, Path)
                and (packaged / "index.html").is_file()):
            return packaged
    except (ModuleNotFoundError, TypeError, OSError):
        pass
    if repo_hint is not None:
        for candidate in (
                repo_hint / "explorer" / "dist" / "explorer" / "browser",
                repo_hint / "explorer" / "dist"):
            if (candidate / "index.html").is_file():
                return candidate
    return None


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8741,
          verbose: bool = False,
          static_root: Path | None = None,
          rate_limit: int = RATE_LIMIT,
          request_timeout: float = REQUEST_TIMEOUT) -> AtlasApiServer:
    """Sobe o servidor — retorna handle para shutdown limpo."""
    return AtlasApiServer(db_path, host=host, port=port, verbose=verbose,
                          static_root=static_root, rate_limit=rate_limit,
                          request_timeout=request_timeout)
