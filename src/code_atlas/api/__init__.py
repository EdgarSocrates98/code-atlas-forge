"""API HTTP local para o Explorer — loopback-only, read-only, sem telemetria."""

from code_atlas.api.server import AtlasApiServer, serve

__all__ = ["AtlasApiServer", "serve"]
