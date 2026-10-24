"""Fábrica de conexões SQLite (Blueprint ADR-006).

Pragmas obrigatórios: WAL, foreign_keys=ON, busy_timeout, synchronous=NORMAL.
Todos os caminhos de escrita usam transações explícitas; escritor único por
workspace é coordenado pelo IndexCoordinator via ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def connect(path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    """Abre conexão configurada para o workspace Atlas.

    ``read_only=True`` usa URI ``mode=ro`` para leitores concorrentes que
    nunca devem escrever nem criar o arquivo.
    """
    if read_only:
        uri = f"file:{Path(path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA foreign_keys = ON")
    # timeout= do connect() já aplica busy_timeout; PRAGMA não aceita bind.
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    """True se a build do sqlite suporta FTS5."""
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts_probe")
        return True
    except sqlite3.Error:
        return False
