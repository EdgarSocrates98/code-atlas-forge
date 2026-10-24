"""Fixture builder: sintetiza bytes protobuf válidos de um índice SCIP.

Encoder mínimo (test-only) do wire format — espelha o decoder para
garantir que o fixture exercita o caminho real, não um atalho.
"""

from __future__ import annotations


def _varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field_no: int, wire: int) -> bytes:
    return _varint((field_no << 3) | wire)


def f_varint(no: int, v: int) -> bytes:
    return _tag(no, 0) + _varint(v)


def f_string(no: int, s: str) -> bytes:
    b = s.encode("utf-8")
    return _tag(no, 2) + _varint(len(b)) + b


def f_msg(no: int, payload: bytes) -> bytes:
    return _tag(no, 2) + _varint(len(payload)) + payload


def f_packed_ints(no: int, vals: list[int]) -> bytes:
    body = b"".join(_varint(v & 0xFFFFFFFFFFFFFFFF) for v in vals)
    return _tag(no, 2) + _varint(len(body)) + body


def tool_info(name: str = "scip-python", version: str = "0.11.0") -> bytes:
    return f_string(1, name) + f_string(2, version)


def metadata(project_root: str, tool: bytes = b"") -> bytes:
    return (
        f_varint(1, 0)
        + f_msg(2, tool or tool_info())
        + f_string(3, project_root)
        + f_varint(4, 1)
    )


def slr(line: int, sc: int, ec: int) -> bytes:
    return f_varint(1, line) + f_varint(2, sc) + f_varint(3, ec)


def occurrence(symbol: str, rng: bytes, roles: int = 0) -> bytes:
    # range multilinha via single_line_range (field 8)
    return (
        f_msg(8, rng)
        + f_string(2, symbol)
        + f_varint(3, roles)
    )


def symbol_info(symbol: str, kind: int = 0, docs: list[str] | None = None) -> bytes:
    out = f_string(1, symbol)
    for d in docs or []:
        out += f_string(3, d)
    if kind:
        out += f_varint(5, kind)
    return out


def document(path: str, language: str = "python",
             occs: list[bytes] | None = None,
             syms: list[bytes] | None = None) -> bytes:
    out = f_string(1, path) + f_string(4, language)
    for o in occs or []:
        out += f_msg(2, o)
    for s in syms or []:
        out += f_msg(3, s)
    out += f_varint(6, 3)  # UTF32 position encoding
    return out


def index_bytes(project_root: str, docs: list[bytes],
                tool: bytes = b"") -> bytes:
    out = f_msg(1, metadata(project_root, tool))
    for d in docs:
        out += f_msg(2, d)
    return out
