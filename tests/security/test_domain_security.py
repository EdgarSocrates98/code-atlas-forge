"""Security/privacy dos adapters de domínio (Cycle 7 Ph.18, lf-0125).

Asserções de fronteira sobre ``code_atlas.domains`` / ``federation`` /
``eval`` e sobre a saída persistida dos adapters:

- **redação de segredos** — valores secretos das fixtures NUNCA
  aparecem em entidades, relações, evidência, diagnósticos ou rows;
- **sem rede** — nenhum import http/socket/urllib/boto nos pacotes;
- **loopback preservado** — a fronteira ``api`` segue 127.0.0.1-only e
  nenhum código de domínio vincula endereço público;
- **sem execução** — adapters usam ast/tokenize/texto: nada de
  subprocess/eval/exec sobre conteúdo indexado;
- **marcadores, não valores** — ``${{ secrets.* }}`` → ``<secret>`` e
  interpolações ``${...}`` → ``${dynamic}`` nunca materializam;
- **opcionalidade real** — pacotes de domínio importam apenas stdlib +
  ``code_atlas`` (zero dependência mandatória nova);
- **paths via pathlib** — nenhum ``os.path``/concatenação de separador
  no código de domínio (Windows compat).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import re
import sys
from pathlib import Path

import pytest

from code_atlas.domains.cicd import GitHubActionsAdapter
from code_atlas.domains.cli_surface import builtin_registry
from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.domains.terraform.refs import is_sensitive_name
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

SRC = Path(__file__).resolve().parents[2] / "src" / "code_atlas"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TF_FIXTURE = FIXTURES / "terraform_infra"
CICD_FIXTURE = FIXTURES / "cicd_repo"

DOMAIN_PKGS = (SRC / "domains", SRC / "federation", SRC / "eval")

# módulos de rede/SDK cloud — nenhum pode aparecer em código de domínio
_NETWORK_BANNED = {
    "socket", "urllib", "http", "httpx", "requests", "aiohttp",
    "websocket", "ftplib", "smtplib", "telnetlib", "paramiko", "grpc",
    "ssl", "asyncio", "boto", "boto3", "botocore", "azure", "google",
}

# execução de processos/conteúdo — proibida dentro dos pacotes de domínio
_EXEC_BANNED_MODULES = {"subprocess", "pty", "shutil", "multiprocessing"}
_EXEC_BANNED_NAMES = {"eval", "exec", "compile", "__import__", "import_module"}
_EXEC_BANNED_ATTRS = {
    "system", "popen", "spawnl", "spawnlp", "spawnv", "spawnvp",
    "execl", "execlp", "execv", "execve", "execvp", "Popen",
}


def _domain_sources() -> list[Path]:
    out: list[Path] = []
    for pkg in DOMAIN_PKGS:
        out.extend(
            p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts
        )
    return sorted(out)


def _harvest_strings(res: DomainExtractionResult) -> str:
    """Todo texto armazenável do resultado — varredura completa."""
    out: list[str] = []
    for e in res.entities:
        out.append(e.name)
        out.append(e.path)
        out.append(e.identity.qualified_name)
        out.append(e.identity.canonical_key())
        out.extend(e.attributes.keys())
        out.extend(str(v) for v in e.attributes.values())
    for r in res.relations:
        out.append(r.relation_type)
        out.append(r.path)
        out.append(r.to_unresolved)
        out.append(r.from_symbol)
        out.append(r.to_symbol)
        if r.resolution is not None:
            out.append(r.resolution.unresolved_text)
    for ev in res.evidence:
        out.extend((ev.path, ev.notes, ev.extractor))
    for d in res.diagnostics:
        out.extend((d.code, d.message, d.path))
    return "\n".join(out)


def _artifacts_under(root: Path, *names: str) -> tuple[DiscoveredArtifact, ...]:
    """Artefatos com sha quando legível — sha ausente é válido no contrato."""
    out: list[DiscoveredArtifact] = []
    for p in names:
        try:
            sha = hashlib.sha256((root / p).read_bytes()).hexdigest()
        except OSError:
            sha = ""
        out.append(DiscoveredArtifact(path=p, sha256=sha))
    return tuple(out)


# --- segredos: terraform ---------------------------------------------------

# (nome de var sensível no bloco ``variable``) e (atribuição solta nome=valor)
_VAR_BLOCK = re.compile(r'variable\s+"([^"]+)"\s*\{(.*?)\}', re.S)
_ASSIGN = re.compile(r'(?m)^\s*([A-Za-z_]\w*)\s*=\s*"([^"]*)"')


def _fixture_secret_values() -> set[str]:
    """Valores secretos reais das fixtures terraform — grep dinâmico.

    Coleta ``default`` de variáveis cujo nome é sensível
    (``is_sensitive_name``) e atribuições ``name = "valor"`` sensíveis
    em ``.tf``/``.tfvars`` — a asserção não depende de lista hardcoded.
    """
    secrets: set[str] = set()
    for f in sorted(TF_FIXTURE.rglob("*")):
        if f.suffix not in (".tf", ".tfvars", ".json") or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for m in _VAR_BLOCK.finditer(text):
            if not is_sensitive_name(m.group(1)):
                continue
            for d in re.finditer(r'default\s*=\s*"([^"]*)"', m.group(2)):
                secrets.add(d.group(1))
        for m in _ASSIGN.finditer(text):
            if is_sensitive_name(m.group(1)):
                secrets.add(m.group(2))
    secrets.discard("")
    return secrets


def _terraform_result() -> DomainExtractionResult:
    adapter = TerraformAdapter(base_path=TF_FIXTURE)
    support = adapter.supported_artifacts()
    paths = tuple(
        p.relative_to(TF_FIXTURE).as_posix()
        for p in sorted(TF_FIXTURE.rglob("*"))
        if p.is_file() and support.matches(p.relative_to(TF_FIXTURE).as_posix())
    )
    return adapter.extract(
        DomainExtractionRequest(
            repository="git-remote:sec/infra",
            artifacts=_artifacts_under(TF_FIXTURE, *paths),
        )
    )


@pytest.fixture(scope="module")
def tf_result() -> DomainExtractionResult:
    return _terraform_result()


def test_fixture_yields_real_secrets() -> None:
    """Sanidade: o grep dinâmico encontra os segredos das fixtures."""
    secrets = _fixture_secret_values()
    assert "sup3r-s3cret-do-not-leak" in secrets
    assert "tok-live-adversarial" in secrets
    assert "tfvars-secret-must-not-leak" in secrets


def test_terraform_secret_values_never_in_result(
    tf_result: DomainExtractionResult,
) -> None:
    blob = _harvest_strings(tf_result)
    secrets = _fixture_secret_values()
    assert secrets  # guarda: fixture continha segredos
    for secret in secrets:
        assert secret not in blob, f"secret vazou: {secret!r}"
    assert tf_result.stats.get("secrets_redacted", 0) >= 3


def test_terraform_secret_values_never_persisted(tmp_path: Path) -> None:
    """Row-level: segredos ausentes das tabelas de domínio gravadas."""
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    reg = DomainRegistry()
    reg.register(TerraformAdapter(base_path=TF_FIXTURE))
    out = IndexCoordinator(conn, domain_registry=reg).index(TF_FIXTURE)
    assert out.status == "success", out.error
    secrets = _fixture_secret_values()
    assert secrets
    rows = conn.execute(
        "SELECT qualified_name, name, attributes FROM domain_entities"
    ).fetchall()
    blob = "\n".join(
        f"{r['qualified_name']}\n{r['name']}\n{r['attributes']}"
        for r in rows
    )
    blob += "\n" + "\n".join(
        str(v)
        for r in conn.execute(
            "SELECT type, to_unresolved, evidence_ids FROM domain_relations"
        ).fetchall()
        for v in r
    )
    blob += "\n" + "\n".join(
        str(r["notes"])
        for r in conn.execute("SELECT notes FROM evidence").fetchall()
    )
    conn.close()
    for secret in secrets:
        assert secret not in blob, f"secret persistido: {secret!r}"


def test_terraform_interpolations_never_materialize(
    tf_result: DomainExtractionResult,
) -> None:
    """``${...}`` vira ``${dynamic}`` — nunca o valor interpolado real."""
    for e in tf_result.entities:
        for k, v in e.attributes.items():
            assert "${" not in str(v) or "${dynamic}" in str(v), (k, v)
    for r in tf_result.relations:
        if "${" in r.to_unresolved:
            assert "${dynamic}" in r.to_unresolved


# --- segredos: cicd ---------------------------------------------------------


def _cicd_result() -> DomainExtractionResult:
    adapter = GitHubActionsAdapter.for_workspace(CICD_FIXTURE)
    wf = CICD_FIXTURE / ".github" / "workflows"
    paths = tuple(
        p.relative_to(CICD_FIXTURE).as_posix()
        for p in sorted(wf.glob("*.yml"))
    )
    return adapter.extract(
        DomainExtractionRequest(
            repository="git-remote:sec/cicd",
            artifacts=_artifacts_under(CICD_FIXTURE, *paths),
        )
    )


def test_github_actions_secrets_never_materialize() -> None:
    """Fixture: ``${{ expr }}`` nunca é armazenada — só marcadores."""
    blob = _harvest_strings(_cicd_result())
    # o caminho de marcação engatou sobre a fixture real
    assert "<expr>" in blob
    # expressão bruta e referências a secrets nunca são armazenadas
    assert "${{" not in blob
    assert "secrets.CI_TOKEN" not in blob
    assert "secrets.GITHUB_TOKEN" not in blob


def test_secret_expression_stored_only_as_marker() -> None:
    """``${{ secrets.X }}`` em campo armazenado → ``<secret>``, nunca valor."""
    files = {
        ".github/workflows/w.yml": (
            "name: W\non: push\njobs:\n"
            "  j:\n"
            "    runs-on: ${{ secrets.RUNNER_LABEL }}\n"
            "    env:\n"
            "      T: ${{ secrets.MY_TOKEN }}\n"
            "    steps:\n"
            "      - run: echo ${{ github.sha }}\n"
        )
    }
    adapter = GitHubActionsAdapter(
        lambda rel: files[rel].encode("utf-8") if rel in files else None
    )
    res = adapter.extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(path=".github/workflows/w.yml"),
            ),
        )
    )
    blob = _harvest_strings(res)
    assert "<secret>" in blob               # marcador armazenado
    assert "${{" not in blob                # expressão nunca crua
    assert "secrets.RUNNER_LABEL" not in blob
    assert "secrets.MY_TOKEN" not in blob
    assert res.stats["secrets_redacted"] >= 1


def test_cicd_workspace_reader_confined(tmp_path: Path) -> None:
    """Reader do adapter recusa traversal/absoluto — confinado ao root."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: x\non: push\n", encoding="utf-8")
    # "../escape.yml" resolve para fora do root — deve ser recusado
    (tmp_path.parent / "escape.yml").write_text(
        "name: evil\non: push\n", encoding="utf-8"
    )
    adapter = GitHubActionsAdapter.for_workspace(tmp_path)
    res = adapter.extract(
        DomainExtractionRequest(
            repository="r",
            artifacts=_artifacts_under(
                tmp_path,
                "../escape.yml",
                "/etc/passwd",
                ".github/workflows/ci.yml",
            ),
        )
    )
    blob = _harvest_strings(res)
    assert "evil" not in blob
    refused = {d.path for d in res.diagnostics if d.code == "missing-content"}
    assert "../escape.yml" in refused
    assert "/etc/passwd" in refused
    # o artefato legítimo dentro do root foi lido e extraído
    assert res.entities  # workflow "x" foi emitido


