"""GitHubActionsAdapter — extração de domínio CI/CD (lf-0116).

Fixture real em ``tests/fixtures/cicd_repo`` (inclui ``broken.yml``
propositalmente malformado e um ``.gitlab-ci.yml`` que a plataforma
declared-optional deve ignorar). Regras verificadas: evidence-first,
estados honestos de resolução, redação de secrets, nunca executar.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains import (
    DiscoveredArtifact,
    DomainAdapter,
    DomainExtractionRequest,
    DomainRegistry,
    validate_extraction,
)
from code_atlas.domains.cicd import (
    DOMAIN_ID,
    OPTIONAL_PLATFORMS,
    GitHubActionsAdapter,
    sanitize,
)
from code_atlas.domains.model import DomainEntityKind

FIXTURE = Path(__file__).parent.parent / "fixtures" / "cicd_repo"
WF_DIR = ".github/workflows"
CI = f"{WF_DIR}/ci.yml"
RELEASE = f"{WF_DIR}/release.yml"
REUSABLE = f"{WF_DIR}/reusable.yml"
BROKEN = f"{WF_DIR}/broken.yml"


def _adapter() -> GitHubActionsAdapter:
    return GitHubActionsAdapter.for_workspace(FIXTURE)


def _req(
    *paths: str, repository: str = "git-remote:test/repo", root: str = ""
) -> DomainExtractionRequest:
    return DomainExtractionRequest(
        repository=repository,
        root=root,
        artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
    )


def _extract(*paths: str, **kw: object) -> object:
    return _adapter().extract(_req(*paths, **kw))  # type: ignore[arg-type]


def _inline_adapter(files: dict[str, str]) -> GitHubActionsAdapter:
    """Adapter com reader em memória — conteúdo injetado, nunca disco."""
    return GitHubActionsAdapter(
        lambda rel: (
            files[rel].encode("utf-8") if rel in files else None
        )
    )


def _inline_req(*paths: str) -> DomainExtractionRequest:
    return _req(*paths)


# --- contrato ---------------------------------------------------------------


def test_implements_domain_adapter_protocol() -> None:
    assert isinstance(_adapter(), DomainAdapter)
    assert _adapter().domain_id == DOMAIN_ID == "cicd"


def test_supported_artifacts_github_only() -> None:
    support = _adapter().supported_artifacts()
    assert support.matches(f"{WF_DIR}/ci.yml")
    assert support.matches(f"{WF_DIR}/w.yaml")
    assert support.matches(f"services/api/{WF_DIR}/x.yml")  # monorepo
    # declared-optional: outras plataformas NÃO são casadas — zero fatos
    assert not support.matches(".gitlab-ci.yml")
    assert not support.matches("azure-pipelines.yml")
    assert not support.matches("Jenkinsfile")
    assert not support.matches(".circleci/config.yml")
    assert not support.matches("docs/notes.yml")
    assert OPTIONAL_PLATFORMS  # fronteira documentada


def test_registry_hosts_adapter() -> None:
    reg = DomainRegistry()
    reg.register(_adapter())
    assert reg.get("cicd") is not None
    out = reg.discovery_candidates([CI, ".gitlab-ci.yml", "README.md"])
    assert out == {"cicd": [CI]}


def test_confidence_model_declared() -> None:
    cm = _adapter().confidence_model()
    assert cm.ceiling is ConfidenceClass.SYNTACTIC
    assert cm.admits(ConfidenceClass.HEURISTIC)


# --- extração real na fixture ----------------------------------------------


def test_extract_entities_and_kinds() -> None:
    res = _extract(CI, RELEASE, REUSABLE)
    assert not res.diagnostics
    kinds = {e.kind for e in res.entities}
    assert kinds == {
        DomainEntityKind.CICD_WORKFLOW, DomainEntityKind.CICD_JOB
    }
    wfs = [e for e in res.entities if e.kind == "cicd_workflow"]
    assert {e.name for e in wfs} == {"CI", "Release", "Reusable Checks"}
    jobs = [e for e in res.entities if e.kind == "cicd_job"]
    # ci(3) + release(4) + reusable(1)
    assert len(jobs) == 8


def test_identity_includes_repo_path_and_name() -> None:
    res = _extract(CI)
    wf = next(e for e in res.entities if e.kind == "cicd_workflow")
    ident = wf.identity
    assert ident.repository == "git-remote:test/repo"
    assert ident.namespace == WF_DIR                       # path dir
    assert ident.qualified_name == "ci.yml#CI"             # file + name
    job = next(
        e for e in res.entities if e.attributes.get("job_id") == "test"
    )
    assert job.identity.qualified_name == "ci.yml#CI/jobs/test"
    # isolamento: outra repo → chave lógica distinta
    res2 = _adapter().extract(
        _req(CI, repository="git-remote:other/repo")
    )
    wf2 = next(e for e in res2.entities if e.kind == "cicd_workflow")
    assert wf2.identity.logical_key() != ident.logical_key()


def test_multi_job_needs_graph() -> None:
    res = _extract(CI)
    deps = [
        r for r in res.relations if r.relation_type == "DEPENDS_ON"
    ]
    by_src = {
        (r.from_identity.qualified_name, r.resolution.status)
        for r in deps
    }
    assert by_src == {
        ("ci.yml#CI/jobs/test", ResolutionStatus.RESOLVED),
        ("ci.yml#CI/jobs/build", ResolutionStatus.RESOLVED),
    }
    targets = {r.to_identity.qualified_name for r in deps}
    assert targets == {"ci.yml#CI/jobs/lint", "ci.yml#CI/jobs/test"}


def test_triggers_push_pr_cron_dispatch() -> None:
    res = _extract(CI, RELEASE)
    trig = [
        (r.from_identity.qualified_name, r.to_unresolved)
        for r in res.relations if r.relation_type == "TRIGGERS"
    ]
    assert ("ci.yml#CI", "push") in trig
    assert ("ci.yml#CI", "pull_request") in trig
    assert ("release.yml#Release", "workflow_dispatch") in trig
    assert any(t[1].startswith("schedule:cron=") for t in trig)
    # triggers são externos — o event bus não está no snapshot
    assert all(
        r.resolution.status is ResolutionStatus.EXTERNAL
        for r in res.relations if r.relation_type == "TRIGGERS"
    )
    wf = next(
        e for e in res.entities
        if e.kind == "cicd_workflow" and e.name == "Release"
    )
    assert wf.attributes["trigger.schedule.cron"] == "17 3 * * 1"


def test_matrix_declared_multiplicity_only() -> None:
    res = _extract(CI)
    job = next(
        e for e in res.entities if e.attributes.get("job_id") == "test"
    )
    # multiplicidade DECLARADA: 2 os x 2 python — nunca expandida
    assert job.attributes["matrix_axes"] == "os=2,python=2"
    assert job.attributes["runs_on"] == "<expr>"  # ${{ matrix.os }}
    assert job.attributes["fail_fast"] == "false"


def test_reusable_workflow_local_resolves_external_stays() -> None:
    res = _extract(RELEASE, REUSABLE)
    refs = [
        r for r in res.relations if r.relation_type == "REFERENCES"
    ]
    local = next(
        r for r in refs
        if r.from_identity.qualified_name.endswith("call-reusable")
    )
    assert local.resolution.status is ResolutionStatus.RESOLVED
    assert (
        local.to_identity.qualified_name == "reusable.yml#Reusable Checks"
    )
    ext = next(
        r for r in refs
        if r.from_identity.qualified_name.endswith("call-external")
    )
    assert ext.resolution.status is ResolutionStatus.EXTERNAL
    assert ext.to_unresolved == (
        "octocat/hello-world/.github/workflows/reusable.yml@v1"
    )


def test_local_ref_missing_stays_unresolved() -> None:
    res = _extract(RELEASE)  # reusable.yml ausente do request
    refs = [
        r for r in res.relations if r.relation_type == "REFERENCES"
    ]
    local = next(
        r for r in refs
        if r.from_identity.qualified_name.endswith("call-reusable")
    )
    assert local.resolution.status is ResolutionStatus.UNRESOLVED
    assert local.to_unresolved.startswith("local:")
    assert any(
        d.code == "local-ref-not-in-artifacts" for d in res.diagnostics
    )


def test_tests_and_deploys_classification() -> None:
    res = _extract(CI, RELEASE)
    tests = [
        r for r in res.relations if r.relation_type == "TESTS"
    ]
    assert tests and all(
        r.resolution.confidence_class is ConfidenceClass.HEURISTIC
        and r.resolution.status is ResolutionStatus.UNRESOLVED
        for r in tests
    )
    test_job = next(
        r for r in tests
        if "jobs/test" in r.from_identity.qualified_name
    )
    assert test_job.to_unresolved == 'step:"Test"'
    deploys = [
        r for r in res.relations if r.relation_type == "DEPLOYS"
    ]
    env_targets = {r.to_unresolved for r in deploys}
    assert "environment:production" in env_targets
    assert "environment:staging" in env_targets
    assert 'step:"Roll out"' in env_targets  # kubectl apply


def test_artifacts_attributes_not_fabricated_relations() -> None:
    """BUILDS/PUBLISHES fora do vocabulário fechado → atributos honestos."""
    res = _extract(CI, RELEASE)
    rel_types = {r.relation_type for r in res.relations}
    assert "BUILDS" not in rel_types and "PUBLISHES" not in rel_types
    test_job = next(
        e for e in res.entities if e.attributes.get("job_id") == "test"
    )
    assert test_job.attributes["artifacts_out"] == "dist"
    assert test_job.attributes["publish_steps"] == "1"
    build_job = next(
        e for e in res.entities if e.attributes.get("job_id") == "build"
    )
    assert build_job.attributes["build_steps"] == "1"
    assert build_job.attributes["artifacts_in"] == "dist"
    rel_job = next(
        e for e in res.entities if e.attributes.get("job_id") == "release"
    )
    assert rel_job.attributes["publish_steps"] == "2"


def test_secrets_and_interpolation_never_concrete() -> None:
    res = _extract(CI, RELEASE, REUSABLE)
    for e in res.entities:
        for k, v in e.attributes.items():
            assert "${{" not in v, (k, v)
            assert "secrets." not in v, (k, v)
    for r in res.relations:
        for txt in (r.to_unresolved, r.to_symbol):
            assert "secrets." not in txt
    test_job = next(
        e for e in res.entities if e.attributes.get("job_id") == "test"
    )
    # env var NAMES são observáveis; valores (secretos) nunca são lidos
    assert test_job.attributes["runs_on"] == "<expr>"


def test_malformed_yaml_is_diagnostics_not_crash() -> None:
    res = _extract(BROKEN)
    assert res.entities or res.diagnostics  # nunca exceção
    codes = {d.code for d in res.diagnostics}
    assert "yaml-tab-indent" in codes


def test_gitlab_file_produces_nothing() -> None:
    """Plataforma declared-optional: mesmo forçando o artifact no
    request, doc sem 'on:'/'jobs:' não vira workflow — diagnóstico."""
    res = _extract(".gitlab-ci.yml")
    assert res.entities == ()
    assert any(d.code == "not-a-gha-workflow" for d in res.diagnostics)


def test_evidence_points_to_real_content() -> None:
    res = _extract(CI)
    assert len(res.evidence) == 1
    ev = res.evidence[0]
    content = (FIXTURE / CI).read_bytes()
    assert ev.content_hash == hashlib.sha256(content).hexdigest()
    assert ev.extractor == "domain:cicd"
    for e in res.entities:
        assert e.evidence and all(
            0 <= i < len(res.evidence) for i in e.evidence
        )
        assert e.content_hash == ev.content_hash
    assert validate_extraction(_adapter(), _req(CI), res) == []


def test_all_files_fixture_end_to_end_valid() -> None:
    res = _extract(CI, RELEASE, REUSABLE, BROKEN)
    assert validate_extraction(
        _adapter(), _req(CI, RELEASE, REUSABLE, BROKEN), res
    ) == []
    # broken.yml produz diagnósticos sem derrubar os demais
    assert any(d.path == BROKEN for d in res.diagnostics)


def test_needs_unknown_job_is_unresolved_plus_diagnostic() -> None:
    files = {
        "w.yml": (
            "on: push\njobs:\n  a:\n    needs: ghost\n"
            "    steps:\n      - run: echo hi\n"
        ),
    }
    res = _inline_adapter(files).extract(_inline_req("w.yml"))
    deps = [r for r in res.relations if r.relation_type == "DEPENDS_ON"]
    assert deps and deps[0].resolution.status is ResolutionStatus.UNRESOLVED
    assert deps[0].to_unresolved == "ghost"
    assert any(d.code == "unresolved-needs" for d in res.diagnostics)


def test_hash_mismatch_skips_artifact() -> None:
    bad = DiscoveredArtifact(path=CI, sha256="0" * 64)
    res = _adapter().extract(
        DomainExtractionRequest(repository="r", artifacts=(bad,))
    )
    assert res.entities == ()
    assert any(
        d.code == "artifact-hash-mismatch" for d in res.diagnostics
    )


def test_missing_content_reader_is_honest() -> None:
    adapter = GitHubActionsAdapter()  # sem reader
    res = adapter.extract(_req(CI))
    assert res.entities == ()
    assert any(
        d.code == "missing-content-reader" for d in res.diagnostics
    )


def test_missing_artifact_content() -> None:
    res = _extract(f"{WF_DIR}/nao-existe.yml")
    assert res.entities == ()
    assert any(d.code == "missing-content" for d in res.diagnostics)


def test_sanitize_rules() -> None:
    assert sanitize("plain") == "plain"
    assert sanitize("${{ matrix.os }}") == "<expr>"
    assert sanitize("${{ secrets.TOKEN }}") == "<secret>"
    assert sanitize("x ${{ env.Y }} y") == "x <expr> y"
    # expressão não-fechada: redige até fim da linha — nunca vaza
    assert sanitize("${{ secrets.A") == "<secret>"


def test_inline_multi_platform_boundary() -> None:
    files = {
        "w.yml": "name: X\non: push\njobs:\n  j:\n    runs-on: x\n    steps:\n      - run: echo\n",
    }
    res = _inline_adapter(files).extract(_inline_req("w.yml"))
    wf = next(e for e in res.entities if e.kind == "cicd_workflow")
    assert wf.name == "X"


def test_dynamic_environment_not_deployed_target() -> None:
    files = {
        "d.yml": (
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  deploy:\n"
            "    runs-on: ubuntu-latest\n"
            "    environment: ${{ inputs.env }}\n"
            "    steps:\n"
            "      - run: echo hi\n"
        ),
    }
    res = _inline_adapter(files).extract(_inline_req("d.yml"))
    job = res.entities[-1]
    assert job.attributes["environment"] == "<dynamic>"
    assert not any(
        r.relation_type == "DEPLOYS"
        and r.to_unresolved.startswith("environment:")
        for r in res.relations
    )


def test_step_uses_local_action_unresolved() -> None:
    files = {
        "a.yml": (
            "on: push\njobs:\n  j:\n    runs-on: x\n    steps:\n"
            "      - uses: ./.github/actions/my-action\n"
        ),
    }
    res = _inline_adapter(files).extract(_inline_req("a.yml"))
    refs = [r for r in res.relations if r.relation_type == "REFERENCES"]
    assert refs and refs[0].resolution.status is ResolutionStatus.UNRESOLVED
    assert refs[0].to_unresolved == "local:./.github/actions/my-action"


def test_monorepo_root_enters_identity() -> None:
    res = _adapter().extract(_req(CI, root="services/api"))
    wf = next(e for e in res.entities if e.kind == "cicd_workflow")
    assert wf.identity.root == "services/api"


def test_extract_never_raises_on_garbage(tmp_path: Path) -> None:
    """Bytes não-UTF-8 reais no disco → decode-error, nunca exceção."""
    bad = tmp_path / ".github" / "workflows"
    bad.mkdir(parents=True)
    (bad / "g.yml").write_bytes(b"\x00\x01\xfe invalid \x82")
    res = GitHubActionsAdapter.for_workspace(tmp_path).extract(
        _req(".github/workflows/g.yml")
    )
    assert res.entities == ()
    assert any(d.code == "decode-error" for d in res.diagnostics)
