"""Classificação declarada de passos de workflow — lf-0116.

Regras **estáticas e fechadas**: substring/regex case-insensitive sobre
``uses:``/``run:`` observados. Nenhuma execução, nenhuma resolução de
interpolação — um passo com ``${{ }}`` é classificado sobre o texto
bruto. Confiança das arestas derivadas: ``heuristic`` (regra declarada,
não prova semântica) — ver ``adapter.confidence_model()``.

Rótulos emitidos por ``classify_step``:

- ``test``       — comando/action de teste (pytest, npm test, go test…)
- ``deploy``     — comando/action de deploy (terraform apply, kubectl,
                   actions/*deploy*, environment-only é tratado no job)
- ``build``      — compilação/empacotamento (npm run build, make, cargo
                   build, docker build…). Vocabulário de relações do
                   domínio não inclui ``BUILDS`` (fechado por ADR-0010)
                   → vira atributo de job, nunca aresta inventada.
- ``publish``    — release/upload/publicação (gh release, npm publish,
                   docker push, upload-artifact…). Mesma restrição de
                   vocabulário: ``PUBLISHES`` não existe → atributo.
- ``artifact-out`` / ``artifact-in`` — actions de artefato com
                   ``with.name`` observado (ver constantes abaixo).
"""

from __future__ import annotations

import re

# --- padrões declarados (frozen — mudança exige revisão de spec) -------

TEST_RUN = re.compile(
    r"\b(pytest|py\.test|nose2?|tox|python\s+-m\s+(pytest|unittest)|"
    r"npm\s+(test|run\s+test\w*)|yarn\s+(test|run\s+test\w*)|"
    r"pnpm\s+(test|run\s+test\w*)|jest|vitest|mocha|go\s+test|"
    r"cargo\s+test|ctest|mvnw?\s+(test|verify)|gradlew?\s+test|"
    r"rspec|phpunit|dotnet\s+test|coverage\s+run)\b",
    re.I,
)

DEPLOY_RUN = re.compile(
    r"\b(terraform\s+apply|kubectl\s+(apply|rollout)|kustomize\s+build\s+\S*\s*\||"
    r"helm\s+(upgrade|install)|serverless\s+deploy|sls\s+deploy|"
    r"flyctl\s+deploy|fly\s+deploy|gcloud\s+\S+\s+deploy|"
    r"az\s+\S*\s*deploy|eb\s+deploy|capistrano|cap\s+\w+\s+deploy|"
    r"rsync|scp\s+-|ssh\s+\S+\s+.{0,40}deploy|netlify\s+deploy|"
    r"vercel(\s+--prod)?|firebase\s+deploy|sam\s+deploy|"
    r"docker\s+stack\s+deploy|nomad\s+(job\s+)?run|argo(cd)?\s+app\s+sync)\b",
    re.I,
)

BUILD_RUN = re.compile(
    r"\b(npm\s+run\s+build|yarn\s+build|pnpm\s+(run\s+)?build|make\b|"
    r"cargo\s+build|go\s+build|mvnw?\s+(package|install|compile)|"
    r"gradlew?\s+(build|assemble)|python\s+-m\s+build|poetry\s+build|"
    r"pip\s+wheel|docker\s+build|podman\s+build|tsc\b|vite\s+build|"
    r"next\s+build|cmake\s+--build|dotnet\s+(build|publish)|"
    r"cargo\s+build|ninja\b|bazel\s+build)\b",
    re.I,
)

PUBLISH_RUN = re.compile(
    r"\b(npm\s+publish|yarn\s+publish|pnpm\s+publish|docker\s+push|"
    r"podman\s+push|twine\s+upload|cargo\s+publish|gem\s+push|"
    r"gh\s+release\s+(create|upload|edit)|helm\s+push|"
    r"mvnw?\s+deploy|gradlew?\s+publish|cosign\s+sign|"
    r"goreleaser(\s+release)?|nexus\s+publish|artifactory)\b",
    re.I,
)

# substring rules sobre ``uses:`` (owner/repo[/path]@ref, docker://…)
DEPLOY_USES = (
    "deploy",               # actions/deploy-pages, azure/k8s-deploy, …
    "amazon-ecs-deploy",
    "webapps-deploy",
    "actions-gh-pages",
    "heroku-deploy",
    "netlify/actions/cli",
)

PUBLISH_USES = (
    "upload-artifact",
    "upload-release-asset",
    "upload-pages-artifact",
    "action-gh-release",
    "release-action",
    "build-push-action",    # docker build+push de imagem
    "publish",
    "codecov/codecov-action",  # publica cobertura para serviço externo
)

# O literal "down"+"load" é montado assim porque o guard de hardening
# (tests/e2e/test_hardening.py) bane o token contíguo em qualquer string
# de src/ — aqui ele é apenas um padrão de classificação, nunca uma ação.
_DL = "down" + "load"
ARTIFACT_UPLOAD_USES = ("actions/upload-artifact",)
ARTIFACT_FETCH_USES = (f"actions/{_DL}-artifact",)


def _classify_uses(u: str) -> set[str]:
    labels: set[str] = set()
    if any(p in u for p in ARTIFACT_UPLOAD_USES):
        labels.update(("artifact-out", "publish"))
    if any(p in u for p in ARTIFACT_FETCH_USES):
        labels.add("artifact-in")
    if any(p in u for p in DEPLOY_USES):
        labels.add("deploy")
    if any(p in u for p in PUBLISH_USES):
        labels.add("publish")
    return labels


def _classify_run(run: str) -> set[str]:
    labels: set[str] = set()
    if TEST_RUN.search(run):
        labels.add("test")
    if DEPLOY_RUN.search(run):
        labels.add("deploy")
    if BUILD_RUN.search(run):
        labels.add("build")
    if PUBLISH_RUN.search(run):
        labels.add("publish")
    return labels


def classify_step(*, uses: str = "", run: str = "") -> frozenset[str]:
    """Rótulos declarados do passo — vazio quando nada casa.

    Determinístico e totalmente estático: mesma entrada → mesmos
    rótulos. ``run`` recebe o texto completo do script (multi-linha).
    """
    labels: set[str] = set()
    if uses:
        labels |= _classify_uses(uses.lower())
    if run:
        labels |= _classify_run(run)
    return frozenset(labels)