# --- sem rede ----------------------------------------------------------------


def test_no_network_imports_in_domain_packages() -> None:
    offenders: list[str] = []
    for py in _domain_sources():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{py.name}:{node.lineno}:{a.name}"
                    for a in node.names
                    if a.name.split(".")[0] in _NETWORK_BANNED
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.level == 0
                and node.module.split(".")[0] in _NETWORK_BANNED
            ):
                offenders.append(f"{py.name}:{node.lineno}:{node.module}")
    assert offenders == []


def test_no_public_bind_in_domain_packages() -> None:
    """Nenhum endereço público — loopback é fronteira só da ``api``.

    Chamadas a socket (``bind``/``listen``/``connect``) são impossíveis
    sem os módulos já barrados por ``test_no_network_imports_*`` —
    aqui basta garantir que nenhum literal de bind público (``0.0.0.0``,
    ``INADDR_ANY``/``INADDR_BROADCAST``) entra no código de domínio.
    """
    offenders: list[str] = []
    for py in _domain_sources():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "0.0.0.0":
                offenders.append(f"{py.name}:{node.lineno}:0.0.0.0")
            elif (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("INADDR")
            ):
                offenders.append(f"{py.name}:{node.lineno}:{node.attr}")
    assert offenders == []


def test_api_boundary_stays_loopback_only() -> None:
    """lf-0125 (c): a fronteira HTTP continua recusando bind público."""
    from code_atlas.api.server import ApiError, AtlasApiServer, serve

    sig = inspect.signature(serve)
    assert sig.parameters["host"].default == "127.0.0.1"
    for public in ("0.0.0.0", "8.8.8.8", "example.com", "::"):
        with pytest.raises(ApiError):
            AtlasApiServer("never.db", host=public)


