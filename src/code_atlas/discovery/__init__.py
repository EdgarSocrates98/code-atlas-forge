"""Discovery Git-aware: inventário de arquivos, fingerprints e manifest."""

from code_atlas.discovery.models import (
    GitInfo,
    ScanMode,
    ScanOptions,
    ScanResult,
    ScanWarning,
    SourceFile,
    WarningKind,
)
from code_atlas.discovery.scanner import probe_git, scan

__all__ = [
    "GitInfo",
    "ScanMode",
    "ScanOptions",
    "ScanResult",
    "ScanWarning",
    "SourceFile",
    "WarningKind",
    "probe_git",
    "scan",
]

