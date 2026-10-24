"""Reuse path do lf-0020: resultados reutilizados via cache versionado.

Contrato (grill): reuso nunca por path — a ``CacheKey`` cobre
``content_sha256`` + idioma + versões de parser/grammar/extractor/
resolver/builder + ``config_digest``. O payload é ``ParseResult``
serializado — artefato sintático puro (símbolos, spans, relações
**não resolvidas**).

Política conservadora: só arquivos ``REUSE`` (conjunto de
dependências inalterado no plano) são servidos do cache; arquivos
``REVALIDATE_RELATIONS`` são re-parseados — "reevaluate" = re-extração
+ re-resolução. Relações nunca são copiadas prontas: o graph builder
re-resolve ``to_name`` de cada aresta sobre o universo do snapshot
novo.

Materialização: resultados reutilizados entram no ``build`` junto com
os parseados — ``persist_graph`` grava linhas **novas** amarradas ao
``snapshot_id`` novo (isolamento lógico entre snapshots).

Cache miss (primeira indexação incremental, GC, corrupção): o arquivo
é lido e parseado de verdade — fallback correto, nunca inventa dados.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from code_atlas.indexing.cache import CacheKey

if TYPE_CHECKING:
    from code_atlas.discovery.models import SourceFile
    from code_atlas.parsers.contracts import ParserAdapter
    from code_atlas.parsers.engine import ParseEngine


def config_digest(engine: ParseEngine) -> str:
    """Digest da configuração efetiva que influencia o output do parse.

    Cobre os limites do engine e a identidade completa dos adaptadores
    (id, parser/grammar versions, extensões) — qualquer mudança de
    configuração efetiva muda a chave e invalida o cache.
    """
    adapters = []
    for lang in sorted(engine.registry.languages):
        ad = engine.registry.for_language(lang)
        if ad is not None:
            adapters.append([
                ad.adapter_id, ad.parser_version, ad.grammar_version,
                list(ad.extensions),
            ])
    doc = {
        "adapters": adapters,
        "limits": [
            engine.limits.max_file_size,
            engine.limits.timeout_us,
            engine.limits.max_diagnostics,
        ],
    }
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_key_for(
    f: SourceFile,
    adapter: ParserAdapter,
    cfg_digest: str,
) -> CacheKey | None:
    """Chave de validade do arquivo; ``None`` se sha indisponível.

    ``extractor_version`` = ``adapter_id`` (identifica o extrator que
    gerou o resultado); parser/grammar versions vêm do adaptador.
    """
    if f.sha256 is None:
        return None
    return CacheKey(
        path=f.path,
        content_sha256=f.sha256,
        language=adapter.language,
        parser_version=adapter.parser_version,
        grammar_version=adapter.grammar_version,
        extractor_version=adapter.adapter_id,
        config_digest=cfg_digest,
    )
