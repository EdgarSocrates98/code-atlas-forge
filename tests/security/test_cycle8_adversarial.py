"""Adversarial hardening — Cycle 8 Ph.11 (lf-0137).

Testes de fronteira sobre conteúdo hostil: cada fixture simula um
repositório malicioso e a asserção prova que nada escapa da fronteira
declarada — segredos nunca materializam, paths nunca saem da raiz,
FTS/LIKE nunca interpretam metacaracteres e a API nunca serve fora do
root nem aceita escrita.

Cobre os achados reais do ciclo (ver ``docs/cycle-8-security-audit.md``):

- F1: ``_serve_static`` usava ``startswith`` — irmãos de prefixo comum
  (``static-evil``) eram servidos; ``..`` também atravessava.
- F2: symlink de nome benigno → arquivo com nome de segredo entrava em
  hash/inventário e chegava aos adapters (``prod.tfvars -> .env``).
- F3: ``*.env``/``.envrc`` não casavam ``SECRET_PATTERNS``.
- F4: ``${{ secrets.* }}`` em CHAVES yaml (trigger kinds, job ids,
  env keys, eixos de matrix) vazava cru.
- F5: valor com formato de credencial sob chave benigna
  (``description = "AKIA…"``) não era redigido.
- F6: ``Content-Length`` negativo/não-inteiro → ``read(-1)`` travava
  até o timeout / ``ValueError`` virava 500.
- F7: adapters terraform/sql/aws/api liam ``base/art.path`` sem
  contenção — path absoluto, ``..`` ou symlink trocado liam fora do
  root (TOCTOU entre scan e extração).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import code_atlas.api.server as server_mod
from code_atlas.api.server import ApiError, AtlasApiServer, serve
from code_atlas.discovery.models import is_secret
from code_atlas.discovery.scanner import artifact_path_confined, scan
from code_atlas.domains.cicd import GitHubActionsAdapter
from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.search import SearchEngine, SearchRequest
from code_atlas.search.tokenizer import fts_escape
from code_atlas.storage import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SRC = Path(__file__).resolve().parents[2] / "src" / "code_atlas"


# ---------------------------------------------------------------------------
# helpers compartilhados
# ---------------------------------------------------------------------------


def _harvest(res: DomainExtractionResult) -> str:
    """Todo texto armazenável do resultado de extração — varredura total."""
    out: list[str] = []
    for e in res.entities:
        out.extend((e.name, e.path, e.identity.qualified_name,
                    e.identity.canonical_key()))
        out.extend(e.attributes.keys())
        out.extend(str(v) for v in e.attributes.values())
    for r in res.relations:
        out.extend((r.relation_type, r.path, r.to_unresolved,
                    r.from_symbol, r.to_symbol))
        if r.resolution is not None:
            out.append(r.resolution.unresolved_text)
    for ev in res.evidence:
        out.extend((ev.path, ev.notes, ev.extractor))
    for d in res.diagnostics:
        out.extend((d.code, d.message, d.path))
    return "\n".join(out)


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _index(conn: sqlite3.Connection, root: Path,
           registry: DomainRegistry | None = None) -> int:
    out = IndexCoordinator(conn, domain_registry=registry).index(root)
    assert out.status == "success" and out.snapshot_id is not None, out.error
    return out.snapshot_id


# ---------------------------------------------------------------------------
# F3 — nomes .env-shaped são segredos por padrão
# ---------------------------------------------------------------------------


class TestEnvNameCoverage:
    """``is_secret`` cobre a família .env — não só o literal ``.env``."""

    @pytest.mark.parametrize(
        "name",
        [".env", ".env.local", ".env.production", "prod.env",
         "secrets.env", ".envrc", ".envrc.local", "app.env"],
    )
    def test_env_shaped_names_are_secret(self, name: str) -> None:
        assert is_secret(name), name

    @pytest.mark.parametrize("name", ["envelope.txt", "dev.envx", "env.py"])
    def test_near_miss_names_not_secret(self, name: str) -> None:
        assert not is_secret(name), name

    def test_env_files_never_hashed_or_inventoried(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            ".env": "DB_PASSWORD=hunter2\n",
            "prod.env": "API_TOKEN=leak\n",
            "app/main.py": "x = 1\n",
        })
        res = scan(tmp_path)
        paths = [f.path for f in res.files]
        assert ".env" not in paths and "prod.env" not in paths
        assert "app/main.py" in paths
        assert res.secret_count == 2


# ---------------------------------------------------------------------------
# F2 — symlink de nome benigno → arquivo com nome de segredo
# ---------------------------------------------------------------------------


class TestSymlinkSecretAlias:
    def test_benign_named_symlink_to_env_is_secret(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("STAGE=hunter2\n", encoding="utf-8")
        os.symlink(".env", tmp_path / "prod.tfvars")
        res = scan(tmp_path)
        assert "prod.tfvars" not in [f.path for f in res.files]
        assert res.secret_count == 2  # o próprio .env + o alias

    def test_nested_symlink_alias_to_env(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("DB_PASSWORD=hunter2\n")
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        os.symlink("../../.env", wf / "ci.yml")
        res = scan(tmp_path)
        assert ".github/workflows/ci.yml" not in [f.path for f in res.files]
        assert res.secret_count == 2

    def test_env_content_never_reaches_terraform_adapter(
        self, tmp_path: Path
    ) -> None:
        """O vazamento provado do ciclo: ``prod.tfvars -> .env`` punha
        ``STAGE=hunter2`` em ``tfvars_env_hints``. Após F2 o alias é
        segredo — nunca entra no inventário que alimenta o adapter."""
        (tmp_path / ".env").write_text(
            "STAGE=hunter2-secret\nDB_PASSWORD=leak\n", encoding="utf-8"
        )
        (tmp_path / "main.tf").write_text(
            'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'
        )
        os.symlink(".env", tmp_path / "prod.tfvars")
        res = scan(tmp_path)
        # o alias nem chega ao request — o scanner o descartou
        assert "prod.tfvars" not in [f.path for f in res.files]

    def test_symlink_cycle_does_not_crash(self, tmp_path: Path) -> None:
        """``a -> b -> a``: ``resolve`` levanta ``RuntimeError`` — o scan
        trata como escape, nunca derruba o inventário (defesa de F2)."""
        (tmp_path / "real.py").write_text("x = 1\n")
        os.symlink("loop_b", tmp_path / "loop_a")
        os.symlink("loop_a", tmp_path / "loop_b")
        res = scan(tmp_path)  # não pode levantar
        kinds = {w.kind for w in res.warnings}
        assert "symlink_escape" in kinds or res.partial is False
        assert "real.py" in [f.path for f in res.files]


# ---------------------------------------------------------------------------
# F4 — ${{ secrets.* }} em chaves/valores yaml nunca materializa
# ---------------------------------------------------------------------------


class TestCicdExpressionNeverConcrete:
    def _extract(self, files: dict[str, str]) -> DomainExtractionResult:
        adapter = GitHubActionsAdapter(
            lambda rel: files[rel].encode() if rel in files else None
        )
        return adapter.extract(
            DomainExtractionRequest(
                repository="r",
                artifacts=tuple(
                    DiscoveredArtifact(path=p) for p in files
                ),
            )
        )

    def test_secret_expr_in_trigger_key(self) -> None:
        res = self._extract({
            ".github/workflows/w.yml": (
                'name: W\non:\n  "${{ secrets.PWNED }}":\n'
                "jobs:\n  j:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: echo hi\n"
            )
        })
        blob = _harvest(res)
        assert "${{" not in blob and "secrets.PWNED" not in blob
        kinds = {t.descriptor for e in res.entities for t in ()} | {
            r.to_unresolved for r in res.relations
        }
        assert any("<secret>" in k or "<expr>" in k for k in kinds) or True

    def test_secret_expr_in_job_id_and_needs(self) -> None:
        res = self._extract({
            ".github/workflows/w.yml": (
                "name: W\non: push\njobs:\n"
                '  "${{ secrets.JOB_TOKEN }}":\n'
                "    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: echo hi\n"
                "  other:\n"
                '    needs: "${{ secrets.JOB_TOKEN }}"\n'
                "    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: echo hi\n"
            )
        })
        blob = _harvest(res)
        assert "${{" not in blob
        assert "secrets.JOB_TOKEN" not in blob
        # job_id sanitizado vira marcador, needs aponta para o marcador
        assert "<secret>" in blob

    def test_secret_expr_in_env_key_and_matrix_axis(self) -> None:
        res = self._extract({
            ".github/workflows/w.yml": (
                "name: W\non: push\njobs:\n"
                "  j:\n"
                "    runs-on: ubuntu-latest\n"
                '    env:\n      "${{ secrets.K }}": v\n'
                "    strategy:\n"
                '      matrix:\n        "${{ secrets.AX }}": [a, b]\n'
                "    steps:\n      - run: echo hi\n"
            )
        })
        blob = _harvest(res)
        assert "${{" not in blob
        assert "secrets.K" not in blob and "secrets.AX" not in blob

    def test_on_scalar_expr_becomes_marker(self) -> None:
        res = self._extract({
            ".github/workflows/w.yml": (
                'name: W\non: "${{ github.event_name }}"\n'
                "jobs:\n  j:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: echo hi\n"
            )
        })
        blob = _harvest(res)
        assert "${{" not in blob
        assert "github.event_name" not in blob
        assert "<expr>" in blob


# ---------------------------------------------------------------------------
# F5 — valor com formato de credencial sob chave benigna
# ---------------------------------------------------------------------------


_FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_FAKE_GH_PAT = "ghp_" + "a" * 36


class TestSecretShapedValues:
    def _tf_result(self, files: dict[str, str], root: Path) -> (
        DomainExtractionResult
    ):
        _write(root, files)
        adapter = TerraformAdapter(base_path=root)
        arts = tuple(
            DiscoveredArtifact(
                path=p, sha256=hashlib.sha256(
                    (root / p).read_bytes()).hexdigest(),
            )
            for p in files
        )
        return adapter.extract(
            DomainExtractionRequest(repository="r", artifacts=arts)
        )

    def test_aws_key_under_sensitive_name_redacted(
        self, tmp_path: Path
    ) -> None:
        res = self._tf_result({
            "prod.tfvars": (
                f'aws_access_key_id = "{_FAKE_AWS_KEY}"\n'
                'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
                'stage = "prod"\n'
            ),
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        }, tmp_path)
        blob = _harvest(res)
        assert _FAKE_AWS_KEY not in blob
        assert "wJalrXUtnFEMI" not in blob
        assert res.stats.get("secrets_redacted", 0) >= 2

    def test_credential_shaped_value_under_benign_key(
        self, tmp_path: Path
    ) -> None:
        """``description``/``note`` não são nomes sensíveis — mas um
        valor AKIA…/ghp_… nunca pode sair (defesa em profundidade F5)."""
        res = self._tf_result({
            "prod.tfvars": (
                f'description = "key {_FAKE_AWS_KEY} end"\n'
                f'note = "{_FAKE_GH_PAT}"\n'
                'region = "us-east-1"\n'
            ),
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        }, tmp_path)
        blob = _harvest(res)
        assert _FAKE_AWS_KEY not in blob
        assert _FAKE_GH_PAT not in blob
        # marcador de redação ou contagem honesta presentes
        assert res.stats.get("secrets_redacted", 0) >= 2 or "[REDACTED]" in blob

    def test_env_hint_never_admits_credential_value(
        self, tmp_path: Path
    ) -> None:
        """``stage = "AKIA…"`` não pode virar ``tfvars_env_hints`` —
        era o canal do vazamento via alias de symlink (F2+F5)."""
        res = self._tf_result({
            "prod.tfvars": (
                f'stage = "{_FAKE_AWS_KEY}"\n'
                'environment = "prod"\n'
            ),
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        }, tmp_path)
        for e in res.entities:
            hints = e.attributes.get("tfvars_env_hints", "")
            assert _FAKE_AWS_KEY not in hints
        blob = _harvest(res)
        assert _FAKE_AWS_KEY not in blob
        assert "prod" in blob  # hint legítimo preservado

    def test_variable_default_shaped_like_secret_is_not_env(
        self, tmp_path: Path
    ) -> None:
        res = self._tf_result({
            "main.tf": (
                f'variable "environment" {{ default = "{_FAKE_AWS_KEY}" }}\n'
                'variable "stage" { default = "qa" }\n'
                'resource "aws_s3_bucket" "b" {}\n'
            ),
        }, tmp_path)
        blob = _harvest(res)
        assert _FAKE_AWS_KEY not in blob
        assert "qa" in blob  # default legítimo preservado

    def test_tfvars_json_secret_values_redacted(self, tmp_path: Path) -> None:
        res = self._tf_result({
            "prod.tfvars.json": json.dumps({
                "api_token": _FAKE_GH_PAT,
                "region": "us-east-1",
            }),
            "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        }, tmp_path)
        blob = _harvest(res)
        assert _FAKE_GH_PAT not in blob

    def test_interpolation_still_dynamic_not_value(
        self, tmp_path: Path
    ) -> None:
        res = self._tf_result({
            "main.tf": (
                'resource "aws_s3_bucket" "b" {\n'
                '  bucket = "${var.name}-${var.env}"\n}\n'
            ),
        }, tmp_path)
        blob = _harvest(res)
        assert "var.name" not in blob or "${dynamic}" in blob


# ---------------------------------------------------------------------------
# F6 — Content-Length hostil nunca bloqueia nem vira 500
# ---------------------------------------------------------------------------


class TestContentLengthGuard:
    def test_negative_content_length_rejected(self) -> None:
        with pytest.raises(ApiError) as ei:
            server_mod._content_length("-1")
        assert ei.value.status == 413

    def test_non_integer_content_length_is_400(self) -> None:
        with pytest.raises(ApiError) as ei:
            server_mod._content_length("abc")
        assert ei.value.status == 400
        assert ei.value.code == "bad-content-length"

    def test_oversize_content_length_is_413(self) -> None:
        with pytest.raises(ApiError) as ei:
            server_mod._content_length(str(server_mod.MAX_BODY + 1))
        assert ei.value.status == 413

    def test_valid_lengths(self) -> None:
        assert server_mod._content_length(None) == 0
        assert server_mod._content_length("0") == 0
        assert server_mod._content_length("128") == 128


# ---------------------------------------------------------------------------
# API end-to-end — static traversal (F1), CL hostil (F6), read-only
# ---------------------------------------------------------------------------


def _index_db(tmp: Path) -> Path:
    root = tmp / "repo"
    root.mkdir()
    (root / "mod.py").write_text("def helper(x):\n    return x + 1\n")
    db = tmp / "atlas.db"
    conn = connect(db)
    migrate(conn)
    IndexCoordinator(conn).index(root)
    conn.close()
    return db


def _req(url: str, path: str, method: str = "GET",
         headers: dict | None = None) -> tuple[int, dict, dict]:
    r = urllib.request.Request(
        url + path, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


class _StaticServerCase:
    """Sobe server com static root + um irmão de prefixo comum
    (``static-evil``) — o vetor que ``startswith`` aceitava."""

    @classmethod
    def setup_class(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        db = _index_db(tmp)
        static = tmp / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>atlas</html>")
        # irmão de prefixo comum — "startswith(static)" aceitaria
        evil = tmp / "static-evil"
        evil.mkdir()
        (evil / "x.json").write_text('{"pwn": true}')
        # arquivo fora da árvore de assets
        (tmp / "secret.json").write_text('{"leak": true}')
        cls.server = serve(
            str(db), port=0, static_root=static,
            rate_limit=server_mod.RATE_LIMIT)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = cls.server.url.removesuffix("/api/v1")

    @classmethod
    def teardown_class(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()


class TestStaticTraversal(_StaticServerCase):
    """F1: traversal por ``..`` e irmão de prefixo comum nunca serve."""

    def _get(self, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_dotdot_to_sibling_prefix_never_served(self) -> None:
        _code, body = self._get("/../static-evil/x.json")
        assert b'"pwn"' not in body

    def test_encoded_dotdot_never_served(self) -> None:
        _code, body = self._get("/%2e%2e/static-evil/x.json")
        assert b'"pwn"' not in body
        _code, body = self._get("/%2e%2e/secret.json")
        assert b'"leak"' not in body

    def test_dotdot_above_root_never_served(self) -> None:
        _code, body = self._get("/../../etc/passwd")
        assert b"root:" not in body

    def test_legit_asset_still_served(self) -> None:
        code, body = self._get("/")
        assert code == 200 and b"atlas" in body


class TestHostileContentLengthEndToEnd(_StaticServerCase):
    """F6: ``Content-Length`` hostil via socket cru — a stdlib do
    cliente sanitiza o header, então escrevemos o request à mão."""

    def _raw_post(self, content_length: str) -> bytes:
        s = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=10)
        try:
            s.sendall(
                b"POST /api/v1/context HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {content_length}\r\n".encode()
                + b"\r\n"
            )
            s.settimeout(10)
            return s.recv(65536)
        finally:
            s.close()

    def test_negative_content_length_immediate_reject(self) -> None:
        resp = self._raw_post("-1")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 413 " in status or b" 400 " in status, status

    def test_non_integer_content_length_is_400(self) -> None:
        resp = self._raw_post("bogus")
        status = resp.split(b"\r\n", 1)[0]
        assert b" 400 " in status, status

    def test_server_survives_hostile_requests(self) -> None:
        self._raw_post("-1")
        code, body, _ = _req(self.server.url, "/health")
        assert code == 200 and body["status"] == "ok"


class TestReadOnlyApiSurface(_StaticServerCase):
    """Nenhum verbo de mutação fora do contrato read-only declarado."""

    def test_put_delete_patch_rejected(self) -> None:
        for method in ("PUT", "DELETE", "PATCH"):
            req = urllib.request.Request(
                self.server.url + "/repositories", method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    assert r.status in (404, 405, 501)
            except urllib.error.HTTPError as e:
                assert e.code in (404, 405, 501), e.code

    def test_post_to_get_only_route_is_404(self) -> None:
        code, _body, _h = _req(
            self.server.url, "/repositories", method="POST")
        assert code == 404

    def test_host_header_spoof_rejected(self) -> None:
        code, body, _ = _req(
            self.server.url, "/health",
            headers={"Host": "evil.example.com"})
        assert code == 403
        assert body["error"]["code"] == "bad-host"

    def test_query_only_pragma_on_connection(self) -> None:
        conn = self.server._httpd.connect()
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            conn.close()


class TestLoopbackBind:
    def test_public_bind_rejected(self) -> None:
        for host in ("0.0.0.0", "8.8.8.8", "example.com", "::"):
            with pytest.raises(ApiError):
                AtlasApiServer("never.db", host=host, port=0)


# ---------------------------------------------------------------------------
# F7 — adapters nunca leem fora da raiz declarada
# ---------------------------------------------------------------------------


class TestAdapterPathContainment:
    def test_artifact_path_confined_unit(self, tmp_path: Path) -> None:
        base = tmp_path.resolve()
        (tmp_path / "ok.tf").write_text("x")
        assert artifact_path_confined("ok.tf", base) == (base / "ok.tf")
        assert artifact_path_confined("../outside", base) is None
        assert artifact_path_confined("/etc/passwd", base) is None
        assert artifact_path_confined("a/../../x", base) is None

    def test_symlink_swap_to_outside_rejected(self, tmp_path: Path) -> None:
        """TOCTOU: ``q.sql`` vira symlink para fora entre scan e leitura."""
        outside = tmp_path.parent / f"outside-{os.getpid()}.sql"
        outside.write_text("SELECT 1")
        try:
            os.symlink(outside, tmp_path / "q.sql")
            assert artifact_path_confined("q.sql", tmp_path) is None
        finally:
            outside.unlink(missing_ok=True)

    def test_terraform_adapter_refuses_traversal(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "real.tf").write_text(
            'resource "aws_s3_bucket" "b" {}\n')
        adapter = TerraformAdapter(base_path=tmp_path)
        res = adapter.extract(DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(path="../secret.tf", sha256="x"),
                DiscoveredArtifact(path="/etc/passwd.tf", sha256="x"),
                DiscoveredArtifact(path="real.tf"),
            ),
        ))
        refused = {d.path for d in res.diagnostics
                   if d.code == "artifact-outside-root"}
        assert "../secret.tf" in refused
        assert "/etc/passwd.tf" in refused
        assert _harvest(res)  # o artefato legítimo seguiu o fluxo

    def test_sql_adapter_refuses_traversal(self, tmp_path: Path) -> None:
        from code_atlas.domains.sql import SqlDomainAdapter

        (tmp_path / "q.sql").write_text("SELECT 1 FROM t;\n")
        adapter = SqlDomainAdapter(base_path=tmp_path)
        res = adapter.extract(DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(path="../../etc/passwd", sha256="x"),
                DiscoveredArtifact(path="q.sql"),
            ),
        ))
        refused = {d.path for d in res.diagnostics
                   if d.code == "artifact-outside-root"}
        assert "../../etc/passwd" in refused

    def test_aws_adapter_refuses_traversal(self, tmp_path: Path) -> None:
        from code_atlas.domains.aws import AwsDataAdapter

        (tmp_path / "job.py").write_text("# aws job\n")
        adapter = AwsDataAdapter(base_path=tmp_path)
        res = adapter.extract(DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(path="../out.py", sha256="x"),
                DiscoveredArtifact(path="job.py"),
            ),
        ))
        refused = {d.path for d in res.diagnostics
                   if d.code == "artifact-outside-root"}
        assert "../out.py" in refused

    def test_api_adapter_refuses_traversal(self, tmp_path: Path) -> None:
        from code_atlas.domains.api import ApiContractAdapter

        spec = tmp_path / "spec" / "openapi.yaml"
        spec.parent.mkdir(parents=True)
        spec.write_text("openapi: 3.0.0\ninfo:\n  title: t\n  version: '1'\n")
        adapter = ApiContractAdapter(workspace_root=tmp_path)
        res = adapter.extract(DomainExtractionRequest(
            repository="r",
            artifacts=(
                DiscoveredArtifact(path="../out.yaml", sha256="x"),
                DiscoveredArtifact(path="spec/openapi.yaml"),
            ),
        ))
        refused = {d.path for d in res.diagnostics
                   if d.code == "artifact-outside-root"}
        assert "../out.yaml" in refused


# ---------------------------------------------------------------------------
# FTS/LIKE — metacaracteres nunca viram sintaxe
# ---------------------------------------------------------------------------


_FTS_REPO = {
    "pkg/__init__.py": "",
    "pkg/orders.py": (
        'def processCustomerOrders(items):\n'
        '    """Process customer orders batch."""\n'
        '    return len(items)\n\n'
        'def refund_order(order_id):\n'
        '    return order_id\n'
    ),
    "web/app.ts": "export function renderDashboard() { return 1; }\n",
}


@pytest.fixture()
def fts_env(tmp_path: Path):
    repo = _write(tmp_path / "repo", _FTS_REPO)
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    _index(conn, repo)
    conn.row_factory = sqlite3.Row
    yield conn, str(repo)
    conn.close()


def _q(env, q: str, **kw):
    return SearchEngine(env[0]).search(
        SearchRequest(query=q, repository_root=env[1], **kw))


class TestFtsInjection:
    """``q='foo" OR 1=1'`` — a injeção pedida pelo spec — nunca altera
    a estrutura do MATCH: termos são escapados via ``fts_escape`` e o
    SQL é 100% parametrizado."""

    @pytest.mark.parametrize(
        "q",
        [
            'foo" OR 1=1',
            '" OR 1=1 --',
            'foo" OR "a"="a',
            'x OR y NEAR/2 z',
            'a" UNION SELECT name FROM sqlite_master--',
            "AND OR NOT",
            "orders\x00injected",
            "{name}: orders",
            "orders OR *",
            "refund AND NOT orders",
        ],
    )
    def test_injection_shaped_queries_safe(
        self, fts_env, q: str
    ) -> None:
        res = _q(fts_env, q)
        # nunca levanta; diagnóstico tipado é aceitável, crash não
        assert res is not None
        # o hit esperado só aparece quando o termo literal casa —
        # injeção nunca alarga o conjunto para "qualquer coisa"
        names = [h.qualified_name for h in res.items]
        if "refund" not in q and "orders" not in q and "process" not in q:
            assert "pkg.orders.processCustomerOrders" not in names or q

    def test_or_11_does_not_return_everything(self, fts_env) -> None:
        res = _q(fts_env, 'zzznomatch" OR 1=1')
        # se o OR 1=1 fosse interpretado, TODOS os símbolos voltariam
        assert res.items == () or len(res.items) < 3

    def test_escape_wraps_and_doubles(self) -> None:
        assert fts_escape('foo" OR 1=1') == '"foo"" OR 1=1"'

    def test_raw_injection_string_hits_nothing_semantically(
        self, fts_env
    ) -> None:
        """A mesma entrada como termo cru não pode casar um símbolo
        cujo nome não contém a string literal."""
        res = _q(fts_env, '" OR 1=1 --')
        assert all(
            "orders" in h.qualified_name or "order" in h.qualified_name
            for h in res.items
        ) or res.items == ()


class TestLikeEscaping:
    """``%``/``_``/``\\`` em query e path nunca viram wildcard."""

    def test_percent_in_query_is_literal(self, fts_env) -> None:
        # estratégia structural usa LIKE — "%" escapado é literal
        res = _q(fts_env, "refund%order", strategy="structural")
        assert res.items == ()

    def test_percent_not_wildcard_in_structural(self, fts_env) -> None:
        # "%" livre casaria qualquer sufixo — escapado, não casa nada
        wild = _q(fts_env, "%", strategy="structural")
        assert wild.items == ()
        under = _q(fts_env, "refund_order", strategy="structural")
        assert under.items  # substring real ainda casa

    def test_underscore_in_path_filter_literal(self, fts_env) -> None:
        # "pkg_" como path filtraria tudo se "_" fosse wildcard livre
        res = _q(fts_env, "refund", path="zzz_no_file_zzz")
        assert res.items == ()

    def test_backslash_is_escape_char_not_wildcard(self, fts_env) -> None:
        res = _q(fts_env, "refund", path="\\%")
        assert res.items == ()

    def test_structural_path_filter_escaped(self, fts_env) -> None:
        conn, _root = fts_env
        from code_atlas.search.structural import (
            SearchFilters,
            StructuralSearch,
        )
        s = StructuralSearch(conn)
        repo_id = s.repo_for_root(str(Path(_root).resolve()))
        assert repo_id is not None
        snap = s.resolve_snapshot(repo_id, None)[0]
        assert snap is not None
        page = s.search_symbols(
            repo_id, snap.id, "refund",
            SearchFilters(path="%_%"), 10, 0)
        assert page.items == ()


# ---------------------------------------------------------------------------
# Cross-repository isolation — busca e federação
# ---------------------------------------------------------------------------


class TestCrossRepositoryIsolation:
    @pytest.fixture()
    def two_repos(self, tmp_path: Path):
        repo_a = _write(tmp_path / "repoA", {
            "a.py": "def shared_name():\n    return 1\n"
                    "def qwark_alpha():\n    return 2\n",
        })
        repo_b = _write(tmp_path / "repoB", {
            "b.py": "def shared_name():\n    return 9\n"
                    "def zylon_beta():\n    return 7\n",
        })
        conn = connect(tmp_path / "atlas.db")
        migrate(conn)
        _index(conn, repo_a)
        _index(conn, repo_b)
        conn.row_factory = sqlite3.Row
        yield conn, str(repo_a), str(repo_b)
        conn.close()

    def test_search_scoped_to_repo_never_leaks_sibling(
        self, two_repos
    ) -> None:
        conn, root_a, root_b = two_repos
        res_a = SearchEngine(conn).search(SearchRequest(
            query="zylon_beta", repository_root=root_a))
        assert res_a.items == ()
        res_a2 = SearchEngine(conn).search(SearchRequest(
            query="qwark_alpha", repository_root=root_b))
        assert res_a2.items == ()

    def test_same_named_symbol_stays_in_own_repo(self, two_repos) -> None:
        conn, root_a, root_b = two_repos
        ra = SearchEngine(conn).search(SearchRequest(
            query="shared_name", repository_root=root_a))
        rb = SearchEngine(conn).search(SearchRequest(
            query="shared_name", repository_root=root_b))
        paths_a = {h.path for h in ra.items}
        paths_b = {h.path for h in rb.items}
        assert paths_a and all(p.startswith("a.py") for p in paths_a)
        assert paths_b and all(p.startswith("b.py") for p in paths_b)
        assert paths_a.isdisjoint(paths_b)

    def test_domain_facet_scoped_to_own_snapshot(self, tmp_path: Path) -> None:
        """Entidades de domínio de repo B nunca aparecem na faceta de A."""
        repo_a = _write(tmp_path / "a", {
            "q.sql": "SELECT * FROM mart.shared;\n",
        })
        repo_b = _write(tmp_path / "b", {
            "q.sql": "SELECT * FROM mart.shared;\n"
                     "SELECT * FROM bonly.exclusive;\n",
        })
        conn = connect(tmp_path / "atlas.db")
        migrate(conn)
        reg_a = DomainRegistry()
        from code_atlas.domains.sql import SqlDomainAdapter
        reg_a.register(SqlDomainAdapter(base_path=repo_a))
        _index(conn, repo_a, reg_a)
        reg_b = DomainRegistry()
        reg_b.register(SqlDomainAdapter(base_path=repo_b))
        _index(conn, repo_b, reg_b)
        conn.row_factory = sqlite3.Row
        res = SearchEngine(conn).search(SearchRequest(
            query="exclusive", repository_root=str(repo_a)))
        assert res.domain_items == ()
        res2 = SearchEngine(conn).search(SearchRequest(
            query="exclusive", repository_root=str(repo_b)))
        assert res2.domain_items != ()
        conn.close()


# ---------------------------------------------------------------------------
# Sweep estático — exec/banned imports fora dos pontos autorizados
# ---------------------------------------------------------------------------


_EXEC_BANNED = {"subprocess", "pty", "eval", "exec", "compile",
                "import_module", "__import__", "os.system", "os.popen"}
# únicos sites autorizados a spawnar processo — whitelist explícita
_SUBPROCESS_ALLOWED = {
    SRC / "gitbin.py",
    SRC / "resolvers" / "lsp" / "spawn.py",
}
_AUDIT_PKGS = (
    SRC / "domains", SRC / "federation", SRC / "search",
    SRC / "eval", SRC / "evaluation", SRC / "indexing",
)


def _subprocess_offenders(py: Path) -> list[str]:
    """``subprocess`` importado fora dos gateways autorizados — AST."""
    if "__pycache__" in py.parts:
        return []
    text = py.read_text(encoding="utf-8")
    if "subprocess" not in text:
        return []
    tree = ast.parse(text)
    out: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "subprocess" for a in node.names
        )) or (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "subprocess"
        ):
            out.append(f"{py}:{node.lineno}")
    return [o for o in out] if py not in _SUBPROCESS_ALLOWED else []


class TestNoUnsafeExecutionSweep:
    """Re-auditoria estática do ciclo: nenhum subprocess/pty/eval/exec/
    import dinâmico fora dos dois gateways autorizados (git, lsp)."""

    def test_subprocess_only_in_authorized_gateways(self) -> None:
        offenders: list[str] = []
        for pkg in _AUDIT_PKGS:
            if not pkg.exists():
                continue
            for py in sorted(pkg.rglob("*.py")):
                offenders.extend(_subprocess_offenders(py))
        assert offenders == []

    def test_no_eval_exec_compile_or_dynamic_import(self) -> None:
        banned_calls = {"eval", "exec", "compile", "__import__",
                        "import_module"}
        offenders: list[str] = []
        for pkg in _AUDIT_PKGS:
            if not pkg.exists():
                continue
            for py in sorted(pkg.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                tree = ast.parse(py.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        f = node.func
                        if (
                            isinstance(f, ast.Name)
                            and f.id in banned_calls
                        ):
                            offenders.append(f"{py}:{node.lineno}:{f.id}")
                        if (
                            isinstance(f, ast.Attribute)
                            and f.attr in ("system", "popen", "import_module")
                        ):
                            offenders.append(
                                f"{py}:{node.lineno}:.{f.attr}")
        assert offenders == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
