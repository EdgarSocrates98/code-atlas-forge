"""Adapter de domínio CI/CD — GitHub Actions (lf-0116, Cycle 7 Phase 8).

Plataforma primária: **GitHub Actions** (``.github/workflows/*.yml``).
Outras plataformas são **declared-optional** — GitLab CI, Azure
Pipelines, Jenkinsfile e CircleCI são reconhecíveis por convenção de
path mas NÃO são parseados neste slice: seus arquivos simplesmente não
casam com ``supported_artifacts`` e produzem zero fatos (ausência
honesta, nunca fabricação). Ver ``OPTIONAL_PLATFORMS``.

Regras duras herdadas da spec/contrato:

- sem execução de workflow/job/step; sem resolução de secrets;
- ``${{ ... }}`` nunca vira valor concreto (``<expr>``/``<secret>``);
- actions e workflows reutilizáveis externos são ``REFERENCES``
  ``external`` — nunca baixados; ``uses: ./...`` só resolve quando o
  arquivo está entre os artefatos descobertos do request;
- ``BUILDS``/``PUBLISHES`` não existem no vocabulário fechado de
  relações (``DOMAIN_RELATION_TYPES``, ADR-0010 — extensão exige
  spec+ADR): passos classificados como build/publish viram **atributos
  de job** com evidência, nunca arestas inventadas.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.cicd import yamlmini
from code_atlas.domains.cicd.workflow import (
    JobObs,
    StepObs,
    WorkflowObs,
    parse_workflow,
    step_descriptor,
)
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveredArtifact,
    DiscoveryRule,
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
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

DOMAIN_ID = "cicd"
ADAPTER_VERSION = "0.1.0"
PLATFORM = "github-actions"

# Plataformas reconhecidas por convenção de path e declaradas opcionais:
# nenhuma é parseada neste slice — documentação honesta de fronteira.
OPTIONAL_PLATFORMS: tuple[str, ...] = (
    "gitlab-ci:.gitlab-ci.yml",
    "azure-pipelines:azure-pipelines.yml",
    "jenkins:Jenkinsfile",
    "circleci:.circleci/config.yml",
)

_WORKFLOW_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "*/.github/workflows/*.yml",
    "*/.github/workflows/*.yaml",
)

ContentReader = Callable[[str], bytes | None]


def _workspace_reader(root: Path) -> ContentReader:
    """Reader restrito ao workspace: só lê paths relativos declarados."""
    base = root.resolve()

    def _read(rel: str) -> bytes | None:
        # recusa traversal: path deve permanecer sob ``base``
        if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
            return None
        target = (base / rel).resolve()
        if not str(target).startswith(str(base) + "/") and target != base:
            return None
        if not target.is_file():
            return None
        try:
            return target.read_bytes()
        except OSError:
            return None

    return _read


class GitHubActionsAdapter:
    """``DomainAdapter`` para workflows GitHub Actions."""

    def __init__(self, read_content: ContentReader | None = None) -> None:
        """``read_content`` injeta acesso a bytes por path relativo.

        Sem reader → cada artefato gera diagnóstico ``missing-content``
        e zero entidades (a extração nunca inventa conteúdo).
        """
        self._read = read_content

    @classmethod
    def for_workspace(cls, root: Path) -> GitHubActionsAdapter:
        """Adapter que lê artefatos do request sob ``root`` no disco.

        Restrito a paths POSIX relativos declarados em
        ``request.artifacts`` — traversal e paths absolutos recusados.
        """
        return cls(_workspace_reader(root))

    # --- contrato ---------------------------------------------------------

    @property
    def domain_id(self) -> str:
        return DOMAIN_ID

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(globs=_WORKFLOW_GLOBS)

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {DomainEntityKind.CICD_WORKFLOW, DomainEntityKind.CICD_JOB}
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset(
            {
                "CONTAINS",
                str(DomainRelationKind.TRIGGERS),
                str(DomainRelationKind.DEPENDS_ON),
                str(DomainRelationKind.TESTS),
                str(DomainRelationKind.DEPLOYS),
                str(DomainRelationKind.REFERENCES),
            }
        )

    def confidence_model(self) -> ConfidenceModel:
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=True,
            notes=(
                "structural edges (CONTAINS/TRIGGERS/DEPENDS_ON/"
                "REFERENCES) are syntactic; step classification edges "
                "(TESTS/DEPLOYS by declared patterns) are heuristic; "
                "inferred is permitted but never reports resolved"
            ),
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (
            DiscoveryRule(
                glob=".github/workflows/*.yml",
                artifact_kind="gha-workflow",
                notes="GitHub Actions workflow (primary platform)",
            ),
            DiscoveryRule(
                glob=".github/workflows/*.yaml",
                artifact_kind="gha-workflow",
                notes="GitHub Actions workflow, .yaml spelling",
            ),
            DiscoveryRule(
                glob="*/.github/workflows/*.yml",
                artifact_kind="gha-workflow",
                notes="nested workflow in monorepo sub-root",
            ),
            DiscoveryRule(
                glob="*/.github/workflows/*.yaml",
                artifact_kind="gha-workflow",
                notes="nested workflow in monorepo sub-root",
            ),
        )

    # --- extração ---------------------------------------------------------

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        artifacts = sorted(request.artifacts, key=lambda a: a.path)
        entities: list[ExtractedDomainEntity] = []
        relations: list[ExtractedDomainRelation] = []
        evidence: list[EvidenceRecord] = []
        diagnostics: list[DomainDiagnostic] = []
        stats: dict[str, int] = {
            "files_seen": len(artifacts), "files_parsed": 0,
            "workflows": 0, "jobs": 0, "steps": 0, "triggers": 0,
            "secrets_redacted": 0, "expressions_masked": 0,
            "diagnostics": 0,
        }

        # Fase 1 — parse de todos os artefatos (paths → obs) antes de
        # emitir relações: ``uses: ./...`` só resolve com o mapa cheio.
        parsed: dict[str, tuple[WorkflowObs, str, int]] = {}
        # path -> (obs, sha256, evidence_index)
        for art in artifacts:
            out = self._extract_file(
                art, request, entities, evidence, diagnostics, stats,
            )
            if out is not None:
                parsed[art.path] = out

        # Fase 2 — relações por workflow/jobs já com identidades certas.
        wf_identity: dict[str, DomainIdentity] = {
            path: self._wf_identity(request, path, obs)
            for path, (obs, _sha, _ev) in parsed.items()
        }
        for path, (obs, _sha, evi) in parsed.items():
            self._emit_workflow_relations(
                request, obs, path, evi, wf_identity,
                relations, diagnostics, stats,
            )

        diagnostics = self._dedup_diags(diagnostics)
        stats["diagnostics"] = len(diagnostics)
        return DomainExtractionResult(
            domain_id=DOMAIN_ID,
            adapter_version=ADAPTER_VERSION,
            entities=tuple(entities),
            relations=tuple(relations),
            evidence=tuple(evidence),
            diagnostics=tuple(diagnostics),
            stats=stats,
        )

    # --- fase 1: arquivo → entidades + evidência ---------------------------

    def _load_document(
        self,
        art: DiscoveredArtifact,
        _d: Callable[[str, str, str], None],
    ) -> tuple[yamlmini.YNode, str] | None:
        """Lê bytes do artefato, valida hash/UTF-8 e parseia YAML.

        Retorna ``(root, sha256)``; ``None`` + diagnóstico em qualquer
        falha — nunca exceção.
        """
        if self._read is None:
            _d("missing-content-reader", "warning",
               "no content reader configured; artifact not parsed")
            return None
        content = self._read(art.path)
        if content is None:
            _d("missing-content", "warning",
               "artifact declared but content unavailable")
            return None
        sha = hashlib.sha256(content).hexdigest()
        if art.sha256 and art.sha256 != sha:
            _d(
                "artifact-hash-mismatch", "error",
                "discovered sha256 does not match content; artifact "
                "skipped to keep evidence honest",
            )
            return None
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            _d("decode-error", "error", f"not valid UTF-8: {exc}")
            return None

        result = yamlmini.parse(text)
        if result.root is None:
            if not result.diagnostics:
                _d("empty-document", "info", "no YAML document content")
            else:
                for yd in result.diagnostics:
                    _d(
                        f"yaml-{yd.code}", yd.severity,
                        f"line {yd.line}: {yd.message}"
                        if yd.line else yd.message,
                    )
            return None
        for yd in result.diagnostics:
            _d(
                f"yaml-{yd.code}", yd.severity,
                f"line {yd.line}: {yd.message}" if yd.line else yd.message,
            )
        return result.root, sha

    def _extract_file(
        self,
        art: DiscoveredArtifact,
        request: DomainExtractionRequest,
        entities: list[ExtractedDomainEntity],
        evidence: list[EvidenceRecord],
        diagnostics: list[DomainDiagnostic],
        stats: dict[str, int],
    ) -> tuple[WorkflowObs, str, int] | None:
        def _d(code: str, sev: str, msg: str) -> None:
            diagnostics.append(
                DomainDiagnostic(code, sev, msg, art.path, DOMAIN_ID)
            )

        loaded = self._load_document(art, _d)
        if loaded is None:
            return None
        root_node, sha = loaded

        parsed = parse_workflow(root_node, art.path)
        for yd in parsed.diagnostics:
            diagnostics.append(
                DomainDiagnostic(
                    f"gha-{yd.code}", yd.severity,
                    f"line {yd.line}: {yd.message}" if yd.line else yd.message,
                    art.path, DOMAIN_ID,
                )
            )
        obs = parsed.obs
        if obs is None:
            return None
        if not obs.triggers and not obs.jobs:
            _d(
                "not-a-gha-workflow", "info",
                "YAML document has neither 'on:' triggers nor 'jobs:' — "
                "not a GitHub Actions workflow; no facts emitted",
            )
            return None

        stats["files_parsed"] += 1
        evi = len(evidence)
        evidence.append(
            evidence_record(
                adapter_domain=DOMAIN_ID,
                adapter_version=ADAPTER_VERSION,
                path=art.path, content_hash=sha, span=root_node.span,
                notes=f"platform={PLATFORM}",
            )
        )

        wf_id = self._wf_identity(request, art.path, obs)
        wf_attrs = self._wf_attrs(obs, stats)
        entities.append(
            ExtractedDomainEntity(
                identity=wf_id, kind=DomainEntityKind.CICD_WORKFLOW,
                name=obs.name, path=art.path, span=obs.span,
                content_hash=sha, attributes=wf_attrs, evidence=(evi,),
            )
        )
        stats["workflows"] += 1
        stats["triggers"] += len(obs.triggers)

        for job in obs.jobs:
            entities.append(
                self._job_entity(
                    request, art.path, obs, job, sha, evi, stats
                )
            )
        return obs, sha, evi

    @staticmethod
    def _wf_attrs(obs: WorkflowObs, stats: dict[str, int]) -> dict[str, str]:
        """Atributos observados do workflow (triggers, permissions…)."""
        attrs: dict[str, str] = {
            "platform": PLATFORM,
            "name": obs.name,
            "name_declared": "true" if obs.name_declared else "false",
            "triggers": ",".join(t.kind for t in obs.triggers),
        }
        for trig in obs.triggers:
            for dk, dv in trig.details:
                stats["expressions_masked"] += dv.count("<expr>")
                stats["secrets_redacted"] += dv.count("<secret>")
                attrs[f"trigger.{trig.kind}.{dk}"] = dv
        if obs.permissions:
            attrs["permissions"] = obs.permissions
        if obs.env_keys:
            attrs["env_keys"] = ",".join(obs.env_keys)
        if obs.concurrency:
            attrs["concurrency"] = obs.concurrency
        return attrs

    # --- identidades --------------------------------------------------------

    @staticmethod
    def _namespace(path: str) -> str:
        parent = str(PurePosixPath(path).parent)
        return "" if parent == "." else parent

    def _wf_identity(
        self,
        request: DomainExtractionRequest,
        path: str,
        obs: WorkflowObs,
    ) -> DomainIdentity:
        """repo + root + path(ns+basename) + nome declarado do workflow."""
        return DomainIdentity(
            domain=DOMAIN_ID,
            repository=request.repository,
            root=request.root,
            namespace=self._namespace(path),
            environment=request.environment,
            qualified_name=f"{PurePosixPath(path).name}#{obs.name}",
        )

    def _job_identity(
        self, wf_id: DomainIdentity, job: JobObs
    ) -> DomainIdentity:
        return DomainIdentity(
            domain=wf_id.domain, repository=wf_id.repository,
            root=wf_id.root, catalog=wf_id.catalog,
            namespace=wf_id.namespace, environment=wf_id.environment,
            qualified_name=f"{wf_id.qualified_name}/jobs/{job.job_id}",
            snapshot=wf_id.snapshot,
        )

    # --- entidades de job --------------------------------------------------

    def _job_entity(
        self,
        request: DomainExtractionRequest,
        path: str,
        obs: WorkflowObs,
        job: JobObs,
        sha: str,
        evi: int,
        stats: dict[str, int],
    ) -> ExtractedDomainEntity:
        stats["jobs"] += 1
        stats["steps"] += len(job.steps)
        attrs = self._job_static_attrs(job)
        attrs.update(self._job_class_attrs(job))
        stats["expressions_masked"] += sum(
            v.count("<expr>") for v in attrs.values()
        )
        stats["secrets_redacted"] += sum(
            v.count("<secret>") for v in attrs.values()
        )
        return ExtractedDomainEntity(
            identity=self._job_identity(
                self._wf_identity(request, path, obs), job
            ),
            kind=DomainEntityKind.CICD_JOB,
            name=job.display_name or job.job_id,
            path=path, span=job.span, content_hash=sha,
            attributes=attrs, evidence=(evi,),
        )

    @staticmethod
    def _job_static_attrs(job: JobObs) -> dict[str, str]:
        """Atributos estruturais observados diretamente no job."""
        attrs: dict[str, str] = {"job_id": job.job_id}
        for key, val in (
            ("display_name", job.display_name),
            ("calls_reusable", job.uses),
            ("runs_on", job.runs_on),
            ("if", job.if_expr),
            ("fail_fast", job.fail_fast),
            ("max_parallel", job.max_parallel),
            ("permissions", job.permissions),
            ("secrets", job.secrets_mode),
            ("concurrency", job.concurrency),
        ):
            if val:
                attrs[key] = val
        if job.environment:
            attrs["environment"] = job.environment
        elif job.environment_dynamic:
            attrs["environment"] = "<dynamic>"
        for key, vals in (
            ("matrix_axes", job.matrix_axes),
            ("matrix_extras", job.matrix_extras),
            ("env_keys", job.env_keys),
            ("needs", job.needs),
        ):
            if vals:
                attrs[key] = ",".join(vals)
        if job.matrix_dynamic:
            attrs["matrix_dynamic"] = "true"
        if job.outputs:
            attrs["outputs_declared"] = str(job.outputs)
        return attrs

    @staticmethod
    def _job_class_attrs(job: JobObs) -> dict[str, str]:
        """Classificação declarada → atributos evidence-backed.

        ``BUILDS``/``PUBLISHES`` não existem no vocabulário fechado de
        relações (ADR-0010) — viram atributos, nunca arestas inventadas.
        """
        attrs: dict[str, str] = {"step_count": str(len(job.steps))}
        for label, key in (
            ("test", "test_steps"), ("deploy", "deploy_steps"),
            ("build", "build_steps"), ("publish", "publish_steps"),
        ):
            n = sum(1 for s in job.steps if label in s.classes)
            if n:
                attrs[key] = str(n)
        outs = [a for s in job.steps for a in s.artifacts_out]
        ins = [a for s in job.steps for a in s.artifacts_in]
        if outs:
            attrs["artifacts_out"] = ",".join(outs)
        if ins:
            attrs["artifacts_in"] = ",".join(ins)
        return attrs

    # --- fase 2: relações ---------------------------------------------------

    def _emit_workflow_relations(
        self,
        request: DomainExtractionRequest,
        obs: WorkflowObs,
        path: str,
        evi: int,
        wf_identity: Mapping[str, DomainIdentity],
        relations: list[ExtractedDomainRelation],
        diagnostics: list[DomainDiagnostic],
        stats: dict[str, int],
    ) -> None:
        wf_id = wf_identity[path]
        resolver = domain_extractor_id(DOMAIN_ID)

        def _res(
            status: ResolutionStatus,
            conf: ConfidenceClass,
            target: DomainIdentity | None = None,
            text: str = "",
        ) -> DomainResolution:
            return DomainResolution(
                status=status, confidence_class=conf, resolver=resolver,
                target=target, unresolved_text=text,
            )

        # TRIGGERS: workflow → evento externo (GitHub event bus fora do
        # snapshot) — alvo textual honesto, status external.
        for trig in obs.triggers:
            relations.append(
                ExtractedDomainRelation(
                    relation_type=str(DomainRelationKind.TRIGGERS),
                    path=path, from_identity=wf_id,
                    to_unresolved=trig.descriptor,
                    resolution=_res(
                        ResolutionStatus.EXTERNAL,
                        ConfidenceClass.SYNTACTIC,
                        text=trig.descriptor,
                    ),
                    span=trig.span, evidence=(evi,),
                )
            )

        for job in obs.jobs:
            job_id = self._job_identity(wf_id, job)

            # CONTAINS: workflow contém job — resolvido no mesmo arquivo.
            relations.append(
                ExtractedDomainRelation(
                    relation_type="CONTAINS",
                    path=path, from_identity=wf_id, to_identity=job_id,
                    resolution=_res(
                        ResolutionStatus.RESOLVED,
                        ConfidenceClass.SYNTACTIC, target=job_id,
                    ),
                    span=job.span, evidence=(evi,),
                )
            )

            # DEPENDS_ON: needs: → job(s) do mesmo workflow.
            for need in job.needs:
                target_obs = next(
                    (j for j in obs.jobs if j.job_id == need), None
                )
                if target_obs is not None:
                    tgt = self._job_identity(wf_id, target_obs)
                    relations.append(
                        ExtractedDomainRelation(
                            relation_type=str(DomainRelationKind.DEPENDS_ON),
                            path=path, from_identity=job_id,
                            to_identity=tgt,
                            resolution=_res(
                                ResolutionStatus.RESOLVED,
                                ConfidenceClass.SYNTACTIC, target=tgt,
                            ),
                            span=job.needs_span or job.span,
                            evidence=(evi,),
                        )
                    )
                else:
                    diagnostics.append(
                        DomainDiagnostic(
                            "unresolved-needs", "warning",
                            f"job {job.job_id!r} needs {need!r} which is "
                            "not a declared job in this workflow",
                            path, DOMAIN_ID,
                        )
                    )
                    relations.append(
                        ExtractedDomainRelation(
                            relation_type=str(DomainRelationKind.DEPENDS_ON),
                            path=path, from_identity=job_id,
                            to_unresolved=need,
                            resolution=_res(
                                ResolutionStatus.UNRESOLVED,
                                ConfidenceClass.SYNTACTIC, text=need,
                            ),
                            span=job.needs_span or job.span,
                            evidence=(evi,),
                        )
                    )

            # REFERENCES: job-level ``uses:`` = reusable workflow.
            if job.uses:
                self._emit_uses(
                    job.uses, job_id, path, evi, wf_identity,
                    relations, diagnostics, _res, job.span,
                )

            # DEPLOYS: environment estático → alvo externo nomeado.
            if job.environment:
                relations.append(
                    ExtractedDomainRelation(
                        relation_type=str(DomainRelationKind.DEPLOYS),
                        path=path, from_identity=job_id,
                        to_unresolved=f"environment:{job.environment}",
                        resolution=_res(
                            ResolutionStatus.EXTERNAL,
                            ConfidenceClass.SYNTACTIC,
                            text=f"environment:{job.environment}",
                        ),
                        span=job.span, evidence=(evi,),
                    )
                )

            # arestas por passo: TESTS/DEPLOYS/REFERENCES
            for step in job.steps:
                self._emit_step_relations(
                    step, job_id, path, evi, wf_identity,
                    relations, diagnostics, _res,
                )

    def _emit_uses(
        self,
        uses: str,
        from_id: DomainIdentity,
        path: str,
        evi: int,
        wf_identity: Mapping[str, DomainIdentity],
        relations: list[ExtractedDomainRelation],
        diagnostics: list[DomainDiagnostic],
        _res: Callable[..., DomainResolution],
        span: SourceSpan | None,
    ) -> None:
        """``uses:`` → REFERENCES; ``./`` resolve só se artefato existe."""
        if uses.startswith("./") or uses.startswith(".\\"):
            norm = uses[2:].replace("\\", "/")
            if norm in wf_identity:
                tgt = wf_identity[norm]
                relations.append(
                    ExtractedDomainRelation(
                        relation_type=str(DomainRelationKind.REFERENCES),
                        path=path, from_identity=from_id, to_identity=tgt,
                        resolution=_res(
                            ResolutionStatus.RESOLVED,
                            ConfidenceClass.SYNTACTIC, target=tgt,
                        ),
                        span=span, evidence=(evi,),
                    )
                )
            else:
                diagnostics.append(
                    DomainDiagnostic(
                        "local-ref-not-in-artifacts", "info",
                        f"local 'uses:' {uses!r} not among discovered "
                        "artifacts — reported unresolved",
                        path, DOMAIN_ID,
                    )
                )
                relations.append(
                    ExtractedDomainRelation(
                        relation_type=str(DomainRelationKind.REFERENCES),
                        path=path, from_identity=from_id,
                        to_unresolved=f"local:{uses}",
                        resolution=_res(
                            ResolutionStatus.UNRESOLVED,
                            ConfidenceClass.SYNTACTIC, text=uses,
                        ),
                        span=span, evidence=(evi,),
                    )
                )
            return
        # externo: owner/repo[/path]@ref ou docker:// — nunca resolvido
        relations.append(
            ExtractedDomainRelation(
                relation_type=str(DomainRelationKind.REFERENCES),
                path=path, from_identity=from_id,
                to_unresolved=uses,
                resolution=_res(
                    ResolutionStatus.EXTERNAL,
                    ConfidenceClass.SYNTACTIC, text=uses,
                ),
                span=span, evidence=(evi,),
            )
        )

    def _emit_step_relations(
        self,
        step: StepObs,
        job_id: DomainIdentity,
        path: str,
        evi: int,
        wf_identity: Mapping[str, DomainIdentity],
        relations: list[ExtractedDomainRelation],
        diagnostics: list[DomainDiagnostic],
        _res: Callable[..., DomainResolution],
    ) -> None:
        if step.uses:
            self._emit_uses(
                step.uses, job_id, path, evi, wf_identity,
                relations, diagnostics, _res, step.span,
            )
        desc = step_descriptor(step)
        if "test" in step.classes:
            relations.append(
                ExtractedDomainRelation(
                    relation_type=str(DomainRelationKind.TESTS),
                    path=path, from_identity=job_id,
                    to_unresolved=desc,
                    resolution=_res(
                        ResolutionStatus.UNRESOLVED,
                        ConfidenceClass.HEURISTIC, text=desc,
                    ),
                    span=step.span, evidence=(evi,),
                )
            )
        if "deploy" in step.classes:
            relations.append(
                ExtractedDomainRelation(
                    relation_type=str(DomainRelationKind.DEPLOYS),
                    path=path, from_identity=job_id,
                    to_unresolved=desc,
                    resolution=_res(
                        ResolutionStatus.UNRESOLVED,
                        ConfidenceClass.HEURISTIC, text=desc,
                    ),
                    span=step.span, evidence=(evi,),
                )
            )

    @staticmethod
    def _dedup_diags(
        diags: list[DomainDiagnostic],
    ) -> list[DomainDiagnostic]:
        seen: set[tuple[str, str, str]] = set()
        out: list[DomainDiagnostic] = []
        for d in diags:
            key = (d.code, d.path, d.message)
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
        return out
