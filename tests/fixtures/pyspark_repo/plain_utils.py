"""Arquivo Python sem Spark — deve passar completamente em branco.

Nenhum import ``pyspark``, nenhum padrão ``spark.*``, nenhum op
camelCase de DataFrame: o adapter não pode emitir entidade, relação
ou diagnóstico de domínio para este arquivo.
"""

from __future__ import annotations

import hashlib
import os


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def join_path(*parts: str) -> str:
    return os.path.join(*parts)


class Greeter:
    def greet(self, name: str) -> str:
        return f"hello {name}"
