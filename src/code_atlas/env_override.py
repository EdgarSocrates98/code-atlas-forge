"""Contexto de override temporário de variáveis de ambiente.

Fora dos pacotes ``domains/``/``federation/``/``eval/`` — o sweep de
segurança proíbe ``import os`` nesses diretórios (portabilidade de
paths); manipulação de ambiente vive aqui.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def patched_env(overrides: dict[str, str]) -> Iterator[None]:
    """Aplica ``overrides`` e restaura o ambiente anterior ao sair."""
    old = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
