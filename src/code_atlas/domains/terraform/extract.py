"""Núcleo de extração do adapter Terraform (lf-0115, Phase 7).

Fluxo: artefatos ``.tf``/``.tf.json``/``.tfvars``(.json) do request →
parse estrutural → entidades ``TERRAFORM_RESOURCE``/``TERRAFORM_MODULE``/
``IAM_ROLE`` + relações ``DEPENDS_ON``/``PROVISIONS``/``USES_RESOURCE``/
``ASSUMES_ROLE``/``REFERENCES``.

Limites declarados (evidence-first, sem execução):

- identidade de recurso = ``type.name`` + module path (``namespace``)
  + ``environment`` quando estaticamente conhecido
  (``request.environment`` ou default literal de variável de ambiente);
- ``depends_on`` explícito + refs implícitas ``tipo.nome`` →
  ``DEPENDS_ON``; refs dentro de atributos de documento (policy,
  definition, statement…) → ``USES_RESOURCE``; refs ``data.tipo.nome``
  → ``REFERENCES``; leituras ``terraform_remote_state``, ``backend`` e
  ``module`` source remoto → ``REFERENCES`` com resolução ``external``;
- ``count``/``for_each`` → ``multiplicity=dynamic`` declarado — nenhuma
  expansão fabricada;
- defaults/atribuições com chave casando ``password|secret|token|key|
  credential`` nunca saem — registrados como ``[REDACTED]``;
- ``${…}`` em qualquer valor persistido → ``${dynamic}``.
"""

from __future__ import annotations

