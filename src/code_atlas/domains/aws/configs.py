"""Artefatos de configuração AWS-data — EMR/Glue em JSON/YAML (lf-0113).

Shapes reconhecidos (somente leitura — nenhum step/roda/comando é
executado, nenhum ``aws``/``boto3`` é contactado):

- **Glue job JSON**: ``{"Name","Role": "arn:…","Command":{"Name",
  "ScriptLocation"},"DefaultArguments"}`` → ``glue_job`` +
  ``ASSUMES_ROLE`` (external) + ``REFERENCES`` ao script S3;
- **EMR step configs**: ``{"Steps":[…]}``, ``{"JobFlowId","Steps":[…]}``
  ou lista top-level de ``{"Name","HadoopJarStep":{"Jar","Args"}}`` →
  entidade por step (``spark_job`` quando ``spark-submit`` está nos
  args — incl. via ``command-runner.jar``; ``data_pipeline`` senão) +
  ``DEPENDS_ON`` sequencial (ordem de submissão é fato estático);
- **cluster config**: dict com ``JobFlowRole``/``ServiceRole``/
  ``Applications`` → ``data_pipeline`` ``emr-cluster`` +
  ``ASSUMES_ROLE`` por ``*Role*``/``*RoleArn`` com ARN IAM literal;
- **glue triggers/workflows**: ``{"Type":"SCHEDULED"|"ON_DEMAND"|
  "CONDITIONAL","Actions":[{"JobName":…}]}`` ou ``{"Workflow",
  "Triggers":[…]}`` → ``data_pipeline`` + ``TRIGGERS``→job (resolvido
  quando o job foi extraído no request; ``external`` senão) +
  ``DEPENDS_ON`` para ``Predicate.Conditions``;
- **spark-submit em .sh**: linhas ``spark-submit --conf k=v script``
  → ``spark_job`` + ``REFERENCES`` a script s3/local;
- **spark confs** (``.conf``/``.properties``): ``spark.sql.catalog.<n>``
  → ``data_catalog``; ``*.warehouse=s3://`` → ``REFERENCES`` ao path.

JSON fora de shape AWS → ``not-aws-artifact`` info (nunca fato
fabricado); JSON malformado → ``malformed-config`` error. Chaves
``password|secret|token|key|credential`` têm valores ``[REDACTED]``
(política lf-0115).
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from code_atlas.contracts.enums import ResolutionStatus
from code_atlas.domains.aws.common import (
    ARN_RE,
    aws_catalog,
    byte_span,
    is_iam_role_arn,
    is_iceberg_impl,
    line_starts,
    parse_arn,
    parse_s3_uri,
    redact_if_sensitive,
)
from code_atlas.domains.aws.emit import Sink
from code_atlas.domains.aws.sqlbridge import s3_entity
from code_atlas.domains.cicd import yamlmini
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.parsers.contracts import SourceSpan

_READS = str(DomainRelationKind.READS_FROM)
_WRITES = str(DomainRelationKind.WRITES_TO)
_REFS = str(DomainRelationKind.REFERENCES)
_ASSUMES = str(DomainRelationKind.ASSUMES_ROLE)
_TRIGGERS = str(DomainRelationKind.TRIGGERS)
_DEPENDS = str(DomainRelationKind.DEPENDS_ON)
_CONTAINS = "CONTAINS"

_ROLE_KEYS = re.compile(r"role|rolearn|role_arn", re.IGNORECASE)
_STEP_TYPES = frozenset({"SCHEDULED", "ON_DEMAND", "CONDITIONAL", "EVENT"})


# -- helpers genéricos ------------------------------------------------------


def _file_key(path: str) -> str:
    return PurePosixPath(path).stem


def _json_span(text: str, starts: tuple[int, ...], needle: str) -> SourceSpan | None:
    """Span aproximado: primeira ocorrência literal de ``needle``."""
    idx = text.find(needle)
    if idx < 0:
        return None
    return byte_span(starts, idx, idx + len(needle))


def _role_entity_from_arn(
    sink: Sink,
    arn_text: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> DomainIdentity | None:
    parts = parse_arn(arn_text)
    if parts is None or not is_iam_role_arn(parts):
        return None
    return sink.entity(
        kind=DomainEntityKind.IAM_ROLE,
        name=parts.resource.rsplit("/", 1)[-1],
        qualified_name=parts.resource,
        catalog=aws_catalog("iam", parts.account, parts.region),
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "arn": parts.arn,
            "account": parts.account,
            "partition": parts.partition,
        },
        evidence=(evi,),
    )


def _emit_s3_refs(
    sink: Sink,
    values: Sequence[object],
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Valores que são URIs ``s3://`` → entidades + ``REFERENCES``."""
    for v in values:
        if not isinstance(v, str):
            continue
        for m in re.finditer(r"s3://[^\s'\",}\]]+", v):
            uri = m.group(0)
            ident = s3_entity(sink, uri, path=path, sha=sha, span=span, evi=evi)
            if ident is not None:
                sink.rel(
                    _REFS,
                    path,
                    subject,
                    to_identity=ident,
                    span=span,
                    evidence=(evi,),
                )
                sink.bump("s3_paths")