# --- sem execução ------------------------------------------------------------


def test_no_code_execution_in_domain_packages() -> None:
    offenders: list[str] = []
    for py in _domain_sources():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{py.name}:{node.lineno}:import {a.name}"
                    for a in node.names
                    if a.name.split(".")[0] in _EXEC_BANNED_MODULES
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in _EXEC_BANNED_MODULES
            ):
                offenders.append(f"{py.name}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in _EXEC_BANNED_NAMES:
                    offenders.append(f"{py.name}:{node.lineno}:{f.id}()")
                elif (
                    isinstance(f, ast.Attribute)
                    and f.attr in _EXEC_BANNED_ATTRS
                ):
                    offenders.append(f"{py.name}:{node.lineno}:.{f.attr}()")
    assert offenders == []


# --- opcionalidade / deps / portabilidade ------------------------------------


def test_domain_packages_import_only_stdlib() -> None:
    """Adapters não adicionam dependências: imports são stdlib/code_atlas.

    ``sys.stdlib_module_names`` cobre 3.12+; imports relativos
    (``level > 0``) são internos ao pacote. Deps transitivas
    (tree-sitter via ``code_atlas.parsers``) pré-datam os adapters e
    são as únicas do pacote — ADR-0010/ADR-0015.
    """
    offenders: list[str] = []
    for py in _domain_sources():
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if (
                        top not in sys.stdlib_module_names
                        and top != "code_atlas"
                    ):
                        offenders.append(f"{py.name}:{node.lineno}:{a.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
            ):
                top = node.module.split(".")[0]
                if (
                    top not in sys.stdlib_module_names
                    and top != "code_atlas"
                ):
                    offenders.append(f"{py.name}:{node.lineno}:{node.module}")
    assert offenders == []


def _separator_offenders(py: Path) -> list[str]:
    """Ofensores de path-portabilidade num arquivo — AST, por nó."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(
                f"{py.name}:{node.lineno}:import {a.name}"
                for a in node.names
                if a.name in ("os", "os.path")
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            out.append(f"{py.name}:{node.lineno}:from os import")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in ("sep", "altsep", "pathsep")
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            out.append(f"{py.name}:{node.lineno}:os.{node.attr}")
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            # ``str(<path>) + "/"`` / ``+ "\\"`` — concat de
            # separador sobre str() de Path é o antipattern.
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "str"
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
            and node.right.value.startswith(("/", "\\"))
        ):
            out.append(
                f"{py.name}:{node.lineno}:str()+{node.right.value!r}"
            )
    return out


def test_no_os_path_or_separator_concat_in_domain_packages() -> None:
    """Paths via pathlib: sem ``os.path``/``os.sep`` e sem ``str(...) + '/'``.

    ``str(base) + "/"`` quebra containment no Windows (resolve devolve
    ``\\``) e ``+ '/'`` em string é frágil a irmãos de prefixo comum —
    o idiom correto é ``Path.is_relative_to`` / ``/``.
    """
    offenders: list[str] = []
    for py in _domain_sources():
        offenders.extend(_separator_offenders(py))
    assert offenders == []


def test_builtin_adapters_optional_but_present() -> None:
    """Os seis adapters built-in registram/habilitam — opt-in intacto."""
    reg = builtin_registry()
    assert reg.domains() == (
        "api", "aws-data", "cicd", "pyspark", "sql", "terraform"
    )
    # desabilitar um domínio não afeta os demais — opcionalidade real
    reg.disable("terraform")
    assert not reg.is_enabled("terraform")
    assert len(reg.enabled()) == 5