import bisect
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import (
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import domain_extractor_id, evidence_record
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.domains.terraform.catalog import provider_of, resource_class
from code_atlas.domains.terraform.hcl import HclBlock, HclFile, parse_hcl
from code_atlas.domains.terraform.refs import (
    RefMatch,
    is_sensitive_name,
    sanitize_value,
    scan_refs,
)
from code_atlas.domains.terraform.tfjson import parse_tfjson
from code_atlas.domains.terraform.tfvars import parse_tfvars, parse_tfvars_json
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

DOMAIN_ID = "terraform"

# Atributos cujo conteúdo é documento/payload — refs dentro deles são
# uso semântico do recurso (ARN em policy/definition), não wiring de
# dependência direta.
_DOC_ATTRS = frozenset(
    {
        "policy", "policy_document", "assume_role_policy", "definition",
        "document", "inline_policy", "statement", "statements", "json",
        "template", "body", "user_data", "container_definitions", "input",
        "resources", "actions", "principals", "identifiers",
        "managed_policy_arns", "policy_arns", "values",
    }
)

# Atributos que carregam uma role — ref a ``aws_iam_role`` nesses attrs
# é ``ASSUMES_ROLE`` (o recurso opera sob aquela identidade).
_ROLE_ATTRS = frozenset(
    {
        "role", "role_arn", "service_role", "service_role_arn",
        "task_role", "task_role_arn", "execution_role_arn",
        "job_role_arn", "instance_role", "instance_profile",
        "delivery_stream_role_arn", "crawler_role", "role_name",
        "service_linked_role_arn",
    }
)

# Attachments vinculam policy↔role — não é "assumir" a role.
_ROLE_BINDING_TYPES = frozenset(
    {"aws_iam_role_policy_attachment", "aws_iam_policy_attachment"}
)

# Variáveis que qualificam ambiente quando o default é literal estático.
_ENV_VAR_NAMES = frozenset({"environment", "env", "stage", "workspace"})

# Princípios de serviço literais em trust policies ("Service": "x").
_SERVICE_PRINCIPAL_RE = re.compile(
    r'"?Service"?\s*[:=]\s*(?:"([^"\[\]]+?)"|\[\s*"([^"\[\]]+?)")'
)

_MAX_LISTED = 40
_MAX_DEPTH = 8


@dataclass(frozen=True)
class _Parsed:
    """Artefato parseado — conteúdo lido somente via ``base_path``."""

    path: str                 # rel POSIX
    directory: str            # dir POSIX rel ("" = raiz do recorte)
    sha256: str
    kind: str                 # tf | tfjson | tfvars
    parsed: HclFile
    tfvars: tuple[tuple[str, str, bool], ...] = ()  # (nome, valor, redigido)
    line_starts: tuple[int, ...] = ()               # byte offsets de "\n"


def _line_starts(text: str) -> tuple[int, ...]:
    """Byte offsets do início de cada linha (para spans de refs)."""
    starts = [0]
    off = 0
    for ch in text:
        off += len(ch.encode("utf-8"))
        if ch == "\n":
            starts.append(off)
    return tuple(starts)


def _byte_span(
    line_starts: tuple[int, ...], start: int, end: int
) -> SourceSpan:
    """Byte offsets → ``SourceSpan`` com linha/col calculados."""
    li = bisect.bisect_right(line_starts, start) - 1
    lj = bisect.bisect_right(line_starts, end) - 1
    return SourceSpan(
        start_byte=start, end_byte=end,
        start_line=li + 1, start_col=start - line_starts[li],
        end_line=lj + 1, end_col=end - line_starts[lj],
    )


@dataclass
class _Builder:
    """Acumulador do resultado — evidência/entidades/relações/diags."""

    request: DomainExtractionRequest
    adapter_version: str
    evidence: list[EvidenceRecord] = field(default_factory=list)
    _evi_index: dict[tuple[str, tuple[int, ...] | None], int] = field(
        default_factory=dict
    )
    entities: list[ExtractedDomainEntity] = field(default_factory=list)
    relations: list[ExtractedDomainRelation] = field(default_factory=list)
    diagnostics: list[DomainDiagnostic] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    id_by_addr: dict[tuple[str, str], DomainIdentity] = field(
        default_factory=dict
    )
    mod_addr: dict[str, DomainIdentity] = field(default_factory=dict)

    def evi(self, path: str, sha: str, span: SourceSpan | None) -> int:
        """Índice de evidência dedupado por (path, span)."""
        key = (
            path,
            None
            if span is None
            else (
                span.start_byte, span.end_byte, span.start_line,
                span.start_col, span.end_line, span.end_col,
            ),
        )
        idx = self._evi_index.get(key)
        if idx is not None:
            return idx
        self.evidence.append(
            evidence_record(
                adapter_domain=DOMAIN_ID,
                adapter_version=self.adapter_version,
                path=path, content_hash=sha, span=span,
            )
        )
        idx = len(self.evidence) - 1
        self._evi_index[key] = idx
        return idx

    def diag(
        self, code: str, severity: str, message: str, path: str = ""
    ) -> None:
        self.diagnostics.append(
            DomainDiagnostic(
                code=code, severity=severity, message=message,
                path=path, adapter_id=DOMAIN_ID,
            )
        )

    def ident(self, namespace: str, env: str, qname: str) -> DomainIdentity:
        return DomainIdentity(
            domain=DOMAIN_ID,
            repository=self.request.repository,
            root=self.request.root,
            namespace=namespace,
            environment=env,
            qualified_name=qname,
        )

    def _res(
        self,
        status: ResolutionStatus,
        *,
        target: DomainIdentity | None = None,
        unresolved_text: str = "",
    ) -> DomainResolution:
        return DomainResolution(
            status=status,
            confidence_class=ConfidenceClass.SYNTACTIC,
            resolver=domain_extractor_id(DOMAIN_ID),
            target=target,
            unresolved_text=unresolved_text,
        )

    def rel_to_entity(
        self,
        rtype: str,
        path: str,
        from_id: DomainIdentity,
        namespace: str,
        qname: str,
        span: SourceSpan | None,
        evi: tuple[int, ...],
    ) -> None:
        """Aresta para alvo interno — resolve contra entidades extraídas."""
        target = self.id_by_addr.get((namespace, qname))
        if target is not None:
            self.relations.append(
                ExtractedDomainRelation(
                    relation_type=rtype, path=path,
                    from_identity=from_id, to_identity=target,
                    resolution=self._res(
                        ResolutionStatus.RESOLVED, target=target,
                    ),
                    span=span, evidence=evi,
                )
            )
            return
        text = f"{namespace}.{qname}" if namespace else qname
        self.relations.append(
            ExtractedDomainRelation(
                relation_type=rtype, path=path,
                from_identity=from_id,
                to_unresolved=text,
                resolution=self._res(
                    ResolutionStatus.UNRESOLVED, unresolved_text=text,
                ),
                span=span, evidence=evi,
            )
        )
        self.stats["unresolved_targets"] = (
            self.stats.get("unresolved_targets", 0) + 1
        )

    def rel_external(
        self,
        rtype: str,
        path: str,
        from_id: DomainIdentity,
        text: str,
        span: SourceSpan | None,
        evi: tuple[int, ...],
    ) -> None:
        """Aresta para alvo fora do snapshot — ``external`` declarado."""
        clean = sanitize_value(text)
        self.relations.append(
            ExtractedDomainRelation(
                relation_type=rtype, path=path,
                from_identity=from_id,
                to_unresolved=clean,
                resolution=self._res(
                    ResolutionStatus.EXTERNAL, unresolved_text=clean,
                ),
                span=span, evidence=evi,
            )
        )
        self.stats["external_refs"] = self.stats.get("external_refs", 0) + 1


# ---------------------------------------------------------------------
# leitura e roteamento de artefatos
# ---------------------------------------------------------------------


def _artifact_kind(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".tfvars.json"):
        return "tfvars"
    if lower.endswith(".tf.json"):
        return "tfjson"
    if lower.endswith(".tfvars"):
        return "tfvars"
    if lower.endswith(".tf"):
        return "tf"
    return ""


def _read_artifacts(
    b: _Builder, base_path: Path | None
) -> list[_Parsed]:
    out: list[_Parsed] = []
    for art in b.request.artifacts:
        kind = _artifact_kind(art.path)
        if not kind:
            b.diag(
                "unsupported-artifact", "warning",
                f"artifact not handled by terraform adapter: {art.path}",
                art.path,
            )
            continue
        src = Path(art.path)
        fp = src if src.is_absolute() or base_path is None else base_path / src
        try:
            raw = fp.read_bytes()
        except OSError as exc:
            b.diag(
                "artifact-unreadable", "error",
                f"cannot read {art.path}: {exc}", art.path,
            )
            continue
        sha = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            b.diag(
                "encoding-warning", "warning",
                "non-UTF-8 bytes replaced during decode", art.path,
            )
        tfvars: tuple[tuple[str, str, bool], ...] = ()
        if kind == "tf":
            parsed = parse_hcl(text)
        elif kind == "tfjson":
            jf = parse_tfjson(text)
            parsed = HclFile(blocks=jf.blocks, diagnostics=jf.diagnostics)
        else:
            tv = (
                parse_tfvars_json(text)
                if art.path.lower().endswith(".json")
                else parse_tfvars(text)
            )
            parsed = HclFile(diagnostics=tv.diagnostics)
            tfvars = tuple((a.name, a.value, a.redacted) for a in tv.assignments)
        for d in parsed.diagnostics:
            b.diag(
                d.code,
                "error" if "unterminated" in d.code or "error" in d.code
                else "warning",
                f"{d.message} (line {d.line})" if d.line else d.message,
                art.path,
            )
        out.append(
            _Parsed(
                path=art.path, directory=posixpath.dirname(art.path),
                sha256=sha, kind=kind, parsed=parsed, tfvars=tfvars,
                line_starts=_line_starts(text),
            )
        )
        b.stats[f"{kind}_files"] = b.stats.get(f"{kind}_files", 0) + 1
    return out


# ---------------------------------------------------------------------
# módulos: caminhos de instanciação resolvidos estaticamente
# ---------------------------------------------------------------------


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    return v


def _norm_source(directory: str, source: str) -> str:
    """Source local (``./``/``../``) → dir POSIX normalizado; "" senão."""
    if not source.startswith(("./", "../")):
        return ""
    joined = posixpath.normpath(posixpath.join(directory, source))
    return "" if joined == "." else joined


@dataclass(frozen=True)
class _ModuleCall:
    name: str
    directory: str          # dir do arquivo que contém o bloco
    source: str             # valor bruto (unquoted)
    local_dir: str          # dir normalizado quando source é local, "" senão
    block: HclBlock
    parsed: _Parsed


def _module_calls(parsed: list[_Parsed]) -> list[_ModuleCall]:
    calls: list[_ModuleCall] = []
    for p in parsed:
        if p.kind not in ("tf", "tfjson"):
            continue
        for blk in p.parsed.blocks:
            if blk.type != "module" or not blk.labels:
                continue
            src_attr = blk.attribute("source")
            source = _unquote(src_attr.value) if src_attr else ""
            calls.append(
                _ModuleCall(
                    name=blk.labels[0], directory=p.directory,
                    source=source,
                    local_dir=_norm_source(p.directory, source),
                    block=blk, parsed=p,
                )
            )
    return calls


def _module_addresses(
    parsed: list[_Parsed], calls: list[_ModuleCall]
) -> dict[str, tuple[str, ...]]:
    """dir → endereços terraform estáticos ("" = root module).

    Fixpoint sobre chamadas com source local: ``module x {source="./m"}``
    num diretório com endereço ``a`` dá ao dir ``m`` o endereço
    ``a.module.x``. Dirs não alcançados → ``("unresolved:<dir>",)``.
    Um dir instanciado por N chamadas produz N endereços — nenhuma
    expansão é fabricada, os endereços são literais.
    """
    tf_dirs = sorted(
        {p.directory for p in parsed if p.kind in ("tf", "tfjson")}
    )
    targeted = {c.local_dir for c in calls if c.local_dir}
    candidates = [d for d in tf_dirs if d not in targeted]
    pool = candidates or tf_dirs
    root = min(pool, key=lambda d: (d.count("/"), d), default="")
    addrs: dict[str, set[str]] = {d: set() for d in tf_dirs}
    if root in addrs:
        addrs[root].add("")
    for _ in range(len(tf_dirs) + 2):
        grew = False
        for c in calls:
            if not c.local_dir or c.local_dir not in addrs:
                continue
            for base in sorted(addrs.get(c.directory, ())):
                addr = (
                    f"{base}.module.{c.name}" if base else f"module.{c.name}"
                )
                if addr not in addrs[c.local_dir]:
                    addrs[c.local_dir].add(addr)
                    grew = True
        if not grew:
            break
    return {
        d: tuple(sorted(v)) if v else (f"unresolved:{d}",)
        for d, v in addrs.items()
    }


def _dir_env(
    b: _Builder, parsed: list[_Parsed]
) -> dict[str, tuple[str, str]]:
    """dir → ``(environment, source)`` — env estático por diretório.

    ``request.environment`` vence sempre; depois, default literal de
    variável de ambiente (``environment|env|stage|workspace``).
    """
    env: dict[str, tuple[str, str]] = {}
    if b.request.environment:
        for p in parsed:
            env[p.directory] = (b.request.environment, "request")
        return env
    for p in parsed:
        if p.kind not in ("tf", "tfjson") or p.directory in env:
            continue
        for blk in p.parsed.blocks:
            raw = _env_var_default(blk)
            if raw is not None:
                env[p.directory] = (raw, "variable-default")
                break
    return env


def _env_var_default(blk: HclBlock) -> str | None:
    """Default literal de variável de ambiente — ``None`` se ausente
    ou não-literal (interpolação nunca é avaliada como valor)."""
    if blk.type != "variable" or not blk.labels:
        return None
    if blk.labels[0].lower() not in _ENV_VAR_NAMES:
        return None
    default = blk.attribute("default")
    if default is None:
        return None
    raw = _unquote(default.value)
    if "${" in raw or not raw or len(raw) > 64:
        return None
    if not all(ch.isalnum() or ch in "._-" for ch in raw):
        return None
    return raw


# ---------------------------------------------------------------------
# entidades
# ---------------------------------------------------------------------


def _multiplicity(block: HclBlock) -> str:
    """count/for_each → multiplicidade declarada, nunca expandida."""
    if (
        block.attribute("count") is not None
        or block.attribute("for_each") is not None
    ):
        return "dynamic"
    return "single"


def _trusted_services(block: HclBlock, _depth: int = 0) -> list[str]:
    """Principals ``Service:`` literais do trust policy — strings curtas."""
    found: list[str] = []
    for attr in block.attributes:
        if attr.name != "assume_role_policy":
            continue
        for m in _SERVICE_PRINCIPAL_RE.finditer(attr.value):
            svc = m.group(1) or m.group(2) or ""
            if svc and svc not in found:
                found.append(svc)
    if _depth < _MAX_DEPTH:
        for child in block.children:
            for svc in _trusted_services(child, _depth + 1):
                if svc not in found:
                    found.append(svc)
    return sorted(found)


def _resource_attrs(block: HclBlock, ns: str, env_src: str) -> dict[str, str]:
    rtype, rname = block.labels[0], block.labels[1]
    attrs = {
        "resource_type": rtype,
        "resource_name": rname,
        "resource_class": resource_class(rtype),
        "provider": provider_of(rtype),
        "module_path": ns,
        "multiplicity": _multiplicity(block),
        "mode": "managed",
    }
    if env_src:
        attrs["env_source"] = env_src
    return attrs


def _is_sensitive_var(blk: HclBlock) -> bool:
    """Variável sensível: nome casa o padrão de segredos ou
    ``sensitive = true`` declarado."""
    if is_sensitive_name(blk.labels[0]):
        return True
    sattr = blk.attribute("sensitive")
    return sattr is not None and _unquote(sattr.value) == "true"


def _scan_var_block(
    b: _Builder,
    blk: HclBlock,
    names: list[str],
    sensitive: list[str],
) -> str:
    """Um bloco ``variable``: registra nome/flag sensível e devolve
    env-default literal (``""`` quando sensível ou não-literal)."""
    vname = blk.labels[0]
    names.append(vname)
    if _is_sensitive_var(blk):
        sensitive.append(vname)
        b.stats["secrets_redacted"] = (
            b.stats.get("secrets_redacted", 0) + 1
        )
        return ""
    return _env_var_default(blk) or ""


def _attach_var_attrs(
    b: _Builder,
    parsed: list[_Parsed],
    directory: str,
    attrs: dict[str, str],
    env_hints: dict[str, list[str]],
) -> None:
    """Variáveis do módulo (dir): nomes + flags — defaults sensíveis
    nunca saem (``[REDACTED]``), interpolações viram ``${dynamic}``."""
    names: list[str] = []
    sensitive: list[str] = []
    env_default = ""
    for p in parsed:
        if p.directory != directory or p.kind not in ("tf", "tfjson"):
            continue
        for blk in p.parsed.blocks:
            if blk.type != "variable" or not blk.labels:
                continue
            default = _scan_var_block(b, blk, names, sensitive)
            if default:
                env_default = default
    _var_summary_attrs(
        attrs, names, sensitive, env_default, env_hints.get(directory)
    )


def _var_summary_attrs(
    attrs: dict[str, str],
    names: list[str],
    sensitive: list[str],
    env_default: str,
    hints: list[str] | None,
) -> None:
    """Grava no ``attrs`` do módulo o resumo das variáveis declaradas."""
    if names:
        shown = sorted(set(names))[:_MAX_LISTED]
        attrs["variables"] = ",".join(shown)
        if len(set(names)) > _MAX_LISTED:
            attrs["variables_truncated"] = "true"
    if sensitive:
        attrs["sensitive_variables"] = ",".join(sorted(set(sensitive)))
    if env_default:
        attrs["env_default"] = env_default
    if hints:
        attrs["tfvars_env_hints"] = ",".join(sorted(hints))


def _source_kind(source: str) -> str:
    if source.startswith(("./", "../")):
        return "local"
    if source.startswith(("git::", "github.com/")) or ".git" in source:
        return "git"
    if source.startswith(("s3::", "s3://")):
        return "s3"
    if source.startswith(("gcs::", "gs://")):
        return "gcs"
    if source.startswith(("http://", "https://")):
        return "http"
    return "registry"


def _module_attrs(
    block: HclBlock, ns: str, env_src: str, source: str
) -> dict[str, str]:
    attrs: dict[str, str] = {
        "module_path": ns,
        "multiplicity": _multiplicity(block),
    }
    if env_src:
        attrs["env_source"] = env_src
    if source:
        attrs["source"] = sanitize_value(source)
        attrs["source_kind"] = _source_kind(source)
    return attrs


def _emit_entities(
    b: _Builder,
    parsed: list[_Parsed],
    addresses: dict[str, tuple[str, ...]],
    env: dict[str, tuple[str, str]],
    env_hints: dict[str, list[str]],
) -> None:
    """Entidades de ``resource``/``data``/``module`` + módulo root."""
    for p in parsed:
        if p.kind not in ("tf", "tfjson"):
            continue
        p_env, p_src = env.get(p.directory, ("", ""))
        for ns in addresses.get(p.directory, (f"unresolved:{p.directory}",)):
            for blk in p.parsed.blocks:
                evi = b.evi(p.path, p.sha256, blk.header_span or blk.span)
                if blk.type == "resource" and len(blk.labels) >= 2:
                    _emit_resource(b, p, blk, ns, p_env, p_src, evi)
                elif blk.type == "data" and len(blk.labels) >= 2:
                    _emit_data(b, p, blk, ns, p_env, p_src, evi)
                elif blk.type == "module" and blk.labels:
                    _emit_module(
                        b, p, blk, ns, parsed, env, env_hints, evi
                    )
                elif blk.type in ("resource", "data", "module"):
                    b.diag(
                        "malformed-block", "error",
                        f"{blk.type} block missing labels", p.path,
                    )
    _emit_root_module(b, parsed, addresses, env, env_hints)


def _emit_resource(
    b: _Builder, p: _Parsed, blk: HclBlock, ns: str,
    env_val: str, env_src: str, evi: int,
) -> None:
    rtype, rname = blk.labels[0], blk.labels[1]
    kind = (
        DomainEntityKind.IAM_ROLE
        if rtype == "aws_iam_role"
        else DomainEntityKind.TERRAFORM_RESOURCE
    )
    qname = f"{rtype}.{rname}"
    ident = b.ident(ns, env_val, qname)
    attrs = _resource_attrs(blk, ns, env_src)
    if kind is DomainEntityKind.IAM_ROLE:
        svc = _trusted_services(blk)
        if svc:
            attrs["trusted_services"] = ",".join(svc)
    b.entities.append(
        ExtractedDomainEntity(
            identity=ident, kind=kind, name=qname,
            path=p.path, span=blk.span, content_hash=p.sha256,
            attributes=attrs, evidence=(evi,),
        )
    )
    b.id_by_addr[(ns, qname)] = ident
    key = "iam_roles" if kind is DomainEntityKind.IAM_ROLE else "resources"
    b.stats[key] = b.stats.get(key, 0) + 1


def _emit_data(
    b: _Builder, p: _Parsed, blk: HclBlock, ns: str,
    env_val: str, env_src: str, evi: int,
) -> None:
    rtype, rname = blk.labels[0], blk.labels[1]
    qname = f"data.{rtype}.{rname}"
    ident = b.ident(ns, env_val, qname)
    attrs = _resource_attrs(blk, ns, env_src)
    attrs["mode"] = "data"
    if rtype == "terraform_remote_state":
        attrs["remote_state"] = "true"
        backend = blk.attribute("backend")
        if backend is not None:
            attrs["backend"] = sanitize_value(_unquote(backend.value))
    b.entities.append(
        ExtractedDomainEntity(
            identity=ident, kind=DomainEntityKind.TERRAFORM_RESOURCE,
            name=qname, path=p.path, span=blk.span,
            content_hash=p.sha256, attributes=attrs, evidence=(evi,),
        )
    )
    b.id_by_addr[(ns, qname)] = ident
    b.stats["data_sources"] = b.stats.get("data_sources", 0) + 1


def _emit_module(
    b: _Builder, p: _Parsed, blk: HclBlock, ns: str,
    parsed: list[_Parsed],
    env: dict[str, tuple[str, str]],
    env_hints: dict[str, list[str]],
    evi: int,
) -> None:
    name = blk.labels[0]
    qname = f"{ns}.module.{name}" if ns else f"module.{name}"
    src_attr = blk.attribute("source")
    source = _unquote(src_attr.value) if src_attr else ""
    local_dir = _norm_source(p.directory, source)
    # env da entidade-módulo = env do dir alvo quando local
    env_key = local_dir if local_dir else p.directory
    env_val, env_src = env.get(env_key, env.get(p.directory, ("", "")))
    ident = b.ident(ns, env_val, qname)
    attrs = _module_attrs(blk, ns, env_src, source)
    if local_dir:
        _attach_var_attrs(b, parsed, local_dir, attrs, env_hints)
    b.entities.append(
        ExtractedDomainEntity(
            identity=ident, kind=DomainEntityKind.TERRAFORM_MODULE,
            name=qname, path=p.path, span=blk.span,
            content_hash=p.sha256, attributes=attrs, evidence=(evi,),
        )
    )
    b.id_by_addr[(ns, qname)] = ident
    b.mod_addr[qname] = ident
    b.stats["modules"] = b.stats.get("modules", 0) + 1


def _emit_root_module(
    b: _Builder,
    parsed: list[_Parsed],
    addresses: dict[str, tuple[str, ...]],
    env: dict[str, tuple[str, str]],
    env_hints: dict[str, list[str]],
) -> None:
    """Módulo root sintetizado — derivação declarada (``path=""``)."""
    root_dir = ""
    for d, addrs in addresses.items():
        if "" in addrs:
            root_dir = d
            break
    if not any(p.kind in ("tf", "tfjson") for p in parsed):
        return
    env_val, env_src = env.get(root_dir, ("", ""))
    ident = b.ident("", env_val, "module.root")
    attrs: dict[str, str] = {
        "module_role": "root", "module_path": "",
        "declared": "synthesized",
    }
    if env_src:
        attrs["env_source"] = env_src
    _attach_var_attrs(b, parsed, root_dir, attrs, env_hints)
    b.entities.append(
        ExtractedDomainEntity(
            identity=ident, kind=DomainEntityKind.TERRAFORM_MODULE,
            name="module.root", path="", attributes=attrs,
        )
    )
    b.id_by_addr[("", "module.root")] = ident
    b.mod_addr[""] = ident
    b.stats["modules"] = b.stats.get("modules", 0) + 1


# ---------------------------------------------------------------------
# relações
# ---------------------------------------------------------------------


def _attr_index(block: HclBlock) -> list[tuple[int, int, str]]:
    """``(start_byte, end_byte, attr_name)`` dos valores do bloco."""
    out: list[tuple[int, int, str]] = []

    def walk(blk: HclBlock, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for a in blk.attributes:
            out.append(
                (a.value_span.start_byte, a.value_span.end_byte, a.name)
            )
        for c in blk.children:
            walk(c, depth + 1)

    walk(block, 0)
    return out


def _enclosing_attr(spans: list[tuple[int, int, str]], offset: int) -> str:
    for s, e, name in spans:
        if s <= offset <= e:
            return name
    return ""


def _block_from_ids(
    b: _Builder,
    blk: HclBlock,
    ns_list: tuple[str, ...],
) -> list[DomainIdentity]:
    """Identidades do bloco em cada endereço do seu diretório."""
    out: list[DomainIdentity] = []
    for ns in ns_list:
        if blk.type == "module":
            qn = f"{ns}.module.{blk.labels[0]}" if ns else f"module.{blk.labels[0]}"
        else:
            prefix = "data." if blk.type == "data" else ""
            qn = f"{prefix}{blk.labels[0]}.{blk.labels[1]}"
        ident = b.id_by_addr.get((ns, qn))
        if ident is not None:
            out.append(ident)
    return out


def _role_chain_edge(
    b: _Builder,
    p: _Parsed,
    block: HclBlock,
    m: RefMatch,
    enclosing: str,
    from_id: DomainIdentity,
    span: SourceSpan | None,
    evi: tuple[int, ...],
    seen: set[tuple[DomainIdentity, str, str]],
) -> bool:
    """Trust chain: trust policy de R lista principal ``aws_iam_role.P``
    → aresta ``P ASSUMES_ROLE R``. ``True`` quando aplicado."""
    if not (
        block.type == "resource"
        and block.labels
        and block.labels[0] == "aws_iam_role"
        and enclosing == "assume_role_policy"
        and m.type_name == "aws_iam_role"
    ):
        return False
    ns = from_id.namespace
    target_qname = f"{m.type_name}.{m.name}"
    prin = b.id_by_addr.get((ns, target_qname))
    if prin is None:
        b.diag(
            "role-principal-unresolved", "info",
            f"trust policy principal {target_qname} not extracted "
            "in this snapshot", p.path,
        )
        return True
    key = (prin, str(from_id), "ASSUMES_ROLE")
    if key not in seen:
        seen.add(key)
        b.relations.append(
            ExtractedDomainRelation(
                relation_type=str(DomainRelationKind.ASSUMES_ROLE),
                path=p.path,
                from_identity=prin,
                to_identity=from_id,
                resolution=b._res(
                    ResolutionStatus.RESOLVED, target=from_id,
                ),
                span=span, evidence=evi,
            )
        )
    return True


def _managed_ref_kinds(
    block: HclBlock, m: RefMatch, explicit: bool, enclosing: str
) -> tuple[str, ...]:
    """Classifica ref a recurso managed: atributo de role →
    ``ASSUMES_ROLE`` + ``DEPENDS_ON``; doc/payload → ``USES_RESOURCE``;
    demais → ``DEPENDS_ON``."""
    if (
        not explicit
        and enclosing in _ROLE_ATTRS
        and m.type_name == "aws_iam_role"
        and block.labels
        and block.labels[0] not in _ROLE_BINDING_TYPES
    ):
        return (
            str(DomainRelationKind.ASSUMES_ROLE),
            str(DomainRelationKind.DEPENDS_ON),
        )
    if not explicit and enclosing in _DOC_ATTRS:
        return (str(DomainRelationKind.USES_RESOURCE),)
    return (str(DomainRelationKind.DEPENDS_ON),)


def _emit_one_ref(
    b: _Builder,
    p: _Parsed,
    block: HclBlock,
    m: RefMatch,
    explicit: bool,
    enclosing: str,
    from_id: DomainIdentity,
    seen: set[tuple[DomainIdentity, str, str]],
) -> None:
    """Uma referência → aresta(s) honestas a partir de ``from_id``."""
    ns = from_id.namespace
    # tf: match em offsets de arquivo reais; tfjson: offsets no body
    # serializado → evidência cai para o span do bloco.
    span = (
        _byte_span(p.line_starts, m.start, m.end)
        if p.kind == "tf"
        else block.span
    )
    evi = (b.evi(p.path, p.sha256, span or block.header_span),)
    if m.kind == "arn":
        key = (from_id, f"arn:{m.name}", "REFERENCES")
        if key not in seen:
            seen.add(key)
            b.rel_external(
                str(DomainRelationKind.REFERENCES), p.path, from_id,
                m.name, span, evi,
            )
        return
    if m.kind == "data":
        tqn = f"data.{m.type_name}.{m.name}"
        dtype = (
            "DEPENDS_ON" if explicit else str(DomainRelationKind.REFERENCES)
        )
        key = (from_id, f"{ns}.{tqn}", dtype)
        if key not in seen:
            seen.add(key)
            b.rel_to_entity(dtype, p.path, from_id, ns, tqn, span, evi)
        return
    if m.kind == "module":
        mqname = f"{ns}.module.{m.name}" if ns else f"module.{m.name}"
        key = (from_id, mqname, "DEPENDS_ON")
        if key not in seen:
            seen.add(key)
            b.rel_to_entity(
                str(DomainRelationKind.DEPENDS_ON), p.path, from_id,
                ns, mqname, span, evi,
            )
        return
    # ref a recurso managed
    if not explicit and _role_chain_edge(
        b, p, block, m, enclosing, from_id, span, evi, seen
    ):
        return
    target_qname = f"{m.type_name}.{m.name}"
    for rtype in _managed_ref_kinds(block, m, explicit, enclosing):
        key = (from_id, f"{ns}.{target_qname}", rtype)
        if key in seen:
            continue
        seen.add(key)
        b.rel_to_entity(rtype, p.path, from_id, ns, target_qname, span, evi)


def _emit_block_relations(
    b: _Builder,
    p: _Parsed,
    block: HclBlock,
    from_ids: list[DomainIdentity],
) -> None:
    """Refs do corpo do bloco → arestas por instância de identidade."""
    attr_spans = _attr_index(block)
    dep_attr = block.attribute("depends_on")
    dep_span = (
        (dep_attr.value_span.start_byte, dep_attr.value_span.end_byte)
        if dep_attr is not None
        else None
    )
    refs = scan_refs(block.body, base_byte=block.body_start)
    seen: set[tuple[DomainIdentity, str, str]] = set()
    for m in refs:
        explicit = (
            dep_span is not None and dep_span[0] <= m.start <= dep_span[1]
        )
        enclosing = "" if explicit else _enclosing_attr(attr_spans, m.start)
        for from_id in from_ids:
            _emit_one_ref(
                b, p, block, m, explicit, enclosing, from_id, seen
            )


def _emit_terraform_block(
    b: _Builder,
    p: _Parsed,
    block: HclBlock,
    from_ids: list[DomainIdentity],
) -> None:
    """``terraform { backend "x" {} / cloud {} }`` → REFERENCES external."""
    for child in block.children:
        if child.type not in ("backend", "cloud"):
            continue
        label = child.labels[0] if child.labels else ""
        target = f"{child.type}:{label}" if label else child.type
        for from_id in from_ids:
            evi = (b.evi(p.path, p.sha256, child.header_span or child.span),)
            b.rel_external(
                str(DomainRelationKind.REFERENCES), p.path, from_id,
                target, child.header_span, evi,
            )


def _emit_relations_for_block(
    b: _Builder,
    p: _Parsed,
    blk: HclBlock,
    ns_list: tuple[str, ...],
) -> None:
    """Despacha relações de um bloco — entidade própria ou módulo dono."""
    if blk.type in ("resource", "data") and len(blk.labels) < 2:
        return
    if blk.type in ("resource", "data", "module") and blk.labels:
        from_ids = _block_from_ids(b, blk, ns_list)
        if not from_ids:
            return
        if blk.type == "module":
            _emit_module_relations(b, p, blk, from_ids)
        elif blk.type == "data" and blk.labels[0] == "terraform_remote_state":
            _emit_remote_state_relations(b, p, blk, from_ids)
        _emit_block_relations(b, p, blk, from_ids)
        return
    if blk.type in ("output", "provider", "terraform"):
        owners = [b.mod_addr[ns] for ns in ns_list if ns in b.mod_addr]
        if not owners:
            return
        if blk.type == "terraform":
            _emit_terraform_block(b, p, blk, owners)
        _emit_block_relations(b, p, blk, owners)


def _emit_relations(
    b: _Builder,
    parsed: list[_Parsed],
    calls: list[_ModuleCall],
    addresses: dict[str, tuple[str, ...]],
) -> None:
    for p in parsed:
        if p.kind not in ("tf", "tfjson"):
            continue
        ns_list = addresses.get(
            p.directory, (f"unresolved:{p.directory}",)
        )
        for blk in p.parsed.blocks:
            _emit_relations_for_block(b, p, blk, ns_list)
    _emit_provisions(b, calls, addresses)


def _emit_module_relations(
    b: _Builder,
    p: _Parsed,
    blk: HclBlock,
    from_ids: list[DomainIdentity],
) -> None:
    """``module`` com source remoto → REFERENCES external (declarado)."""
    src_attr = blk.attribute("source")
    if src_attr is None:
        return
    source = _unquote(src_attr.value)
    if _norm_source(p.directory, source):
        return  # local — resolvido via PROVISIONS, nada externo
    for from_id in from_ids:
        evi = (b.evi(p.path, p.sha256, src_attr.value_span),)
        b.rel_external(
            str(DomainRelationKind.REFERENCES), p.path, from_id,
            source, src_attr.value_span, evi,
        )


def _emit_remote_state_relations(
    b: _Builder,
    p: _Parsed,
    blk: HclBlock,
    from_ids: list[DomainIdentity],
) -> None:
    """``data "terraform_remote_state"`` → leitura externa declarada."""
    backend = blk.attribute("backend")
    bname = _unquote(backend.value) if backend is not None else ""
    target = f"remote-state:{bname}" if bname else "remote-state"
    for from_id in from_ids:
        evi = (b.evi(p.path, p.sha256, blk.header_span or blk.span),)
        b.rel_external(
            str(DomainRelationKind.REFERENCES), p.path, from_id,
            target, blk.header_span, evi,
        )


def _provision_call(
    b: _Builder,
    call: _ModuleCall,
    addresses: dict[str, tuple[str, ...]],
    res_by_ns: dict[str, list[DomainIdentity]],
    prov_seen: set[tuple[DomainIdentity, DomainIdentity]],
) -> None:
    """``module x { source = "./dir" }`` → ``PROVISIONS`` por recurso."""
    for parent_addr in addresses.get(call.directory, ()):
        qname = (
            f"{parent_addr}.module.{call.name}"
            if parent_addr
            else f"module.{call.name}"
        )
        mod_id = b.mod_addr.get(qname)
        if mod_id is None:
            continue
        evi = (
            b.evi(call.parsed.path, call.parsed.sha256,
                  call.block.header_span or call.block.span),
        )
        for target in res_by_ns.get(qname, []):
            if (mod_id, target) in prov_seen:
                continue
            prov_seen.add((mod_id, target))
            b.relations.append(
                ExtractedDomainRelation(
                    relation_type=str(DomainRelationKind.PROVISIONS),
                    path=call.parsed.path, from_identity=mod_id,
                    to_identity=target,
                    resolution=b._res(
                        ResolutionStatus.RESOLVED, target=target,
                    ),
                    span=call.block.header_span, evidence=evi,
                )
            )


def _emit_provisions(
    b: _Builder,
    calls: list[_ModuleCall],
    addresses: dict[str, tuple[str, ...]],
) -> None:
    """``module`` call ``PROVISIONS`` recursos do source local; root idem."""
    res_by_ns: dict[str, list[DomainIdentity]] = {}
    for ent in b.entities:
        if ent.kind in (
            DomainEntityKind.TERRAFORM_RESOURCE, DomainEntityKind.IAM_ROLE,
        ):
            res_by_ns.setdefault(ent.identity.namespace, []).append(
                ent.identity
            )
    prov_seen: set[tuple[DomainIdentity, DomainIdentity]] = set()
    for call in calls:
        if call.local_dir:
            _provision_call(b, call, addresses, res_by_ns, prov_seen)
    root = b.mod_addr.get("")
    if root is not None:
        for target in res_by_ns.get("", []):
            b.relations.append(
                ExtractedDomainRelation(
                    relation_type=str(DomainRelationKind.PROVISIONS),
                    path="", from_identity=root, to_identity=target,
                    resolution=b._res(ResolutionStatus.RESOLVED, target=target),
                )
            )


def _emit_tfvars(
    b: _Builder, parsed: list[_Parsed]
) -> dict[str, list[str]]:
    """Hints de ambiente vindos de tfvars — valores sensíveis nunca saem."""
    env_hints: dict[str, list[str]] = {}
    for p in parsed:
        if p.kind != "tfvars":
            continue
        for name, value, redacted in p.tfvars:
            if redacted:
                b.stats["secrets_redacted"] = (
                    b.stats.get("secrets_redacted", 0) + 1
                )
                continue
            if name.lower() in _ENV_VAR_NAMES and value:
                vals = env_hints.setdefault(p.directory, [])
                if value not in vals:
                    vals.append(value)
    return env_hints


# ---------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------


def extract(
    request: DomainExtractionRequest,
    adapter_version: str,
    base_path: Path | None,
) -> DomainExtractionResult:
    """Orquestra leitura→parse→entidades→relações; nunca levanta."""
    b = _Builder(request=request, adapter_version=adapter_version)
    parsed = _read_artifacts(b, base_path)
    env_hints = _emit_tfvars(b, parsed)
    calls = _module_calls(parsed)
    addresses = _module_addresses(parsed, calls)
    env = _dir_env(b, parsed)
    _emit_entities(b, parsed, addresses, env, env_hints)
    _emit_relations(b, parsed, calls, addresses)
    b.stats["entities"] = len(b.entities)
    b.stats["relations"] = len(b.relations)
    b.stats["files"] = len(parsed)
    return DomainExtractionResult(
        domain_id=DOMAIN_ID,
        adapter_version=adapter_version,
        entities=tuple(b.entities),
        relations=tuple(b.relations),
        evidence=tuple(b.evidence),
        diagnostics=tuple(b.diagnostics),
        stats=b.stats,
    )
