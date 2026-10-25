"""Varredura de referências + sanitização/Redaction (lf-0115).

Regras de segurança (inespeciáveis):

- **segredos nunca saem**: qualquer valor cuja chave case com
  ``password|secret|token|key|credential`` (case-insensitive) é
  substituído por ``[REDACTED]`` — o valor bruto nunca entra em
  entidades, atributos, evidência ou ``to_unresolved``;
- **interpolação não é valor**: todo ``${…}`` vira o marcador
  ``${dynamic}`` — nunca avaliamos nem copiamos a expressão interna
  para metadados persistidos;
- **count/for_each não expandem**: multiplicidade declarada é
  registrada como ``dynamic`` — nunca fabricamos instâncias;
- referências ``var.``/``local.``/``each.``/``count.``/``self.``/
  ``path.``/``terraform.`` não viram arestas (não são entidades de
  domínio).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Chaves sensíveis — spec lf-0115: password|secret|token|key|credential.
SECRET_KEY_RE = re.compile(
    r"password|secret|token|key|credential", re.IGNORECASE
)

REDACTED = "[REDACTED]"
DYNAMIC = "${dynamic}"

# ``aws_s3_bucket.logs`` — primeiro segmento DEVE conter ``_`` (tipos de
# recurso sempre têm provider_nome); lookbehind impede match em
# ``var.foo_bar.x``, ``local.a_b`` e dentro de ``module.x.aws_y_z``.
RESOURCE_REF_RE = re.compile(
    r"(?<![.\w])([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)"
    r"\.([A-Za-z0-9_-]+)(?![\w-]*\()"
)

# ``data.aws_x.y`` — fontes de dados (leitura, não managed).
DATA_REF_RE = re.compile(
    r"(?<![.\w])data\.([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)"
    r"\.([A-Za-z0-9_-]+)"
)

# ``module.name.attr`` — referência a output de módulo.
MODULE_REF_RE = re.compile(
    r"(?<![.\w])module\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)"
)

# ARNs literais — recursos externos ao snapshot (REFERENCES external).
ARN_RE = re.compile(r"arn:[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[^\s\"'\\)]+")

# ``${…}`` superficial — sanitização de valores antes de armazenar.
_INTERP_START_RE = re.compile(r"\$\{")


def is_sensitive_name(name: str) -> bool:
    """True se a chave/nome casa o padrão de segredos (spec)."""
    return bool(SECRET_KEY_RE.search(name))


def sanitize_value(text: str, *, max_len: int = 200) -> str:
    """Substitui ``${…}`` balanceados por ``${dynamic}`` e trunca.

    Nunca avalia a expressão — o conteúdo interpolado é descartado e
    substituído pelo marcador dinâmico (spec: "dynamic marker, not
    concrete value").
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        m = _INTERP_START_RE.search(text, i)
        if m is None:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        depth = 1
        j = m.end()
        while j < n and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        out.append(DYNAMIC)
        i = j
    s = "".join(out).strip()
    return s[:max_len]


def redact_if_sensitive(key: str, value: str) -> str:
    """``value`` sanitizado, ou ``[REDACTED]`` se ``key`` é sensível."""
    if is_sensitive_name(key):
        return REDACTED
    return sanitize_value(value)


@dataclass(frozen=True)
class RefMatch:
    """Referência encontrada num texto — offset em bytes do arquivo."""

    kind: str          # resource | data | module | arn
    type_name: str     # "aws_s3_bucket" | "module" | "data" | "arn"
    name: str          # label do alvo ("logs") ou texto do ARN
    start: int         # byte offset do início do match
    end: int


def _byte_offsets(text: str, start_char: int, end_char: int) -> tuple[int, int]:
    """Char offsets → byte offsets UTF-8 (trechos curtos, barato)."""
    return (
        len(text[:start_char].encode("utf-8")),
        len(text[:end_char].encode("utf-8")),
    )


def scan_refs(text: str, *, base_byte: int = 0) -> list[RefMatch]:
    """Todas as referências terraform em ``text`` (ordem de posição).

    ``base_byte`` soma-se aos offsets quando ``text`` é um slice do
    arquivo (corpo de bloco). Inclui refs a recursos managed
    (``aws_x.y``), data sources (``data.aws_x.y``), saídas de módulo
    (``module.a.b``) e ARNs literais.
    """
    out: list[RefMatch] = []
    for m in DATA_REF_RE.finditer(text):
        s, e = _byte_offsets(text, m.start(), m.end())
        out.append(
            RefMatch("data", m.group(1), m.group(2), base_byte + s, base_byte + e)
        )
    for m in RESOURCE_REF_RE.finditer(text):
        s, e = _byte_offsets(text, m.start(), m.end())
        out.append(
            RefMatch(
                "resource", m.group(1), m.group(2),
                base_byte + s, base_byte + e,
            )
        )
    for m in MODULE_REF_RE.finditer(text):
        s, e = _byte_offsets(text, m.start(), m.end())
        out.append(
            RefMatch(
                "module", "module", m.group(1),
                base_byte + s, base_byte + e,
            )
        )
    for m in ARN_RE.finditer(text):
        s, e = _byte_offsets(text, m.start(), m.end())
        out.append(
            RefMatch("arn", "arn", m.group(0), base_byte + s, base_byte + e)
        )
    out.sort(key=lambda r: r.start)
    return out