def _emit_role_edges(
    sink: Sink,
    doc: dict[str, Any],
    subject: DomainIdentity,
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
) -> int:
    """Chaves ``*Role*`` com ARN IAM → ``iam_role`` + ``ASSUMES_ROLE``."""
    n = 0
    for k, v in doc.items():
        if not isinstance(v, str) or not _ROLE_KEYS.search(k):
            continue
        for m in ARN_RE.finditer(v):
            span = _json_span(text, starts, m.group(0))
            evi = sink.evi(path, sha, span, notes=f"role:{k}")
            role = _role_entity_from_arn(
                sink,
                m.group(0),
                path=path,
                sha=sha,
                span=span,
                evi=evi,
            )
            if role is None:
                continue
            sink.rel(
                _ASSUMES,
                path,
                subject,
                to_identity=role,
                status=ResolutionStatus.EXTERNAL,
                span=span,
                evidence=(evi,),
            )
            n += 1
    return n


# -- spark-submit -----------------------------------------------------------


_SUBMIT_VALUE_FLAGS = frozenset(
    {
        "--master",
        "--deploy-mode",
        "--class",
        "--name",
        "--packages",
        "--jars",
        "--py-files",
        "--files",
        "--archives",
        "--queue",
        "--driver-memory",
        "--executor-memory",
        "--num-executors",
        "--executor-cores",
        "--driver-cores",
        "--repositories",
        "--exclude-packages",
        "--properties-file",
        "--keytab",
        "--principal",
        "--proxy-user",
        "-e",
    }
)
_NAMED_FLAGS = frozenset({"--class", "--master", "--deploy-mode", "--name"})


def _consume_conf(tokens: list[str], i: int, conf: dict[str, str]) -> int:
    """``--conf k=v`` | ``--conf`` ``k=v`` → último índice consumido."""
    tail = tokens[i][len("--conf") :]
    if tail:
        kv = tail.lstrip("=")
        if "=" in kv:
            k, _, v = kv.partition("=")
            conf[k] = v
        return i
    if i + 1 < len(tokens):
        kv = tokens[i + 1]
        if "=" in kv:
            k, _, v = kv.partition("=")
            conf[k] = v
        return i + 1
    return i


def _consume_value_flag(tokens: list[str], i: int, conf: dict[str, str]) -> int:
    """Flag ``--x valor`` — só flags nomeadas viram ``arg.*``."""
    if i + 1 >= len(tokens):
        return i
    if tokens[i] in _NAMED_FLAGS:
        conf[f"arg.{tokens[i].lstrip('-')}"] = tokens[i + 1]
    return i + 1


def _parse_spark_submit(
    tokens: list[str],
) -> tuple[dict[str, str], list[str]]:
    """``--conf k=v``/flags → (conf, positionals).

    Tokens com ``$``/``${`` viram ``<dynamic>`` — nunca resolvidos.
    """
    conf: dict[str, str] = {}
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--conf"):
            i = _consume_conf(tokens, i, conf)
        elif tok in _SUBMIT_VALUE_FLAGS:
            i = _consume_value_flag(tokens, i, conf)
        elif not tok.startswith("--") and tok != "spark-submit":
            positional.append(tok)
        i += 1
    return conf, positional


