"""Auditoria estática: nenhuma query construída por concatenação/f-string.

Convenção do Blueprint §6 — todo execute/executemany recebe SQL constante
ou parametrizado; este teste escaneia o AST do código-fonte e reprova
``JoinableStr`` (f-string) ou concatenação ``+`` como primeiro argumento.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "code_atlas"

_EXEC_METHODS = {"execute", "executemany", "executescript"}


def _first_arg_dynamic(node: ast.Call) -> bool:
    if not node.args:
        return False
    arg = node.args[0]
    if isinstance(arg, ast.JoinedStr):
        return True
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return True
    return (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "format"
    )


def test_no_dynamic_sql_in_execute_calls() -> None:
    offenders: list[str] = []
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _EXEC_METHODS
                and _first_arg_dynamic(node)
            ):
                offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == []


def test_no_user_input_formatting_in_schema() -> None:
    migrations = (SRC / "storage" / "migrations.py").read_text(encoding="utf-8")
    # o SQL de schema é constante de módulo, nunca interpolado
    assert ".format(" not in migrations.split("_SCHEMA_V1")[1].split('"""')[0]
