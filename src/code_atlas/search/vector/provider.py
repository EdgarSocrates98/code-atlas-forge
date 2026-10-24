"""Provider de embeddings local — baseline naïve stdlib (lf-0039).

``NaiveEmbeddingProvider``: hashing de tokens com sinal (feature
hashing) → vetor L2-normalizado. Determinístico, offline, zero deps.

Baseline HONESTO: captura sobreposição lexical (útil para RRF), NÃO
semântica real — sinônimos ("order"/"pedido") não aproximam. Trocável
via seam ``EmbeddingProvider`` (provider externo só opt-in explícito;
default nunca faz egress).
"""

from __future__ import annotations

import hashlib
import math
import re

from code_atlas.search.vector.contracts import EmbeddingModelInfo

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class NaiveEmbeddingProvider:
    """Feature hashing: sha256(token) → (dim, sinal); soma e normaliza."""

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim deve ser > 0")
        self._dim = dim
        self._info = EmbeddingModelInfo(
            model_id="naive-hash", version="v1", dim=dim,
            detail="feature hashing de tokens (baseline não-semântico)")

    def info(self) -> EmbeddingModelInfo:
        return self._info

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for tok in _TOKEN_RE.findall(text.lower()):
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]