def _submit_catalogs(
    sink: Sink,
    conf: dict[str, str],
    attrs: dict[str, str],
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``spark.sql.catalog.<n>[.prop]=v`` → ``data_catalog`` + refs.

    Duas passadas: declarações primeiro (uma ``.warehouse`` pode citar
    catálogo declarado depois no mesmo comando); demais props viram
    ``conf.*`` no job — nunca promovidas a fato sem evidência.
    """
    catalogs: list[str] = []
    props: list[tuple[str, str, str]] = []
    for k, v in conf.items():
        if "$" in v or "${" in v:
            continue
        if not k.startswith("spark.sql.catalog."):
            attrs[f"conf.{k}"] = redact_if_sensitive(k, v)
            continue
        cat, _, prop = k[len("spark.sql.catalog.") :].partition(".")
        if prop:
            props.append((cat, prop, v))
            continue
        catalogs.append(cat)
        cat_ident = sink.entity(
            kind=DomainEntityKind.DATA_CATALOG,
            name=cat,
            qualified_name=cat,
            path=path,
            span=span,
            content_hash=sha,
            attributes={
                "declared_by": path,
                "catalog_impl": v,
                **({"engine": "iceberg"} if is_iceberg_impl(v) else {}),
            },
            evidence=(evi,),
        )
        sink.register_catalog(cat, cat_ident)
    for cat, prop, v in props:
        cat_ent = sink.catalogs.get(cat)
        if prop == "warehouse" and parse_s3_uri(v) and cat_ent is not None:
            s3 = s3_entity(sink, v, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _REFS,
                    path,
                    cat_ent,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )
            continue
        key = f"spark.sql.catalog.{cat}.{prop}"
        attrs[f"conf.{key}"] = redact_if_sensitive(key, v)
    if catalogs:
        attrs["catalogs"] = ",".join(sorted(catalogs))


def _submit_script_refs(
    sink: Sink,
    script: str,
    positional: list[str],
    job: DomainIdentity,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """Script + posicionais ``s3://`` → ``REFERENCES``/unresolved."""
    if script:
        s3 = s3_entity(sink, script, path=path, sha=sha, span=span, evi=evi)
        if s3 is not None:
            sink.rel(
                _REFS,
                path,
                job,
                to_identity=s3,
                span=span,
                evidence=(evi,),
            )
            sink.bump("s3_paths")
        elif script.startswith("s3") or "{" in script:
            sink.diag(
                "dynamic-s3-uri",
                "warning",
                "spark-submit script arg is dynamic",
                path,
            )
        else:
            sink.rel(
                _REFS,
                path,
                job,
                to_unresolved=script,
                status=ResolutionStatus.UNRESOLVED,
                span=span,
                evidence=(evi,),
            )
    for p in positional[1:] if script else positional:
        if p.startswith("-"):
            continue
        s3 = s3_entity(sink, p, path=path, sha=sha, span=span, evi=evi)
        if s3 is not None:
            sink.rel(
                _REFS,
                path,
                job,
                to_identity=s3,
                span=span,
                evidence=(evi,),
            )


def _emit_spark_submit(
    sink: Sink,
    tokens: list[str],
    subject_hint: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    step: DomainIdentity | None = None,
) -> DomainIdentity:
    """``spark-submit …`` → ``spark_job`` + refs a script/conf."""
    conf, positional = _parse_spark_submit(tokens)
    script = next((p for p in positional if not p.startswith("-")), "")
    evi = sink.evi(path, sha, span, notes="spark-submit")
    qname = subject_hint or _file_key(path)
    attrs: dict[str, str] = {"artifact_kind": "spark-submit"}
    for key in ("arg.class", "arg.master", "arg.deploy-mode", "arg.name"):
        if key in conf:
            attrs[key[4:]] = conf[key]
    _submit_catalogs(sink, conf, attrs, path=path, sha=sha, span=span, evi=evi)
    job = sink.entity(
        kind=DomainEntityKind.SPARK_JOB,
        name=qname,
        qualified_name=qname,
        namespace=posixpath.dirname(path),
        path=path,
        span=span,
        content_hash=sha,
        attributes=attrs,
        evidence=(evi,),
    )
    if step is not None and step.logical_key() != job.logical_key():
        sink.rel(
            _CONTAINS,
            path,
            step,
            to_identity=job,
            span=span,
            evidence=(evi,),
        )
    _submit_script_refs(
        sink,
        script,
        positional,
        job,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )
    return job


# -- EMR steps -------------------------------------------------------------


def _step_entity(
    sink: Sink,
    name: str,
    *,
    spark: bool,
    file_stem: str,
    path: str,
    sha: str,
    span: SourceSpan | None,
    attrs: dict[str, str],
) -> DomainIdentity:
    kind = DomainEntityKind.SPARK_JOB if spark else DomainEntityKind.DATA_PIPELINE
    qname = f"{file_stem}#{name}"
    merged = {"artifact_kind": "emr-step", "emr_step": "true", **attrs}
    return sink.entity(
        kind=kind,
        name=name,
        qualified_name=qname,
        namespace=posixpath.dirname(path),
        path=path,
        span=span,
        content_hash=sha,
        attributes=merged,
        evidence=(sink.evi(path, sha, span, notes="emr-step"),),
    )


def _emit_step(
    sink: Sink,
    step: dict[str, Any],
    idx: int,
    prev: DomainIdentity | None,
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
) -> DomainIdentity | None:
    """Um step EMR → ``spark_job`` (spark-submit) ou ``data_pipeline``."""
    name = str(step.get("Name") or f"step-{idx}")
    jarstep = step.get("HadoopJarStep")
    jar = args = None
    if isinstance(jarstep, dict):
        jar = jarstep.get("Jar")
        raw_args = jarstep.get("Args")
        args = [str(a) for a in raw_args] if isinstance(raw_args, (list, tuple)) else None
    span = _json_span(text, starts, name) or None
    attrs: dict[str, str] = {}
    if isinstance(step.get("ActionOnFailure"), str):
        attrs["action_on_failure"] = str(step["ActionOnFailure"])
    if isinstance(jar, str) and jar:
        attrs["jar"] = jar[:200]
    spark = False
    if args:
        joined = " ".join(args)
        if "spark-submit" in joined or "spark_submit" in joined:
            spark = True
    ident = _step_entity(
        sink,
        name,
        spark=spark,
        file_stem=_file_key(path),
        path=path,
        sha=sha,
        span=span,
        attrs=attrs,
    )
    if prev is not None:
        sink.rel(
            _DEPENDS,
            path,
            ident,
            to_identity=prev,
            span=span,
            evidence=(sink.evi(path, sha, span, notes="step-order"),),
        )
    if args:
        tokens = list(args)
        # command-runner.jar ou Jar direto — args[0] é o comando
        if tokens and tokens[0] in ("spark-submit", "spark_submit"):
            _emit_spark_submit(
                sink,
                tokens,
                name,
                path=path,
                sha=sha,
                span=span,
                step=ident,
            )
        else:
            _emit_s3_refs(
                sink,
                args,
                ident,
                path=path,
                sha=sha,
                span=span,
                evi=sink.evi(path, sha, span, notes="step-args"),
            )
            if tokens:
                attrs_cmd = " ".join(tokens[:4])[:120]
                sink.entity_for(
                    ident,
                    kind=ident_kind(ident, sink),
                    name=name,
                    path=path,
                    span=span,
                    content_hash=sha,
                    attributes={
                        "artifact_kind": "emr-step",
                        "emr_step": "true",
                        "command": attrs_cmd,
                    },
                    evidence=(),
                )
    return ident


def ident_kind(ident: DomainIdentity, sink: Sink) -> DomainEntityKind:
    """Recupera o kind já emitido para ``ident`` (merge de attrs)."""
    ent = sink.entities.get(ident.logical_key())
    return ent.kind if ent is not None else DomainEntityKind.DATA_PIPELINE


def _emit_steps(
    sink: Sink,
    steps: list[Any],
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
    cluster: DomainIdentity | None = None,
) -> int:
    prev: DomainIdentity | None = None
    n = 0
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            sink.diag(
                "malformed-step",
                "warning",
                f"step #{i} is not an object — skipped",
                path,
            )
            continue
        ident = _emit_step(
            sink,
            st,
            i,
            prev,
            path=path,
            sha=sha,
            text=text,
            starts=starts,
        )
        if ident is not None:
            if cluster is not None:
                sink.rel(
                    _CONTAINS,
                    path,
                    cluster,
                    to_identity=ident,
                    span=None,
                    evidence=(sink.evi(path, sha, None, "cluster-step"),),
                )
            prev = ident
            n += 1
    return n


# -- documentos -------------------------------------------------------------


def _glue_job_entity(
    sink: Sink,
    doc: dict[str, Any],
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
) -> DomainIdentity | None:
    """``{"Name","Role","Command":{…},"DefaultArguments"}`` → glue_job."""
    name = doc.get("Name")
    if not isinstance(name, str) or not name:
        sink.diag(
            "missing-name",
            "warning",
            "glue job definition without literal Name",
            path,
        )
        return None
    span = _json_span(text, starts, name)
    evi = sink.evi(path, sha, span, notes="glue-job-def")
    attrs: dict[str, str] = {"artifact_kind": "glue-job-definition"}
    cmd = doc.get("Command")
    if isinstance(cmd, dict):
        cname = cmd.get("Name")
        if isinstance(cname, str):
            attrs["command"] = cname
        pver = cmd.get("PythonVersion")
        if isinstance(pver, str):
            attrs["python_version"] = pver
    ident = sink.entity(
        kind=DomainEntityKind.GLUE_JOB,
        name=name,
        qualified_name=name,
        namespace=posixpath.dirname(path),
        path=path,
        span=span,
        content_hash=sha,
        attributes=attrs,
        evidence=(evi,),
    )
    _emit_role_edges(sink, doc, ident, path=path, sha=sha, text=text, starts=starts)
    if isinstance(cmd, dict):
        _job_command_refs(sink, cmd, ident, path=path, sha=sha, span=span, evi=evi)
    _job_default_args(sink, doc, ident, name, path=path, sha=sha, span=span, evi=evi)
    sink.bump("glue_jobs")
    return ident


def _job_command_refs(
    sink: Sink,
    cmd: dict[str, Any],
    ident: DomainIdentity,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``Command.ScriptLocation`` → ``REFERENCES`` ao script s3."""
    loc = cmd.get("ScriptLocation")
    if not isinstance(loc, str):
        return
    s3 = s3_entity(sink, loc, path=path, sha=sha, span=span, evi=evi)
    if s3 is not None:
        sink.rel(_REFS, path, ident, to_identity=s3, span=span, evidence=(evi,))
        sink.bump("s3_paths")


def _job_default_args(
    sink: Sink,
    doc: dict[str, Any],
    ident: DomainIdentity,
    name: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``DefaultArguments`` → attrs ``arg.*`` (redacted) + refs s3."""
    defaults = doc.get("DefaultArguments")
    if not isinstance(defaults, dict):
        return
    extra = {f"arg.{k}": redact_if_sensitive(k, str(v)) for k, v in defaults.items()}
    if extra:
        sink.entity_for(
            ident,
            kind=DomainEntityKind.GLUE_JOB,
            name=name,
            path=path,
            span=span,
            content_hash=sha,
            attributes={"artifact_kind": "glue-job-definition", **extra},
            evidence=(evi,),
        )
    _emit_s3_refs(
        sink,
        list(defaults.values()),
        ident,
        path=path,
        sha=sha,
        span=span,
        evi=evi,
    )


def _emit_trigger(
    sink: Sink,
    doc: dict[str, Any],
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
) -> int:
    """Glue trigger ``{"Type","Actions":[{JobName}],"Predicate"}``."""
    name = str(doc.get("Name") or _file_key(path))
    span = _json_span(text, starts, name)
    evi = sink.evi(path, sha, span, notes="glue-trigger")
    ttype = str(doc.get("Type") or "")
    attrs = {
        "artifact_kind": "glue-trigger",
        "trigger_type": ttype,
    }
    sched = doc.get("Schedule")
    if isinstance(sched, str):
        attrs["schedule"] = sched[:120]
    trig = sink.entity(
        kind=DomainEntityKind.DATA_PIPELINE,
        name=name,
        qualified_name=f"trigger:{name}",
        namespace=posixpath.dirname(path),
        path=path,
        span=span,
        content_hash=sha,
        attributes=attrs,
        evidence=(evi,),
    )
    n = 0
    actions = doc.get("Actions")
    if isinstance(actions, list):
        n = _trigger_actions(sink, actions, trig, path=path, span=span, evi=evi)
    pred = doc.get("Predicate")
    if isinstance(pred, dict) and isinstance(pred.get("Conditions"), list):
        _trigger_conditions(sink, pred["Conditions"], trig, path=path, span=span, evi=evi)
    return n


def _trigger_actions(
    sink: Sink,
    actions: list[Any],
    trig: DomainIdentity,
    *,
    path: str,
    span: SourceSpan | None,
    evi: int,
) -> int:
    """``Actions[].JobName`` → ``TRIGGERS`` (resolvido ou external)."""
    n = 0
    for a in actions:
        if not isinstance(a, dict):
            continue
        jobname = a.get("JobName") or a.get("Job")
        crawler = a.get("CrawlerName")
        target_txt = crawler or jobname
        if not isinstance(target_txt, str) or not target_txt:
            continue
        tgt = sink.by_name.get((DomainEntityKind.GLUE_JOB.value, str(jobname))) if jobname else None
        if tgt is not None:
            sink.rel(
                _TRIGGERS,
                path,
                trig,
                to_identity=tgt,
                span=span,
                evidence=(evi,),
            )
        else:
            label = "glue-crawler" if crawler else "glue-job"
            sink.rel(
                _TRIGGERS,
                path,
                trig,
                to_unresolved=f"{label}:{target_txt}",
                status=ResolutionStatus.EXTERNAL,
                span=span,
                evidence=(evi,),
            )
        n += 1
    return n


def _trigger_conditions(
    sink: Sink,
    conds: list[Any],
    trig: DomainIdentity,
    *,
    path: str,
    span: SourceSpan | None,
    evi: int,
) -> None:
    """``Predicate.Conditions[].JobName`` → ``DEPENDS_ON``."""
    for c in conds:
        if not isinstance(c, dict):
            continue
        jn = c.get("JobName") or c.get("CrawlerName")
        if not isinstance(jn, str) or not jn:
            continue
        tgt = sink.by_name.get((DomainEntityKind.GLUE_JOB.value, jn))
        if tgt is not None:
            sink.rel(
                _DEPENDS,
                path,
                trig,
                to_identity=tgt,
                span=span,
                evidence=(evi,),
            )
        else:
            sink.rel(
                _DEPENDS,
                path,
                trig,
                to_unresolved=f"glue-job:{jn}",
                status=ResolutionStatus.EXTERNAL,
                span=span,
                evidence=(evi,),
            )


def _walk_json(
    sink: Sink,
    doc: Any,
    *,
    path: str,
    sha: str,
    text: str,
    starts: tuple[int, ...],
) -> None:
    """Despacha um documento JSON para o shape AWS reconhecido."""
    if isinstance(doc, list):
        if doc and all(isinstance(s, dict) and "HadoopJarStep" in s for s in doc):
            _emit_steps(
                sink,
                doc,
                path=path,
                sha=sha,
                text=text,
                starts=starts,
            )
            return
        sink.diag(
            "not-aws-artifact",
            "info",
            "JSON list is not an EMR step config",
            path,
        )
        return
    if not isinstance(doc, dict):
        sink.diag(
            "not-aws-artifact",
            "info",
            "JSON scalar/array without AWS shape",
            path,
        )
        return

    handled = False
    if (
        isinstance(doc.get("Command"), dict)
        and ("ScriptLocation" in doc["Command"] or "Role" in doc)
    ) or ("ScriptLocation" in doc and ("Role" in doc or "Command" in doc)):
        _glue_job_entity(sink, doc, path=path, sha=sha, text=text, starts=starts)
        handled = True

    ttype = str(doc.get("Type") or "").upper()
    if ttype in _STEP_TYPES and ("Actions" in doc or "Predicate" in doc):
        _emit_trigger(sink, doc, path=path, sha=sha, text=text, starts=starts)
        handled = True

    cluster_keys = {
        "JobFlowRole",
        "ServiceRole",
        "AutoScalingRole",
        "Applications",
        "Instances",
        "LogUri",
        "Ec2KeyName",
    }
    cluster = None
    if cluster_keys & set(doc):
        name = str(doc.get("Name") or _file_key(path))
        span = _json_span(text, starts, name)
        evi = sink.evi(path, sha, span, notes="emr-cluster")
        cluster = sink.entity(
            kind=DomainEntityKind.DATA_PIPELINE,
            name=name,
            qualified_name=f"emr-cluster:{name}",
            namespace=posixpath.dirname(path),
            path=path,
            span=span,
            content_hash=sha,
            attributes={"artifact_kind": "emr-cluster-config"},
            evidence=(evi,),
        )
        _emit_role_edges(
            sink,
            doc,
            cluster,
            path=path,
            sha=sha,
            text=text,
            starts=starts,
        )
        handled = True

    steps = doc.get("Steps")
    if isinstance(steps, list):
        _emit_steps(
            sink,
            steps,
            path=path,
            sha=sha,
            text=text,
            starts=starts,
            cluster=cluster,
        )
        handled = True
    elif "Steps" in doc and not isinstance(steps, list):
        sink.diag(
            "malformed-config",
            "warning",
            "'Steps' is not a list — skipped",
            path,
        )

    if not handled:
        sink.diag(
            "not-aws-artifact",
            "info",
            "JSON does not match any AWS-data shape — skipped",
            path,
        )


def extract_json(sink: Sink, *, path: str, text: str, sha: str) -> None:
    """Artefato ``.json`` → shapes AWS-data."""
    starts = line_starts(text)
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        sink.diag(
            "malformed-config",
            "error",
            f"JSON does not parse: {exc}",
            path,
        )
        return
    _walk_json(sink, doc, path=path, sha=sha, text=text, starts=starts)
    sink.bump("json_files")


def _ynode_py(node: yamlmini.YNode) -> Any:
    """``YNode`` → python dict/list/str (subconjunto honesto)."""
    if node.kind == "map":
        out: dict[str, Any] = {}
        for k, v in node.pairs:
            if k.kind == "scalar":
                out[k.value] = _ynode_py(v)
        return out
    if node.kind == "seq":
        return [_ynode_py(it) for it in node.items]
    if node.kind == "scalar":
        return node.value
    return None


def extract_yaml(sink: Sink, *, path: str, text: str, sha: str) -> None:
    """Artefato ``.yaml``/``.yml`` via ``yamlmini`` (subconjunto)."""
    result = yamlmini.parse(text)
    for yd in result.diagnostics:
        sink.diag(
            f"yaml-{yd.code}",
            "error" if yd.severity == "error" else "warning",
            f"line {yd.line}: {yd.message}" if yd.line else yd.message,
            path,
        )
    if result.root is None:
        if not result.diagnostics:
            sink.diag("not-aws-artifact", "info", "empty YAML document", path)
        return
    doc = _ynode_py(result.root)
    starts = line_starts(text)
    _walk_json(sink, doc, path=path, sha=sha, text=text, starts=starts)
    sink.bump("yaml_files")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Junta continuações ``\\`` — pares ``(lineno, linha lógica)``."""
    logical: list[tuple[int, str]] = []
    buf = ""
    buf_line = 0
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        cont = stripped.endswith("\\")
        piece = stripped[:-1].rstrip() if cont else stripped
        if buf:
            buf = f"{buf} {piece}" if piece else buf
        else:
            buf = piece
            buf_line = i
        if not cont:
            if buf:
                logical.append((buf_line, buf))
            buf = ""
    if buf:
        logical.append((buf_line, buf))
    return logical


def _shell_submit_line(
    sink: Sink,
    lineno: int,
    line: str,
    *,
    path: str,
    sha: str,
    starts: tuple[int, ...],
) -> bool:
    """Uma linha lógica com ``spark-submit`` → ``spark_job``."""
    code = line.split("#", 1)[0]
    try:
        tokens = shlex.split(code, posix=True)
    except ValueError:
        sink.diag(
            "malformed-config",
            "warning",
            f"line {lineno}: spark-submit line does not lex",
            path,
        )
        return False
    if "spark-submit" not in tokens:
        return False
    tokens = tokens[tokens.index("spark-submit") :]
    if "$" in " ".join(tokens):
        tokens = [t if "$" not in t else "<dynamic>" for t in tokens]
    line_b = len(line.encode("utf-8"))
    span = byte_span(starts, starts[lineno - 1], starts[lineno - 1] + line_b)
    _emit_spark_submit(sink, tokens, "", path=path, sha=sha, span=span)
    return True


def extract_shell(sink: Sink, *, path: str, text: str, sha: str) -> None:
    """``.sh`` com linhas ``spark-submit`` → ``spark_job``."""
    starts = line_starts(text)
    found = 0
    for lineno, line in _logical_lines(text):
        if "spark-submit" not in line:
            continue
        found += _shell_submit_line(sink, lineno, line, path=path, sha=sha, starts=starts)
    if not found:
        sink.diag(
            "not-aws-artifact",
            "info",
            "shell script without spark-submit lines",
            path,
        )
    sink.bump("shell_files")


def _conf_catalog_key(
    sink: Sink,
    key: str,
    val: str,
    *,
    path: str,
    sha: str,
    span: SourceSpan | None,
) -> int:
    """``spark.sql.catalog.<n>[.prop]=v`` — declaração ou warehouse."""
    cat, _, prop = key[len("spark.sql.catalog.") :].partition(".")
    if prop:
        cat_ident = sink.catalogs.get(cat)
        if prop == "warehouse" and parse_s3_uri(val) and cat_ident is not None:
            evi = sink.evi(path, sha, span, notes="catalog-warehouse")
            s3 = s3_entity(sink, val, path=path, sha=sha, span=span, evi=evi)
            if s3 is not None:
                sink.rel(
                    _REFS,
                    path,
                    cat_ident,
                    to_identity=s3,
                    span=span,
                    evidence=(evi,),
                )
        return 0
    evi = sink.evi(path, sha, span, notes="spark-catalog-conf")
    ident = sink.entity(
        kind=DomainEntityKind.DATA_CATALOG,
        name=cat,
        qualified_name=cat,
        path=path,
        span=span,
        content_hash=sha,
        attributes={
            "declared_by": path,
            "catalog_impl": redact_if_sensitive(key, val),
            **({"engine": "iceberg"} if is_iceberg_impl(val) else {}),
        },
        evidence=(evi,),
    )
    sink.register_catalog(cat, ident)
    return 1


def extract_spark_conf(sink: Sink, *, path: str, text: str, sha: str) -> None:
    """``.conf``/``.properties`` com chaves ``spark.*`` → catálogos."""
    starts = line_starts(text)
    seen = False
    catalogs = 0
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*[= ]\s*(.*)$", line)
        if m is None:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not key.startswith("spark."):
            continue
        seen = True
        if not key.startswith("spark.sql.catalog."):
            continue
        raw_b = len(raw.encode("utf-8"))
        span = byte_span(starts, starts[i - 1], starts[i - 1] + raw_b)
        catalogs += _conf_catalog_key(sink, key, val, path=path, sha=sha, span=span)
    if not seen:
        sink.diag(
            "not-aws-artifact",
            "info",
            "conf/properties without spark.* keys",
            path,
        )
    else:
        sink.bump("spark_confs")
        sink.bump("catalogs", catalogs)
