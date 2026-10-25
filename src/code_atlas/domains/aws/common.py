"""Helpers compartilhados do adapter ``domains.aws`` (lf-0113, Cycle 7 Ph.5).

Utilitários puros de texto/identidade — nenhuma I/O, nenhum SDK:

- ``parse_arn``/``ARN_RE``: ARNs literais (``arn:aws:iam::123:role/x``)
  carregam partição/serviço/região/conta **explicitamente** — só o que
  está escrito entra na identidade;
- ``aws_catalog``: segmento ``catalog`` ``aws:<service>:<account>[:<region>]``
  com segmentos ausentes honestos — conta/região desconhecidos NUNCA são
  preenchidos por convenção (duas tabelas homônimas em contas distintas
  não colidem; uma identidade sem segmento declarado mantém o vazio);
- ``parse_s3_uri``: ``s3://bucket/key`` estático; templates
  (``{}``/``${``) não são URIs — viram ``dynamic``;
- ``split_table_name``: convenção ``catalog[.schema].table`` idêntica à
  do adapter ``sql`` (3+ partes → catálogo = primeira; 2 → schema;
  1 → nome simples);
- redação de segredos: chaves ``password|secret|token|key|credential``
  nunca persistem valor (mesma política de ``domains.terraform.refs``);
- spans byte→``SourceSpan`` e nós ``ast`` → ``SourceSpan``.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from code_atlas.parsers.contracts import SourceSpan

if TYPE_CHECKING:  # ast só entra em pyfiles; tipos aqui evitam import cego
    import ast

# ARN AWS literal — família de regex coerente com terraform.refs.ARN_RE:
# `arn:<partition>:<service>:<region>:<account>:<resource>`; region e
# account podem ser vazios (ex.: IAM é global → ``arn:aws:iam::…``).
ARN_RE = re.compile(r"arn:[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[^\s\"'\\)>\],]+")

# Chaves sensíveis — política idêntica a terraform.refs (spec lf-0115):
# o valor nunca sai; a chave pode constar como atributo marcado.
SECRET_KEY_RE = re.compile(r"password|secret|token|key|credential", re.IGNORECASE)
REDACTED = "[REDACTED]"
DYNAMIC = "<dynamic>"

# Sufixos de meta-tabelas Iceberg endereçáveis como ``cat.db.t.<meta>``
# (spec: "snapshot ops when statically named").
ICEBERG_META = frozenset(
    {
        "snapshots",
        "history",
        "files",
        "manifests",
        "partitions",
        "refs",
        "entries",
        "all_files",
        "all_manifests",
        "all_entries",
        "all_data_files",
        "all_delete_files",
        "position_deletes",
        "metadata_log_entries",
    }
)

# Implementações de catálogo Spark que marcam uso Iceberg quando vistas
# em ``spark.sql.catalog.<name>`` / ``--conf`` — comparação substring.
_ICEBERG_HINT = "iceberg"


@dataclass(frozen=True)
class ArnParts:
    """ARN literal decomposto — todos os campos vieram do texto."""

    arn: str
    partition: str
    service: str
    region: str
    account: str
    resource: str


def parse_arn(text: str) -> ArnParts | None:
    """Decompõe ``text`` em ``ArnParts``; ``None`` se não é ARN válido."""
    m = ARN_RE.fullmatch(text.strip())
    if m is None:
        return None
    fields = text.strip().split(":", 5)
    if len(fields) != 6 or fields[0] != "arn":
        return None
    return ArnParts(
        arn=text.strip(),
        partition=fields[1],
        service=fields[2],
        region=fields[3],
        account=fields[4],
        resource=fields[5],
    )


def is_iam_role_arn(parts: ArnParts) -> bool:
    """``arn:aws:iam::<acct>:role/…`` — role, não policy/instance-profile."""
    return parts.service == "iam" and parts.resource.startswith("role/")


def aws_catalog(service: str = "", account: str = "", region: str = "") -> str:
    """Segmento ``catalog`` com coordenadas AWS **só quando explícitas**.

    Forma ``aws:<service>:<account>[:<region>]``; posições vazias do
    meio são preservadas (``aws:glue:123:`` nunca vira ``aws:glue``) e
    o todo vazio vira ``""`` — uma identidade sem coordenadas NÃO
    herda segmentos de convenção e não colide com identidades que os
    têm (chave posicional em ``DomainIdentity``).
    """
    if not (service or account or region):
        return ""
    parts = ["aws", service, account, region]
    while len(parts) > 1 and parts[-1] == "":
        parts.pop()
    return ":".join(parts)


def parse_s3_uri(uri: str) -> tuple[str, str] | None:
    """``s3://bucket/key`` estático → ``(bucket, key)``; ``None`` senão.

    ``{}``/``${``/espaços/caracteres de controle marcam template ou
    texto que apenas contém ``s3://`` — não é uma URI literal e nunca
    vira entidade.
    """
    s = uri.strip()
    if not s.lower().startswith("s3://"):
        return None
    if "${" in s or "{" in s or "}" in s:
        return None  # template/f-string — dinâmico, não literal
    body = s[5:]
    bucket, _, key = body.partition("/")
    if not bucket or bucket.startswith("-"):
        return None
    if any(ch in bucket for ch in " \t'\"\\<>:|?*"):
        return None
    if any(ch in key for ch in "'\"\\<>"):
        return None
    return bucket, key


def s3_uri(bucket: str, key: str) -> str:
    """Recompõe a URI normalizada (``s3://bucket`` sem ``/`` final)."""
    return f"s3://{bucket}/{key}".rstrip("/") if key else f"s3://{bucket}"


def split_table_name(written: str) -> tuple[str, str, str]:
    """``cat.db.t`` → ``(catalog, namespace, name)`` (convenção sql).

    Aspas/backticks por segmento são removidos para a identidade
    (``written_name`` original fica como atributo na entidade).
    """
    raw = [p for p in written.strip().split(".")]
    parts = [p.strip().strip('`"') for p in raw]
    parts = [p for p in parts if p]
    if len(parts) >= 3:
        return parts[0], ".".join(parts[1:-1]), parts[-1]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return "", "", parts[0] if parts else written.strip()


def is_iceberg_impl(conf_value: str) -> bool:
    """Valor de conf aponta para catálogo Iceberg (``…iceberg…``)."""
    return _ICEBERG_HINT in conf_value.lower()


def is_sensitive(key: str) -> bool:
    """True se a chave/nome casa o padrão de segredos."""
    return bool(SECRET_KEY_RE.search(key))


def sanitize_value(text: str, *, max_len: int = 200) -> str:
    """Mascara ``${…}``/``${{…}}``/``{…}``-template e trunca.

    Nunca avalia a expressão — o conteúdo interpolado é substituído por
    ``<dynamic>`` (mesma política de terraform.refs.sanitize_value).
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        m = re.search(r"\$\{", text[i:])
        if m is None:
            out.append(text[i:])
            break
        start = i + m.start()
        out.append(text[i:start])
        depth = 1
        j = start + 2
        while j < n and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        out.append(DYNAMIC)
        i = j
    return "".join(out).strip()[:max_len]


def redact_if_sensitive(key: str, value: str) -> str:
    """``[REDACTED]`` para chaves sensíveis; senão valor sanitizado."""
    if is_sensitive(key):
        return REDACTED
    return sanitize_value(value)


# -- spans ---------------------------------------------------------------


def line_starts(text: str) -> tuple[int, ...]:
    """Offsets de **byte UTF-8** do início de cada linha."""
    starts = [0]
    off = 0
    for ch in text:
        off += len(ch.encode("utf-8"))
        if ch == "\n":
            starts.append(off)
    return tuple(starts)


def byte_span(starts: tuple[int, ...], start: int, end: int) -> SourceSpan:
    """Offsets de byte absolutos do arquivo → ``SourceSpan``."""
    li = bisect.bisect_right(starts, start) - 1
    lj = bisect.bisect_right(starts, end) - 1
    return SourceSpan(
        start_byte=start,
        end_byte=end,
        start_line=li + 1,
        start_col=start - starts[li],
        end_line=lj + 1,
        end_col=end - starts[lj],
    )


def node_span(starts: tuple[int, ...], node: ast.AST) -> SourceSpan | None:
    """``SourceSpan`` de um nó ``ast`` (col_offset é byte-offset UTF-8)."""
    lineno = getattr(node, "lineno", None)
    col = getattr(node, "col_offset", None)
    end_lineno = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if (
        lineno is None
        or col is None
        or end_lineno is None
        or end_col is None
        or lineno < 1
        or end_lineno < 1
        or lineno > len(starts)
        or end_lineno > len(starts)
    ):
        return None
    return byte_span(
        starts,
        starts[lineno - 1] + col,
        starts[end_lineno - 1] + end_col,
    )
