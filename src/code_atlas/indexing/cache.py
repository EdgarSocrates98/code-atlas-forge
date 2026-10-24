"""Cache versionado de ``ParseResult`` por arquivo (lf-0020).

Store: tabela ``parse_result_cache`` (schema v4) — **mesmo banco** do
índice, escolhido porque (a) a transação de publicação já cobre o cache
(rollback de build falho não deixa entradas órfãs novas), (b) zero
infraestrutura extra, (c) o cache nunca pertence a snapshots — GC não
pode corromper linhas vivas.

Chave de validade completa (grill): ``(repo_id, path, content_sha256,
language, parser_version, grammar_version, extractor_version,
resolver_version, builder_version, config_digest)`` — nunca por path
sozinho. Valor: ``ParseResult`` serializado em JSON estruturado (sem
pickle — entrada é disco não-confiável). ``payload_sha256`` verifica
integridade na leitura: blob corrompido → miss, nunca crash.

GC: ``gc`` remove entradas cujo ``last_used_snapshot_id`` é anterior ao
N-ésimo READY mais recente do repo, e aplica cap ``MAX_ENTRIES`` por
repo (eviction por ``last_used_snapshot_id`` mais antigo). Roda dentro
da transação de publicação → atômica com o build.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from code_atlas.parsers.contracts import (
    ExtractedReference,
    ExtractedRelation,
    ExtractedSymbol,
    ParseDiagnostic,
    ParseResult,
    SourceSpan,
)

# Versões de contrato — bump quando a semântica muda (invalida o cache).
RESOLVER_VERSION = "syntactic/1.0.0"
BUILDER_VERSION = "graph-builder/1.0.0"
CACHE_FORMAT_VERSION = 1

# Política de retenção (bounded cache): entradas não usadas pelos
# últimos ``KEEP_SNAPSHOTS`` READY são removidas; cap global por repo.
KEEP_SNAPSHOTS = 5
MAX_ENTRIES = 50_000


@dataclass(frozen=True)
class CacheKey:
    """Chave de validade completa — qualquer componente novo invalida."""

    path: str
    content_sha256: str
    language: str
    parser_version: str
    grammar_version: str
    extractor_version: str
    config_digest: str


# --- codec ParseResult <-> JSON (estruturado, sem pickle) ---------------


def _span_to_json(s: SourceSpan | None) -> list[int] | None:
    if s is None:
        return None
    return [s.start_byte, s.end_byte, s.start_line, s.start_col,
            s.end_line, s.end_col]


def _span_from_json(v: Any) -> SourceSpan | None:
    if v is None:
        return None
    return SourceSpan(int(v[0]), int(v[1]), int(v[2]), int(v[3]),
                      int(v[4]), int(v[5]))


def result_to_payload(res: ParseResult) -> str:
    """Serializa ParseResult em JSON canônico (chaves ordenadas)."""
    doc = {
        "format": CACHE_FORMAT_VERSION,
        "path": res.path,
        "language": res.language,
        "parser": res.parser,
        "parser_version": res.parser_version,
        "grammar_version": res.grammar_version,
        "content_hash": res.content_hash,
        "has_error": res.has_error,
        "error_node_count": res.error_node_count,
        "missing_node_count": res.missing_node_count,
        "node_count": res.node_count,
        "supported": res.supported,
        "diagnostics": [
            {
                "code": d.code, "severity": d.severity,
                "message": d.message, "span": _span_to_json(d.span),
            }
            for d in res.diagnostics
        ],
        "symbols": [
            {
                "language": s.language, "kind": s.kind, "name": s.name,
                "qualified_name": s.qualified_name,
                "span": _span_to_json(s.span),
                "name_span": _span_to_json(s.name_span),
                "scope_path": list(s.scope_path),
                "signature": s.signature, "params": list(s.params),
                "docstring": s.docstring, "decorators": list(s.decorators),
                "bases": list(s.bases), "implements": list(s.implements),
                "modifiers": list(s.modifiers),
            }
            for s in res.symbols
        ],
        "references": [
            {
                "name": r.name, "context": r.context,
                "span": _span_to_json(r.span),
                "scope_path": list(r.scope_path), "target": r.target,
            }
            for r in res.references
        ],
        "relations": [
            {
                "type": r.type, "from_name": r.from_name,
                "to_name": r.to_name, "span": _span_to_json(r.span),
                "confidence_class": r.confidence_class,
                "resolution_status": r.resolution_status,
            }
            for r in res.relations
        ],
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def result_from_payload(payload: str) -> ParseResult:
    """Desserializa; levanta ValueError em formato/versão inválidos."""
    doc = json.loads(payload)
    if not isinstance(doc, dict) or doc.get("format") != CACHE_FORMAT_VERSION:
        raise ValueError("unsupported cache payload format")
    return ParseResult(
        path=str(doc["path"]),
        language=str(doc["language"]),
        parser=str(doc["parser"]),
        parser_version=str(doc["parser_version"]),
        grammar_version=str(doc["grammar_version"]),
        content_hash=str(doc["content_hash"]),
        has_error=bool(doc["has_error"]),
        error_node_count=int(doc["error_node_count"]),
        missing_node_count=int(doc["missing_node_count"]),
        node_count=int(doc["node_count"]),
        supported=bool(doc["supported"]),
        diagnostics=tuple(
            ParseDiagnostic(
                code=str(d["code"]), severity=str(d["severity"]),
                message=str(d["message"]),
                span=_span_from_json(d["span"]),
            )
            for d in doc["diagnostics"]
        ),
        symbols=tuple(
            ExtractedSymbol(
                language=str(s["language"]), kind=str(s["kind"]),
                name=str(s["name"]),
                qualified_name=str(s["qualified_name"]),
                span=_span_from_json(s["span"]) or _ZERO_SPAN,
                name_span=_span_from_json(s["name_span"]),
                scope_path=tuple(str(x) for x in s["scope_path"]),
                signature=s["signature"],
                params=tuple(str(x) for x in s["params"]),
                docstring=s["docstring"],
                decorators=tuple(str(x) for x in s["decorators"]),
                bases=tuple(str(x) for x in s["bases"]),
                implements=tuple(str(x) for x in s["implements"]),
                modifiers=tuple(str(x) for x in s["modifiers"]),
            )
            for s in doc["symbols"]
        ),
        references=tuple(
            ExtractedReference(
                name=str(r["name"]), context=str(r["context"]),
                span=_span_from_json(r["span"]) or _ZERO_SPAN,
                scope_path=tuple(str(x) for x in r["scope_path"]),
                target=str(r["target"]),
            )
            for r in doc["references"]
        ),
        relations=tuple(
            ExtractedRelation(
                type=str(r["type"]), from_name=str(r["from_name"]),
                to_name=str(r["to_name"]),
                span=_span_from_json(r["span"]) or _ZERO_SPAN,
                confidence_class=str(r["confidence_class"]),
                resolution_status=str(r["resolution_status"]),
            )
            for r in doc["relations"]
        ),
    )


_ZERO_SPAN = SourceSpan(0, 0, 0, 0, 0, 0)
_BATCH_CHUNK = 400  # bem abaixo do limite de variáveis do SQLite (999)


class ParseResultCache:
    """Store versionado de ParseResult — leitura nunca executa nada."""

    def __init__(self, conn: sqlite3.Connection, repo_id: int) -> None:
        self._conn = conn
        self._repo_id = repo_id

    def _key_params(self, key: CacheKey) -> tuple[object, ...]:
        return (
            self._repo_id, key.path, key.content_sha256, key.language,
            key.parser_version, key.grammar_version, key.extractor_version,
            RESOLVER_VERSION, BUILDER_VERSION, key.config_digest,
        )

    def get(self, key: CacheKey) -> ParseResult | None:
        """Retorna o resultado cacheado ou ``None`` (miss/inválido).

        Leitura pura — sem writes, pode rodar fora de transação sob WAL.
        Integridade: ``payload_sha256`` precisa bater — blob corrompido
        ou formato desconhecido → miss honesto, nunca exceção.
        ``mark_used`` (chamado na transação de publicação) registra o
        hit e o ``last_used_snapshot_id`` para o GC.
        """
        row = self._conn.execute(
            """
            SELECT id, payload, payload_sha256 FROM parse_result_cache
             WHERE repository_id = ? AND path = ? AND content_sha256 = ?
               AND language = ? AND parser_version = ?
               AND grammar_version = ? AND extractor_version = ?
               AND resolver_version = ? AND builder_version = ?
               AND config_digest = ?
            """,
            self._key_params(key),
        ).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row["payload"].encode()).hexdigest() != (
            row["payload_sha256"]
        ):
            return None  # blob corrompido → miss
        try:
            return result_from_payload(row["payload"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def get_many(
        self, keys: list[CacheKey]
    ) -> dict[str, ParseResult]:
        """Lookup batchado — um ``IN`` por chunk de paths em vez de um
        SELECT por arquivo (lf-0031: 2019 round-trips → ~5 num corpus
        2k). Mesmas garantias de ``get``: payload_sha256 verificado,
        blob inválido/corrompido → ausência (miss honesto)."""
        wanted: dict[str, CacheKey] = {k.path: k for k in keys}
        out: dict[str, ParseResult] = {}
        paths = sorted(wanted)
        for i in range(0, len(paths), _BATCH_CHUNK):
            chunk = paths[i:i + _BATCH_CHUNK]
            # SQL constante (auditoria AST): a lista de paths entra como
            # UM parâmetro JSON via json_each — sem placeholders
            # dinâmicos e sem interpolar dados na query.
            rows = self._conn.execute(
                """
                SELECT path, content_sha256, language, parser_version,
                       grammar_version, extractor_version,
                       resolver_version, builder_version, config_digest,
                       payload, payload_sha256
                  FROM parse_result_cache
                 WHERE repository_id = ?
                   AND path IN (SELECT value FROM json_each(?))
                """,
                (self._repo_id, json.dumps(chunk)),
            ).fetchall()
            for row in rows:
                res = self._row_result(wanted, row)
                if res is not None:
                    out[row["path"]] = res
        return out

    def _row_result(
        self, wanted: dict[str, CacheKey], row: sqlite3.Row
    ) -> ParseResult | None:
        """Valida a linha contra a CacheKey exata + integridade do blob."""
        key = wanted.get(row["path"])
        if key is None:
            return None
        params = self._key_params(key)
        row_key = (
            self._repo_id, row["path"], row["content_sha256"],
            row["language"], row["parser_version"],
            row["grammar_version"], row["extractor_version"],
            row["resolver_version"], row["builder_version"],
            row["config_digest"],
        )
        if row_key != params:
            return None  # chave de validade diverge → miss
        if hashlib.sha256(row["payload"].encode()).hexdigest() != (
            row["payload_sha256"]
        ):
            return None
        try:
            return result_from_payload(row["payload"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def put(
        self,
        key: CacheKey,
        res: ParseResult,
        *,
        snapshot_id: int | None = None,
    ) -> None:
        """Insere/substitui o resultado na chave de validade."""
        payload = result_to_payload(res)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self._conn.execute(
            """
            INSERT INTO parse_result_cache (
                repository_id, path, content_sha256, language,
                parser_version, grammar_version, extractor_version,
                resolver_version, builder_version, config_digest,
                payload, payload_sha256, last_used_snapshot_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (repository_id, path, content_sha256, language,
                         parser_version, grammar_version,
                         extractor_version, resolver_version,
                         builder_version, config_digest)
            DO UPDATE SET payload = excluded.payload,
                          payload_sha256 = excluded.payload_sha256,
                          last_used_snapshot_id =
                              excluded.last_used_snapshot_id
            """,
            (*self._key_params(key), payload, digest, snapshot_id),
        )

    def mark_used(self, key: CacheKey, snapshot_id: int) -> None:
        """Marca a entrada como usada pelo snapshot (para o GC)."""
        self._conn.execute(
            "UPDATE parse_result_cache SET last_used_snapshot_id = ?,"
            " hit_count = hit_count + 1 "
            "WHERE repository_id = ? AND path = ? AND content_sha256 = ?"
            " AND language = ? AND parser_version = ?"
            " AND grammar_version = ? AND extractor_version = ?"
            " AND resolver_version = ? AND builder_version = ?"
            " AND config_digest = ?",
            (snapshot_id, *self._key_params(key)),
        )

    def gc(
        self,
        repo_id: int,
        *,
        keep_snapshots: int = KEEP_SNAPSHOTS,
        max_entries: int = MAX_ENTRIES,
    ) -> int:
        """Remove entradas frias; retorna quantas foram removidas.

        Política: entradas cujo ``last_used_snapshot_id`` é anterior ao
        ``keep_snapshots``-ésimo READY mais recente são removidas; além
        disso o repo fica limitado a ``max_entries`` linhas (eviction
        das menos recentemente usadas). Linhas de cache nunca pertencem
        a snapshots — não há risco de remover dado vivo.
        """
        recent = self._conn.execute(
            "SELECT id FROM snapshots WHERE repository_id = ?"
            " AND status = 'READY' ORDER BY id DESC LIMIT ?",
            (repo_id, keep_snapshots),
        ).fetchall()
        floor = min((int(r["id"]) for r in recent), default=0)
        cur = self._conn.execute(
            "DELETE FROM parse_result_cache WHERE repository_id = ?"
            " AND COALESCE(last_used_snapshot_id, 0) < ?",
            (repo_id, floor),
        )
        removed = cur.rowcount if cur.rowcount else 0
        # cap global por repo — evict LRU (menor last_used_snapshot_id)
        over = self._conn.execute(
            "SELECT COUNT(*) AS c FROM parse_result_cache"
            " WHERE repository_id = ?",
            (repo_id,),
        ).fetchone()["c"] - max_entries
        if over > 0:
            cur2 = self._conn.execute(
                "DELETE FROM parse_result_cache WHERE id IN ("
                "  SELECT id FROM parse_result_cache"
                "  WHERE repository_id = ?"
                "  ORDER BY COALESCE(last_used_snapshot_id, 0), id"
                "  LIMIT ?)",
                (repo_id, over),
            )
            removed += cur2.rowcount if cur2.rowcount else 0
        return int(removed)
