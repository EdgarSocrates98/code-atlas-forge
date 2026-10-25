"""Camada semântica GitHub Actions sobre a árvore yamlmini — lf-0116.

Observa (nunca executa) um workflow: triggers ``on:``, ``jobs:``,
``steps:``, ``needs:``, ``uses:`` de workflows reutilizáveis e actions,
``environment:``, ``strategy.matrix``, ``runs-on:`` e artefatos
publicados/consumidos por actions de artefato.

Regras duras (spec lf-0116):

- interpolação ``${{ ... }}`` NUNCA vira valor concreto — vira marcador
  ``<expr>``; expressões ``secrets.*`` viram ``<secret>`` (redação);
- ``uses:`` externo é referência declarada — nunca baixado nem
  resolvido; ``uses: ./...`` só resolve quando o arquivo referenciado
  está entre os artefatos do request;
- ``environment:`` só é alvo quando literal estático; valor dinâmico
  é marcado, nunca inventado;
- ``strategy.matrix`` reporta multiplicidade **declarada** (eixos e
  contagem de valores) — expansão é runtime e não é simulada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_atlas.domains.cicd.yamlmini import YamlDiagnostic, YNode
from code_atlas.parsers.contracts import SourceSpan

# ${{ ... }}: não-guloso até '}}' ou fim da linha (redação conservadora —
# expressão não-fechada redige até o fim da linha, nunca vaza conteúdo).
_EXPR = re.compile(r"\$\{\{(.*?)(?:\}\}|\n|$)", re.S)
_SECRET_HINT = re.compile(r"\bsecrets\b", re.I)

DYNAMIC = "<expr>"
SECRET = "<secret>"

# chaves ``on:`` que o adapter classifica por nome — qualquer outra chave
# de ``on:`` é trigger válido do GitHub e entra como kind próprio.
SCHEDULE = "schedule"


def sanitize(text: str) -> str:
    """Substitui ``${{ expr }}`` por marcadores — nunca avalia.

    ``secrets.*`` e qualquer menção ao contexto ``secrets`` → ``<secret>``;
    demais expressões → ``<expr>``. Texto sem interpolação volta intacto.
    """
    if "${{" not in text:
        return text

    def _rep(m: re.Match[str]) -> str:
        inner = m.group(1)
        return SECRET if _SECRET_HINT.search(inner) else DYNAMIC

    return _EXPR.sub(_rep, text)


def has_interpolation(text: str) -> bool:
    return "${{" in text


@dataclass(frozen=True)
class TriggerObs:
    """Um evento ``on:`` observado estaticamente."""

    kind: str                 # push | pull_request | schedule | …
    descriptor: str           # alvo textual honesto p/ to_unresolved
    span: SourceSpan
    details: tuple[tuple[str, str], ...] = ()  # ex.: ("branches","main,dev")


@dataclass(frozen=True)
class StepObs:
    """Passo observado: ``run``/``uses`` + classificação declarada."""

    index: int
    name: str                 # ``name:`` declarado; "" quando ausente
    uses: str                 # ref de action (sanitizado) ou ""
    run: str                  # primeiro comando (sanitizado, truncado) ou ""
    env_keys: tuple[str, ...] = ()
    classes: frozenset[str] = frozenset()   # rótulos classify.py
    artifacts_out: tuple[str, ...] = ()
    artifacts_in: tuple[str, ...] = ()
    span: SourceSpan | None = None


@dataclass(frozen=True)
class JobObs:
    """Job observado dentro de ``jobs:``."""

    job_id: str
    display_name: str
    uses: str                 # reusable-workflow call ou ""
    needs: tuple[str, ...] = ()
    runs_on: str = ""         # sanitizado; seq → csv
    environment: str = ""     # nome estático; "" quando ausente/dinâmico
    environment_dynamic: bool = False
    if_expr: str = ""         # sanitizado
    matrix_axes: tuple[str, ...] = ()       # "os=3" multiplicidade declarada
    matrix_dynamic: bool = False
    matrix_extras: tuple[str, ...] = ()     # include|exclude presentes
    fail_fast: str = ""
    max_parallel: str = ""
    permissions: str = ""
    env_keys: tuple[str, ...] = ()
    secrets_mode: str = ""    # "inherit" | "declared" | ""
    concurrency: str = ""
    outputs: int = 0          # nº de outputs declarados (workflow_call)
    steps: tuple[StepObs, ...] = ()
    needs_span: SourceSpan | None = None
    span: SourceSpan | None = None


@dataclass(frozen=True)
class WorkflowObs:
    """Observação completa de um arquivo de workflow."""

    path: str
    name: str                 # ``name:`` ou stem do arquivo (fallback)
    name_declared: bool
    triggers: tuple[TriggerObs, ...] = ()
    jobs: tuple[JobObs, ...] = ()
    permissions: str = ""
    env_keys: tuple[str, ...] = ()
    concurrency: str = ""
    span: SourceSpan | None = None


@dataclass(frozen=True)
class WorkflowParse:
    obs: WorkflowObs | None
    diagnostics: tuple[YamlDiagnostic, ...] = field(default_factory=tuple)


def _scalar(node: YNode | None) -> str:
    """Texto sanitizado de um nó escalar; "" para ausente/não-escalar."""
    if node is None:
        return ""
    v = node.scalar_text()
    if v is None:
        # nó não-escalar onde se esperava escalar → marcador honesto
        return f"<{node.kind}>"
    return sanitize(v)


def _raw_scalar(node: YNode | None) -> str:
    if node is None:
        return ""
    v = node.scalar_text()
    return "" if v is None else v


def _map_summary(node: YNode | None) -> str:
    """``key:value`` resumido em csv sanitizado (permissions etc.)."""
    if node is None or node.kind != "map":
        return ""
    parts = []
    for k, v in node.pairs:
        if k.kind != "scalar":
            continue
        val = v.scalar_text()
        parts.append(
            f"{k.value}:{sanitize(val)}" if val is not None
            else f"{k.value}:<{v.kind}>"
        )
    return ",".join(parts)


def _csv_keys(node: YNode | None) -> tuple[str, ...]:
    if node is None or node.kind != "map":
        return ()
    return tuple(k.value for k, _ in node.pairs if k.kind == "scalar")


def _step_descriptor(step: StepObs) -> str:
    """Alvo textual de arestas step-level — sanitizado e limitado."""
    if step.name:
        return f'step:"{step.name}"'
    if step.uses:
        return f"uses:{step.uses}"
    return f"run:{step.run}" if step.run else f"step[{step.index}]"


def step_descriptor(step: StepObs) -> str:
    return _step_descriptor(step)


def _trigger_details(node: YNode | None) -> tuple[tuple[str, str], ...]:
    """Filtros rasos do trigger (branches/tags/types/inputs/…)."""
    if node is None or node.kind != "map":
        return ()
    out: list[tuple[str, str]] = []
    for k, v in node.pairs:
        if k.kind != "scalar":
            continue
        texts = v.scalar_list()
        if texts:
            out.append((k.value, ",".join(sanitize(t) for t in texts)))
        elif v.kind == "map":
            keys = _csv_keys(v)
            if keys:
                out.append((k.value, ",".join(keys)))
        else:
            s = _scalar(v)
            if s:
                out.append((k.value, s))
    return tuple(out)


def _schedule_triggers(node: YNode) -> tuple[TriggerObs, ...]:
    """``schedule:`` é seq de ``- cron: '…'`` — um trigger por entrada."""
    out: list[TriggerObs] = []
    for it in node.items:
        cron = it.get("cron") if it.kind == "map" else None
        cs = _scalar(cron)
        if cs:
            out.append(
                TriggerObs(
                    SCHEDULE, f"schedule:cron={cs}", it.span,
                    (("cron", cs),),
                )
            )
        else:
            out.append(
                TriggerObs(SCHEDULE, "schedule:<unparsed>", it.span, ())
            )
    return tuple(out)


def _mk_trigger(
    kind: str, node: YNode | None, span: SourceSpan
) -> tuple[TriggerObs, ...]:
    if kind == SCHEDULE and node is not None and node.kind == "seq":
        return _schedule_triggers(node)
    return (TriggerObs(kind, kind, span, _trigger_details(node)),)


def _triggers(on_node: YNode | None, diags: list[YamlDiagnostic]) -> tuple[TriggerObs, ...]:
    """Interpreta ``on:`` nas três formas: escalar, seq e mapa."""
    if on_node is None:
        return ()
    out: list[TriggerObs] = []
    if on_node.kind == "scalar":
        out.extend(_mk_trigger(on_node.value, None, on_node.span))
    elif on_node.kind == "seq":
        for it in on_node.items:
            if it.kind == "scalar":
                out.extend(_mk_trigger(it.value, None, it.span))
            else:
                diags.append(
                    YamlDiagnostic(
                        "trigger-non-scalar", "warning",
                        "non-scalar entry in 'on:' sequence skipped",
                        it.span.start_line,
                    )
                )
    elif on_node.kind == "map":
        for k, v in on_node.pairs:
            if k.kind == "scalar":
                out.extend(_mk_trigger(k.value, v, v.span))
    else:
        diags.append(
            YamlDiagnostic(
                "trigger-unparsed", "warning",
                f"'on:' has unsupported node kind {on_node.kind}",
                on_node.span.start_line,
            )
        )
    return tuple(out)


def _matrix(job: YNode) -> tuple[tuple[str, ...], bool, tuple[str, ...], str, str]:
    """Multiplicidade declarada de ``strategy.matrix`` — nunca expandida."""
    strat = job.get("strategy")
    if strat is None or strat.kind != "map":
        return (), False, (), "", ""
    matrix = strat.get("matrix")
    axes: list[str] = []
    dynamic = False
    extras: list[str] = []
    if matrix is not None and matrix.kind == "map":
        for k, v in matrix.pairs:
            if k.kind != "scalar":
                continue
            if k.value in ("include", "exclude"):
                extras.append(k.value)
                continue
            if v.kind == "seq":
                axes.append(f"{k.value}={len(v.items)}")
            elif has_interpolation(_raw_scalar(v)):
                dynamic = True
                axes.append(f"{k.value}={DYNAMIC}")
            else:
                axes.append(f"{k.value}=?")
    elif matrix is not None:
        dynamic = True
        axes.append(f"<{matrix.kind}>")
    ff = _scalar(strat.get("fail-fast"))
    mp = _scalar(strat.get("max-parallel"))
    return tuple(axes), dynamic, tuple(extras), ff, mp


def _environment(job: YNode) -> tuple[str, bool]:
    """``environment:`` literal → nome; mapa → ``name:``; expr → dinâmico."""
    env = job.get("environment")
    if env is None:
        return "", False
    node: YNode | None = env
    if env.kind == "map":
        node = env.get("name")
        if node is None:
            return "<unnamed-map>", False
    raw = _raw_scalar(node)
    if not raw:
        return "", False
    if has_interpolation(raw):
        return "", True
    return raw, False


def _steps(job: YNode, diags: list[YamlDiagnostic]) -> tuple[StepObs, ...]:
    from code_atlas.domains.cicd.classify import (
        ARTIFACT_FETCH_USES,
        ARTIFACT_UPLOAD_USES,
        classify_step,
    )

    seq = job.get("steps")
    if seq is None:
        return ()
    if seq.kind != "seq":
        diags.append(
            YamlDiagnostic(
                "steps-not-seq", "warning",
                "'steps:' is not a sequence — no steps extracted",
                seq.span.start_line,
            )
        )
        return ()
    out: list[StepObs] = []
    for idx, item in enumerate(seq.items):
        if item.kind != "map":
            diags.append(
                YamlDiagnostic(
                    "step-not-map", "warning",
                    "step entry is not a mapping — skipped",
                    item.span.start_line,
                )
            )
            continue
        name = _scalar(item.get("name"))
        uses = _scalar(item.get("uses"))
        run_raw = _raw_scalar(item.get("run"))
        run = sanitize(run_raw.split("\n")[0].strip()) if run_raw else ""
        if len(run) > 120:
            run = run[:117] + "..."
        with_args = item.get("with")
        artifacts_out: list[str] = []
        artifacts_in: list[str] = []
        u_low = uses.lower()
        if uses and any(p in u_low for p in ARTIFACT_UPLOAD_USES):
            nm = _scalar(with_args.get("name") if with_args else None)
            artifacts_out.append(nm or "<default>")
        if uses and any(p in u_low for p in ARTIFACT_FETCH_USES):
            nm = _scalar(with_args.get("name") if with_args else None)
            artifacts_in.append(nm or "<default>")
        classes = classify_step(uses=uses, run=run_raw)
        out.append(
            StepObs(
                index=idx, name=name, uses=uses, run=run,
                env_keys=_csv_keys(item.get("env")),
                classes=classes,
                artifacts_out=tuple(artifacts_out),
                artifacts_in=tuple(artifacts_in),
                span=item.span,
            )
        )
    return tuple(out)


def _needs(job: YNode) -> tuple[tuple[str, ...], SourceSpan | None]:
    node = job.get("needs")
    if node is None:
        return (), None
    return tuple(sanitize(t) for t in node.scalar_list()), node.span


def _jobs(jobs_node: YNode | None, diags: list[YamlDiagnostic]) -> tuple[JobObs, ...]:
    if jobs_node is None:
        return ()
    if jobs_node.kind != "map":
        diags.append(
            YamlDiagnostic(
                "jobs-not-map", "error",
                "'jobs:' is not a mapping — no jobs extracted",
                jobs_node.span.start_line,
            )
        )
        return ()
    out: list[JobObs] = []
    for k, job in jobs_node.pairs:
        if k.kind != "scalar":
            continue
        if job.kind != "map":
            diags.append(
                YamlDiagnostic(
                    "job-not-map", "warning",
                    f"job {k.value!r} is not a mapping — skipped",
                    job.span.start_line,
                )
            )
            continue
        needs, needs_span = _needs(job)
        axes, mdyn, mextras, ff, mp = _matrix(job)
        env, env_dyn = _environment(job)
        runs_on_node = job.get("runs-on")
        secrets_node = job.get("secrets")
        secrets_mode = ""
        if secrets_node is not None:
            sv = _raw_scalar(secrets_node)
            secrets_mode = "inherit" if sv == "inherit" else "declared"
        outputs = job.get("outputs")
        out.append(
            JobObs(
                job_id=k.value,
                display_name=_scalar(job.get("name")),
                uses=_scalar(job.get("uses")),
                needs=needs,
                runs_on=",".join(
                    sanitize(t)
                    for t in (
                        runs_on_node.scalar_list()
                        if runs_on_node is not None else ()
                    )
                ),
                environment=env,
                environment_dynamic=env_dyn,
                if_expr=_scalar(job.get("if")),
                matrix_axes=axes,
                matrix_dynamic=mdyn,
                matrix_extras=mextras,
                fail_fast=ff,
                max_parallel=mp,
                permissions=_map_summary(job.get("permissions")),
                env_keys=_csv_keys(job.get("env")),
                secrets_mode=secrets_mode,
                concurrency=_scalar(job.get("concurrency")),
                outputs=(
                    len(outputs.pairs)
                    if outputs is not None and outputs.kind == "map"
                    else 0
                ),
                steps=_steps(job, diags),
                needs_span=needs_span,
                span=job.span,
            )
        )
    return tuple(out)


def parse_workflow(
    root: YNode | None, path: str
) -> WorkflowParse:
    """Observação semântica de um workflow; ``None`` se não-executável."""
    diags: list[YamlDiagnostic] = []
    if root is None:
        return WorkflowParse(None, ())
    if root.kind != "map":
        diags.append(
            YamlDiagnostic(
                "workflow-not-map", "error",
                "workflow document is not a mapping — no extraction",
                root.span.start_line,
            )
        )
        return WorkflowParse(None, tuple(diags))
    stem = PurePosixPath(path).name
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    name_node = root.get("name")
    declared = _scalar(name_node)
    name = declared or stem
    triggers = _triggers(root.get("on"), diags)
    jobs = _jobs(root.get("jobs"), diags)
    if not jobs:
        diags.append(
            YamlDiagnostic(
                "no-jobs", "warning",
                "workflow declares no usable 'jobs:' mapping",
                root.span.start_line,
            )
        )
    obs = WorkflowObs(
        path=path, name=name, name_declared=bool(declared),
        triggers=triggers, jobs=jobs,
        permissions=_map_summary(root.get("permissions")),
        env_keys=_csv_keys(root.get("env")),
        concurrency=_scalar(root.get("concurrency")),
        span=root.span,
    )
    return WorkflowParse(obs, tuple(diags))
