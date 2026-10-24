"""Decoder protobuf wire-format mínimo para índices SCIP (lf-0017).

Decisão (grill): decoder stdlib próprio — zero dependências. Implementa o
wire format completo do Protocol Buffers: varints (incl. >32 bits),
zigzag, fixed32/64, campos length-delimited, packed repeated varints e
skip de campos desconhecidos. Erros carregam o field-path para diagnóstico.
"""

from __future__ import annotations

import struct
from typing import Any

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_SGROUP = 3   # deprecated — rejeitado explicitamente
WIRE_EGROUP = 4
WIRE_FIXED32 = 5


class DecodeError(ValueError):
    """Bytes malformados/truncados — sempre com field-path."""


def _zigzag(v: int) -> int:
    return (v >> 1) ^ -(v & 1)


class Reader:
    """Cursor de leitura sobre um buffer protobuf."""

    __slots__ = ("_buf", "_end", "_path", "_pos")

    def __init__(self, buf: bytes, path: str = "") -> None:
        self._buf = buf
        self._pos = 0
        self._end = len(buf)
        self._path = path

    def eof(self) -> bool:
        return self._pos >= self._end

    def _err(self, msg: str) -> DecodeError:
        return DecodeError(f"{self._path or '<root>'}: {msg}")

    def varint(self) -> int:
        """LEB128 — até 10 bytes; truncamento/overflow → erro."""
        result = 0
        shift = 0
        for _ in range(10):
            if self._pos >= self._end:
                raise self._err("truncated varint")
            b = self._buf[self._pos]
            self._pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7
        raise self._err("varint overflow (>10 bytes)")

    def tag(self) -> tuple[int, int]:
        """(field_number, wire_type); field 0 e tipos inválidos → erro."""
        v = self.varint()
        field_no, wire = v >> 3, v & 0x7
        if field_no == 0:
            raise self._err("field number 0")
        if wire in (WIRE_SGROUP, WIRE_EGROUP):
            raise self._err(f"deprecated group wire type {wire}")
        if wire > WIRE_FIXED32:
            raise self._err(f"invalid wire type {wire}")
        return field_no, wire

    def _take(self, n: int) -> bytes:
        if n < 0 or self._pos + n > self._end:
            raise self._err(f"truncated field (need {n} bytes)")
        out = self._buf[self._pos : self._pos + n]
        self._pos += n
        return out

    def fixed32(self) -> int:
        v: int = struct.unpack("<I", self._take(4))[0]
        return v

    def fixed64(self) -> int:
        v: int = struct.unpack("<Q", self._take(8))[0]
        return v

    def sfixed32(self) -> int:
        v: int = struct.unpack("<i", self._take(4))[0]
        return v

    def sfixed64(self) -> int:
        v: int = struct.unpack("<q", self._take(8))[0]
        return v

    def sint(self) -> int:
        return _zigzag(self.varint())

    def int32(self) -> int:
        v = self.varint()
        # int32 negativos viajam como varint de 10 bytes (complemento 64)
        if v >= 1 << 63:
            v -= 1 << 64
        elif v >= 1 << 31:
            v -= 1 << 32
        return v

    def bool_(self) -> bool:
        return self.varint() != 0

    def double(self) -> float:
        v: float = struct.unpack("<d", self._take(8))[0]
        return v

    def float_(self) -> float:
        v: float = struct.unpack("<f", self._take(4))[0]
        return v

    def bytes_(self) -> bytes:
        return self._take(self.varint())

    def string(self) -> str:
        try:
            return self.bytes_().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self._err(f"invalid utf-8 string: {exc}") from exc

    def packed_int32(self) -> list[int]:
        """Campo repeated int32 packed (wire LEN)."""
        data = self.bytes_()
        sub = Reader(data, path=f"{self._path}<packed>")
        out: list[int] = []
        while not sub.eof():
            out.append(sub.int32())
        return out

    def packed_enum(self) -> list[int]:
        data = self.bytes_()
        sub = Reader(data, path=f"{self._path}<packed>")
        out: list[int] = []
        while not sub.eof():
            out.append(sub.varint())
        return out

    def sub(self, path: str) -> Reader:
        """Sub-reader para um campo length-delimited (mensagem)."""
        return Reader(self.bytes_(), path=path)

    def skip(self, wire: int) -> None:
        """Consome um campo de tipo desconhecido."""
        if wire == WIRE_VARINT:
            self.varint()
        elif wire == WIRE_FIXED64:
            self._take(8)
        elif wire == WIRE_LEN:
            self._take(self.varint())
        elif wire == WIRE_FIXED32:
            self._take(4)
        else:  # pragma: no cover — tag() já rejeita
            raise self._err(f"cannot skip wire type {wire}")


def decode_message(buf: bytes, spec: dict[int, tuple[str, Any]],
                   path: str = "") -> dict[str, Any]:
    """Decodifica uma mensagem conforme `spec` {field_no: (name, kind)}.

    `kind` é um string-dispatch: "varint","int32","sint","bool","fixed32",
    "fixed64","sfixed32","sfixed64","double","float","bytes","string",
    "packed_int32","packed_enum", uma tupla (spec, "message") para
    mensagem aninhada, ou ("repeated", inner_kind) para campos repetidos.
    Campos não mapeados são skipados preservando o wire type.
    """
    r = Reader(buf, path=path)
    out: dict[str, Any] = {}
    while not r.eof():
        field_no, wire = r.tag()
        entry = spec.get(field_no)
        if entry is None:
            r.skip(wire)
            continue
        name, kind = entry
        val = _read_field(r, kind, wire, f"{path}.{name}" if path else name)
        if _is_repeated(kind):
            out.setdefault(name, []).extend(val)
        else:
            out[name] = val
    return out


def _is_repeated(kind: Any) -> bool:
    return isinstance(kind, tuple) and bool(kind) and kind[0] == "repeated"


def _read_field(r: Reader, kind: Any, wire: int, path: str) -> Any:
    if isinstance(kind, tuple) and kind and kind[0] == "repeated":
        inner = kind[1]
        # packed só para escalares varint — len-delimited acumula valores
        if wire == WIRE_LEN and inner in ("int32", "packed_int32"):
            return r.packed_int32()
        return [_read_field(r, inner, wire, path)]
    if isinstance(kind, tuple) and len(kind) == 2 and kind[1] == "message":
        return decode_message(r.bytes_(), kind[0], path=path)

    readers: dict[str, Any] = {
        "varint": r.varint, "int32": r.int32, "sint": r.sint,
        "bool": r.bool_, "fixed32": r.fixed32, "fixed64": r.fixed64,
        "sfixed32": r.sfixed32, "sfixed64": r.sfixed64,
        "double": r.double, "float": r.float_, "bytes": r.bytes_,
        "string": r.string,
        "packed_int32": r.packed_int32, "packed_enum": r.packed_enum,
    }
    fn = readers.get(kind)
    if fn is None:
        raise DecodeError(f"{path}: unknown field kind {kind!r}")
    return fn()
