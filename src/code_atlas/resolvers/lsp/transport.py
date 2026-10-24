"""Transporte JSON-RPC 2.0 sobre stdio com framing Content-Length (lf-0018).

Funciona sobre qualquer par (reader, writer) de streams com ``fileno()``
— pipes reais de um processo filho ou pipes in-process dos testes.
Regras de segurança:

- Leitura via ``select`` sobre o fd + ``os.read`` — a thread daemon
  acorda a cada 100ms para conferir o flag de fechamento: ``close()``
  nunca depende do lock de um BufferedReader bloqueado (deadlock).
- Recebimento sempre com timeout — um servidor travado nunca bloqueia
  o caller para sempre.
- Payload máximo configurável (DoS).
- Erros de framing/JSON viram ``TransportError`` com contexto.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import select
import threading
from dataclasses import dataclass
from typing import IO, Any

MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # 16 MiB por mensagem
_POLL_S = 0.1
_IS_WINDOWS = os.name == "nt"  # select() no Windows só aceita sockets


class TransportError(RuntimeError):
    """Framing inválido, stream fechado ou mensagem corrompida."""


@dataclass(frozen=True)
class RpcResponse:
    """Resposta JSON-RPC (result OU error, nunca os dois)."""

    request_id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None
    is_notification: bool = False
    method: str = ""
    params: Any = None


class _FdReader:
    """Reader incremental sobre um fd — ``os.read``+``select``, sem o
    lock de BufferedReader que causava deadlock em ``close()``."""

    def __init__(self, fd: int, closed: threading.Event) -> None:
        self._fd = fd
        self._closed = closed
        self._buf = bytearray()

    def _read_some(self) -> bytes | None:
        """Chunk novo ou None (EOF/fechado). Polla ``_closed`` a cada
        100ms para shutdown limpo. No Windows ``select`` não aceita
        pipes — usa ``os.read`` bloqueante; ``close()`` destrava a
        thread fechando o fd."""
        while not self._closed.is_set():
            if _IS_WINDOWS:
                try:
                    return os.read(self._fd, 65536) or None
                except OSError:
                    return None
            try:
                ready, _, _ = select.select([self._fd], [], [], _POLL_S)
            except (OSError, ValueError):
                return None
            if not ready:
                continue
            try:
                data = os.read(self._fd, 65536)
            except OSError:
                return None
            if not data:
                return None
            return data
        return None

    def readline(self) -> bytes | None:
        """Linha terminada em \\n ou None (EOF/fechado)."""
        while True:
            idx = self._buf.find(b"\n")
            if idx >= 0:
                line = bytes(self._buf[: idx + 1])
                del self._buf[: idx + 1]
                return line
            chunk = self._read_some()
            if chunk is None:
                return None
            self._buf += chunk

    def read_exact(self, n: int) -> bytes | None:
        """Exatamente ``n`` bytes ou None (EOF/fechado)."""
        while len(self._buf) < n:
            chunk = self._read_some()
            if chunk is None:
                return None
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class JsonRpcTransport:
    """Framing LSP: ``Content-Length: N\\r\\n\\r\\n<json>``.

    Thread daemon lê mensagens continuamente e as deposita numa queue;
    ``recv`` consome com timeout — servidor pendurado → ``TimeoutError``.
    """

    def __init__(
        self,
        reader: IO[bytes],
        writer: IO[bytes],
        *,
        max_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self._writer = writer
        self._max = max_bytes
        self._inbox: queue.Queue[RpcResponse | BaseException] = queue.Queue()
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        try:
            fd = reader.fileno()
        except (OSError, AttributeError) as exc:
            raise TransportError(
                f"reader has no usable fileno: {exc}") from exc
        # guarda o objeto reader — se ele for coletado, o fd morre junto
        # e o transport fica surdo silenciosamente
        self._reader = reader
        self._reader_fd = fd
        self._fd_reader = _FdReader(fd, self._closed)
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="atlas-lsp-reader")
        self._reader_thread.start()

    # -- leitura ---------------------------------------------------------

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                msg = self._read_message()
                if msg is None:
                    break
                self._inbox.put(msg)
        except BaseException as exc:  # stream morreu → erro na inbox
            self._inbox.put(exc)
        finally:
            self._closed.set()

    def _read_headers(self) -> int | None:
        """None quando o stream fecha de forma limpa."""
        length: int | None = None
        while True:
            line = self._fd_reader.readline()
            if line is None:
                return None
            line = line.strip()
            if not line:
                break
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError as exc:
                    raise TransportError(
                        f"bad Content-Length: {line!r}") from exc
        if length is None:
            raise TransportError("missing Content-Length header")
        if length > self._max or length <= 0:
            raise TransportError(f"message size {length} out of bounds")
        return length

    def _read_message(self) -> RpcResponse | None:
        length = self._read_headers()
        if length is None:
            return None
        body = self._fd_reader.read_exact(length)
        if body is None or len(body) != length:
            raise TransportError("truncated body / stream closed")
        try:
            obj = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError(f"invalid json payload: {exc}") from exc
        if not isinstance(obj, dict):
            raise TransportError("json-rpc payload is not an object")
        return RpcResponse(
            request_id=obj.get("id"),
            result=obj.get("result"),
            error=obj.get("error"),
            is_notification="id" not in obj,
            method=obj.get("method", ""),
            params=obj.get("params"),
        )

    # -- API pública ------------------------------------------------------

    def send(self, payload: dict[str, Any]) -> None:
        """Serializa e envia uma mensagem JSON-RPC."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        with self._write_lock:
            try:
                self._writer.write(frame)
                self._writer.flush()
            except (OSError, ValueError) as exc:
                raise TransportError(f"send failed: {exc}") from exc

    def recv(self, timeout: float) -> RpcResponse:
        """Próxima mensagem com timeout obrigatório."""
        try:
            item = self._inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"no LSP message within {timeout}s") from exc
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        """Acorda a thread leitora (≤100ms) e fecha os streams — sem
        deadlock: o fd é fechado depois do poll sair."""
        self._closed.set()
        self._reader_thread.join(timeout=1.0)
        with contextlib.suppress(OSError):
            os.close(self._reader_fd)
        with contextlib.suppress(OSError):
            self._writer.close()
