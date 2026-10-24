"""Persistência SQLite (WAL, migrações, snapshots)."""

from code_atlas.storage.db import connect, fts5_available
from code_atlas.storage.migrations import MigrationError, migrate, schema_version
from code_atlas.storage.store import Store

__all__ = [
    "MigrationError",
    "Store",
    "connect",
    "fts5_available",
    "migrate",
    "schema_version",
]
