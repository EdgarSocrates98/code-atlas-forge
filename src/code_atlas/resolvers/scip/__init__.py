"""Integração SCIP (opt-in) — decoder stdlib + resolver semântico.

Cobertura do schema ``scip.proto`` (documentada por campo):

Interpretados:
- Index.metadata{version, tool_info{name,version,arguments},
  project_root, text_document_encoding}
- Index.documents[] e Index.external_symbols[]
- Document{language, relative_path, occurrences, symbols, text(ignorado
  na correlação), position_encoding}
- Occurrence{range(deprec.), single_line_range, multi_line_range, symbol,
  symbol_roles, override_documentation, enclosing_range}
- SymbolInformation{symbol, documentation, relationships, kind,
  display_name, signature_documentation, enclosing_symbol}
- Relationship{symbol, is_reference, is_implementation,
  is_type_definition, is_definition}

Preservados-na-decodificação mas não semânticos hoje:
- syntax_kind, diagnostics (occurrence)
- quaisquer campos futuros: skipados por wire-type (forward-compat)

Símbolos ``local N`` permanecem locais — nunca promovidos a identidade
global.
"""

from code_atlas.resolvers.scip.decoder import DecodeError
from code_atlas.resolvers.scip.importer import (
    ScipDocument,
    ScipIndex,
    ScipOccurrence,
    ScipRange,
    ScipSymbolInfo,
    parse_index,
)
from code_atlas.resolvers.scip.loader import load_index
from code_atlas.resolvers.scip.resolver import RESOLVER_ID, ScipResolver

__all__ = [
    "RESOLVER_ID",
    "DecodeError",
    "ScipDocument",
    "ScipIndex",
    "ScipOccurrence",
    "ScipRange",
    "ScipResolver",
    "ScipSymbolInfo",
    "load_index",
    "parse_index",
]
