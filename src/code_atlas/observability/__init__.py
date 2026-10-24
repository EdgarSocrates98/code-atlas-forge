"""Logging estruturado e redaction (sem telemetria, sem egress)."""

from code_atlas.observability.logging import JsonFormatter, RedactionFilter, get_logger

__all__ = ["JsonFormatter", "RedactionFilter", "get_logger"]
