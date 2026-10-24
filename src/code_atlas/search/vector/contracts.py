"""Contratos do subsistema vetorial (lf-0039 / Cycle 4 Ph4).

Interfaces apenas — nenhuma dependência externa. sqlite-vec é um
extra opt-in (``[vector]``); ausência é reportada como capability
honesta, nunca erro silencioso.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingModelInfo:
    """Identidade versionada do modelo de embedding."""

    model_id: str          # ex.: "naive-hash"
    version: str           # ex.: "v1" — muda → reindex
    dim: int               # dimensionalidade do vetor
    detail: str = ""


class EmbeddingProvider(Protocol):
    """Gera embeddings para textos — local por default."""

    def info(self) -> EmbeddingModelInfo: ...
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Um vetor por texto, dimensão ``info().dim``, determinístico."""
        ...


@dataclass(frozen=True)
class VectorSearchResult:
    """Hit vetorial bruto (pré-fusão)."""

    symbol_key: str
    occurrence_id: int
    score: float           # similaridade cosseno [0, 1] após clamp
    model_id: str


class VectorIndex(Protocol):
    """Index de vetores por snapshot — bruteforce ou sqlite-vec."""

    def upsert(
        self, snapshot_id: int, occurrence_id: int,
        symbol_key: str, vector: list[float],
    ) -> None: ...
    def search(
        self, snapshot_id: int, query: list[float], limit: int,
    ) -> list[VectorSearchResult]: ...
    def delete_snapshot(self, snapshot_id: int) -> None: ...


class EmbeddingCache(Protocol):
    """Cache por conteúdo+modelo — rebuild só quando algo muda."""

    def get(
        self, model: EmbeddingModelInfo, content_hash: str
    ) -> list[float] | None: ...
    def put(
        self, model: EmbeddingModelInfo, content_hash: str,
        vector: list[float],
    ) -> None: ...


@dataclass(frozen=True)
class VectorCapability:
    """O que o backend vetorial oferece nesta build/instalação."""

    available: bool
    backend: str           # "sqlite-vec" | "bruteforce" | "none"
    provider: str          # model_id ou ""
    dim: int = 0
    detail: str = ""
