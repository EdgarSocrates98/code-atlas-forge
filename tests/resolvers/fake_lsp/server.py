"""Fake LSP server in-process para testes (lf-0018).

Roda numa thread sobre pipes do ``os`` — implementa o framing
Content-Length e responde initialize/definition/references/documentSymbol
conforme o comportamento configurado:

- ``caps``: quais capabilities anunciar no initialize (gating real).
- ``hang``: nunca responde (teste de timeout/cancel).
- ``crash_after``: fecha o stream após N requests (teste de crash).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field


def _frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode()
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def _read_exact(r, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = r.read(n - len(out))
        if not chunk:
            raise EOFError
        out += chunk
    return out


def read_message(r) -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = r.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        k, _, v = line.partition(b":")
        headers[k.decode().lower()] = v.decode().strip()
    length = int(headers["content-length"])
    return json.loads(_read_exact(r, length))


@dataclass
class FakeLspServer:
    """Servidor stdio fake — comportamento configurável por flags."""

    caps: frozenset[str] = frozenset(
        {"definitionProvider", "referencesProvider",
         "documentSymbolProvider", "workspaceSymbolProvider"})
    hang: bool = False
    crash_after: int | None = None
    server_name: str = "fake-lsp"
    server_version: str = "0.0.1"
    definition_locations: list = field(default_factory=list)
    reference_locations: list = field(default_factory=list)

    def serve(self, reader, writer) -> None:
        """Loop de requests — chamado numa thread."""
        n = 0
        try:
            while True:
                msg = read_message(reader)
                if msg is None:
                    return
                if "id" not in msg:  # notificação — ignora
                    continue
                n += 1
                if self.crash_after is not None and n > self.crash_after:
                    return  # morre: fecha streams no finally
                if self.hang:
                    continue  # nunca responde → timeout no cliente
                resp = self._dispatch(msg["method"], msg["id"])
                writer.write(_frame(resp))
                writer.flush()
        except (EOFError, OSError, BrokenPipeError):
            return

    def _dispatch(self, method: str, rid: int | str) -> dict:
        """Método → resposta JSON-RPC (result ou error)."""
        if method == "initialize":
            caps = {c: True for c in self.caps}
            result = {
                "capabilities": caps,
                "serverInfo": {"name": self.server_name,
                               "version": self.server_version},
            }
        elif method == "shutdown":
            result = None
        elif method == "textDocument/definition":
            result = self.definition_locations
        elif method == "textDocument/references":
            result = self.reference_locations
        elif method in ("textDocument/documentSymbol", "workspace/symbol"):
            result = []
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601,
                              "message": f"unknown {method}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def pipe_pair():
    """(client_read, client_write, server_read, server_write)."""
    c_r_fd, s_w_fd = os.pipe()
    s_r_fd, c_w_fd = os.pipe()
    return (
        os.fdopen(c_r_fd, "rb"), os.fdopen(c_w_fd, "wb"),
        os.fdopen(s_r_fd, "rb"), os.fdopen(s_w_fd, "wb"),
    )


def start_fake(server: FakeLspServer):
    """Spawna thread do fake server → transporte do lado do cliente."""
    from code_atlas.resolvers.lsp.transport import JsonRpcTransport

    c_r, c_w, s_r, s_w = pipe_pair()
    t = threading.Thread(
        target=server.serve, args=(s_r, s_w), daemon=True)
    t.start()
    return JsonRpcTransport(c_r, c_w), (s_r, s_w, t)
