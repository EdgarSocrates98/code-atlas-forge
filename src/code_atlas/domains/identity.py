"""Identidade canônica de entidades de domínio (lf-0110, Cycle 7).

Regra inegociável: duas entidades com o mesmo nome em escopos distintos
NÃO podem colidir — duas tabelas ``orders`` em catálogos diferentes, dois
``aws_s3_bucket.logs`` em contas/regiões distintas têm identidades
diferentes. A chave canônica é posicional e **escapada por segmento**
(percent-encoding), portanto separadores dentro de um componente
(``:``, ``/``, ``.``) nunca se confundem com o separador estrutural.

Componentes, em ordem fixa:

1. ``domain`` — id do domínio do adapter (``sql``, ``terraform``, …);
2. ``repository`` — ``identity_key`` lógica do repositório (lf-0021);
3. ``root`` — sub-raiz dentro do repo/monorepo (``""`` = raiz);
4. ``catalog`` — catálogo/conta/região/projeto que qualifica o
   namespace (ex.: ``glue:123456789012:us-east-1``, ``aws:…:eu-west-1``);
5. ``namespace`` — prefixo intermediário (schema, database, module);
6. ``environment`` — qualificador opcional (``dev``/``prod``); ``""``
   quando o adapter não distingue ambientes;
7. ``qualified_name`` — nome qualificado definido pelo adapter;
8. ``snapshot`` — escopo opcional de revisão (commit/digest); ``""``
   para identidade **lógica** (vale por repositório, como ``symbols``).

``identity_key`` persistida usa sempre a forma lógica (``snapshot=""``);
a ocorrência por snapshot é quem carrega o vínculo temporal — mesmo
desenho de ``symbols``/``symbol_occurrences``.
"""

from __future__ import annotations

from dataclasses import dataclass

_SCHEME = "dom"
_SEP = "/"

# Percent-encoding próprio: ``urllib.parse`` é proibido fora de ``api/``
# (auditoria de rede lf-0086). Safe-set = RFC 3986 unreserved.
_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-~"
)


def _enc(part: str) -> str:
    """Escapa um segmento — todo byte fora do safe-set vira ``%XX``.

    Cobre ``:``, ``/``, ``.``, espaço e UTF-8 multibyte: separadores
    estruturais nunca se confundem com conteúdo do componente.
    """
    out: list[str] = []
    for b in part.encode("utf-8"):
        ch = chr(b)
        out.append(ch if ch in _SAFE else f"%{b:02X}")
    return "".join(out)


def _dec(part: str) -> str:
    """Inverso de ``_enc`` — ``%XX`` malformado é literal, nunca crash."""
    out = bytearray()
    i, n = 0, len(part)
    while i < n:
        ch = part[i]
        if ch == "%" and i + 3 <= n:
            try:
                out.append(int(part[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8")


@dataclass(frozen=True)
class DomainIdentity:
    """Identidade canônica completa de uma entidade de domínio.

    Campos são ``strip()``ados em ``__post_init__``; capitalização é
    **preservada** (política de case é do adapter — identificadores SQL
    quoted são case-sensitive e adapters não devem fundir nomes reais
    distintos).
    """

    domain: str           # id do domínio produtor (adapter.domain_id)
    repository: str       # identity_key lógica do repositório (lf-0021)
    qualified_name: str   # nome qualificado definido pelo adapter
    root: str = ""        # sub-raiz no monorepo; "" = raiz do repo
    catalog: str = ""     # catálogo/conta/região (qualificador superior)
    namespace: str = ""   # schema/database/módulo (qualificador inferior)
    environment: str = "" # qualificador opcional de ambiente
    snapshot: str = ""    # escopo de revisão; "" = identidade lógica

    def __post_init__(self) -> None:
        for f in ("domain", "repository", "qualified_name", "root",
                  "catalog", "namespace", "environment", "snapshot"):
            object.__setattr__(self, f, getattr(self, f).strip())
        for f in ("domain", "repository", "qualified_name"):
            if not getattr(self, f):
                raise ValueError(f"domain identity requires {f}")

    def _segments(self, *, include_snapshot: bool) -> tuple[str, ...]:
        seg = (
            self.domain, self.repository, self.root, self.catalog,
            self.namespace, self.environment, self.qualified_name,
        )
        if include_snapshot:
            return (*seg, self.snapshot)
        return seg

    def canonical_key(self) -> str:
        """Chave canônica completa (inclui ``snapshot`` quando presente).

        Formato posicional ``dom:<seg>/<seg>/…`` com cada segmento
        percent-encodado — colisão só se **todos** os componentes forem
        idênticos.
        """
        segs = "/".join(_enc(s) for s in self._segments(include_snapshot=True))
        return f"{_SCHEME}:{segs}"

    def logical_key(self) -> str:
        """Chave lógica (sem ``snapshot``) — identidade por repositório."""
        segs = "/".join(_enc(s) for s in self._segments(include_snapshot=False))
        return f"{_SCHEME}:{segs}"

    def logical(self) -> DomainIdentity:
        """Identidade lógica equivalente (``snapshot=""``)."""
        if not self.snapshot:
            return self
        return DomainIdentity(
            domain=self.domain, repository=self.repository,
            qualified_name=self.qualified_name, root=self.root,
            catalog=self.catalog, namespace=self.namespace,
            environment=self.environment,
        )

    def display(self) -> str:
        """Forma legível para humanos (não usada como chave)."""
        head = ".".join(p for p in (self.catalog, self.namespace) if p)
        qname = f"{head}.{self.qualified_name}" if head else self.qualified_name
        env = f"@{self.environment}" if self.environment else ""
        return f"{self.domain}:{qname}{env}"

    @staticmethod
    def parse_key(key: str) -> tuple[str, ...] | None:
        """Decodifica os segmentos de uma chave ``dom:…``; None se inválida."""
        if not key.startswith(f"{_SCHEME}:"):
            return None
        body = key[len(_SCHEME) + 1:]
        return tuple(_dec(seg) for seg in body.split(_SEP))
