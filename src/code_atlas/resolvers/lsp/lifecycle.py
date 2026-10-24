"""Ciclo de vida do cliente LSP (lf-0018).

spawn → initialize (handshake) → capability negotiation → requests com
timeout + ``$/cancelRequest`` → shutdown/exit → cleanup de processo.
Um servidor que trava ou morre vira ``unavailable`` — nunca bloqueia o
pipeline de indexação.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any

from code_atlas.resolvers.lsp.spawn import (
    LspProcess,
    LspServerSpec,
    SpawnError,
    release_server,
    spawn_server,
)
from code_atlas.resolvers.lsp.transport import (
    JsonRpcTransport,
    TransportError,
)

INIT_TIMEOUT_S = 15.0
SHUTDOWN_TIMEOUT_S = 5.0

# métodos que o Atlas usa — cada um só é chamado se o servidor anunciar
_METHOD_TO_CAP = {
    "textDocument/definition": "definitionProvider",
    "textDocument/references": "referencesProvider",
    "textDocument/documentSymbol": "documentSymbolProvider",
    "workspace/symbol": "workspaceSymbolProvider",
}


class LspError(RuntimeError):
    """Falha de protocolo/servidor — sempre diagnóstico, nunca crash."""


@dataclass
class ServerCapabilities:
    """Subconjunto negociado do resultado de ``initialize``."""

    definition: bool = False
    references: bool = False
    document_symbol: bool = False
    workspace_symbol: bool = False
    server_name: str = ""
    server_version: str = ""

    def supports(self, method: str) -> bool:
        cap = _METHOD_TO_CAP.get(method)
        return bool(getattr(self, {
            "definitionProvider": "definition",
            "referencesProvider": "references",
            "documentSymbolProvider": "document_symbol",
            "workspaceSymbolProvider": "workspace_symbol",
        }.get(cap or "", ""), False)) if cap else False


@dataclass
class LspClient:
    """Cliente stdio de um servidor LSP (um por spec habilitada)."""

    spec: LspServerSpec
    _proc: LspProcess | None = field(default=None, init=False)
    _transport: JsonRpcTransport | None = field(default=None, init=False)
    _ids: itertools.count[int] = field(
        default_factory=lambda: itertools.count(1), init=False)
    _caps: ServerCapabilities = field(
        default_factory=ServerCapabilities, init=False)
    _available: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock,
                                  init=False)

    # -- ciclo de vida ----------------------------------------------------

    def start(self) -> None:
        """Spawn + handshake initialize; falhas → unavailable."""
        try:
            self._proc = spawn_server(self.spec)
        except SpawnError as exc:
            raise LspError(str(exc)) from exc
        assert self._proc.stdin and self._proc.stdout
        self.attach(JsonRpcTransport(self._proc.stdout, self._proc.stdin))

    def attach(self, transport: JsonRpcTransport) -> None:
        """Conecta um transporte pronto (testes injetam pipes in-process)
        e faz o handshake initialize."""
        self._transport = transport
        try:
            self.handshake()
        except (LspError, TimeoutError, TransportError):
            self.close()
            raise

    def handshake(self) -> None:
        """``initialize`` → negocia capabilities → ``initialized``."""
        result = self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.spec.root_uri,
                "capabilities": {},
            },
            timeout=min(INIT_TIMEOUT_S, self.spec.request_timeout_s),
        )
        self._parse_initialize(result)
        self.notify("initialized", {})
        self._available = True

    def _parse_initialize(self, result: Any) -> None:
        caps = (result or {}).get("capabilities", {})
        info = (result or {}).get("serverInfo", {})
        self._caps = ServerCapabilities(
            definition=bool(caps.get("definitionProvider")),
            references=bool(caps.get("referencesProvider")),
            document_symbol=bool(caps.get("documentSymbolProvider")),
            workspace_symbol=bool(caps.get("workspaceSymbolProvider")),
            server_name=info.get("name", self.spec.name),
            server_version=info.get("version", ""),
        )

    @property
    def available(self) -> bool:
        """Servidor respondendo: handshake feito + transporte vivo +
        processo rodando (quando há processo real)."""
        if not self._available or self._transport is None:
            return False
        if self._proc is not None:
            return self._proc.poll() is None
        return True  # transporte injetado (testes)

    @property
    def capabilities(self) -> ServerCapabilities:
        return self._caps

    # -- requests ---------------------------------------------------------

    def request(
        self, method: str, params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Request com timeout obrigatório + cancelamento LSP."""
        if self._transport is None:
            raise LspError("transport not started")
        timeout = timeout or self.spec.request_timeout_s
        rid = next(self._ids)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._transport.send(payload)
            while True:
                msg = self._transport.recv(timeout)
                if msg.is_notification or msg.request_id != rid:
                    continue  # notificações/server-requests não correlacionados
                if msg.error:
                    raise LspError(
                        f"{method}: rpc error {msg.error.get('code')}: "
                        f"{msg.error.get('message')}")
                return msg.result
        except TimeoutError:
            self._cancel(rid)
            raise
        except TransportError as exc:
            self._available = False  # stream morreu → crash detectado
            raise LspError(f"{method}: transport died: {exc}") from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._transport is None:
            return
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._transport.send(payload)
        except TransportError:
            self._available = False

    def _cancel(self, rid: int) -> None:
        """``$/cancelRequest`` — cancelamento real LSP após timeout."""
        self.notify("$/cancelRequest", {"id": rid})

    def shutdown(self) -> None:
        """Sequência ordenada shutdown → exit."""
        if self._transport is None:
            return
        with contextlib.suppress(LspError, TimeoutError, TransportError):
            self.request("shutdown", None, timeout=SHUTDOWN_TIMEOUT_S)
        self.notify("exit")
        self.close()

    def close(self) -> None:
        """Fecha transporte e mata processo (zombie cleanup)."""
        self._available = False
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._proc is not None:
            release_server(self._proc)
            self._proc = None


def make_client(spec: LspServerSpec) -> LspClient | None:
    """Fábrica segura: spec desabilitado → None; spawn/handshake falho →
    ``LspError`` (caller vira diagnóstico)."""
    if not spec.enabled:
        return None
    client = LspClient(spec)
    client.start()
    return client
