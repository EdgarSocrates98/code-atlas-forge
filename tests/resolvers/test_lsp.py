"""Testes dos adapters LSP (lf-0018): transporte, lifecycle, capability
gating, timeouts, crash e resolver — com fake server in-process."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fake_lsp"))
from server import FakeLspServer, start_fake

from code_atlas.discovery.scanner import scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.parsers import ParseEngine
from code_atlas.parsers.contracts import SourceSpan
from code_atlas.resolvers.contracts import (
    ResolutionRequest,
    SemanticResolver,
)
from code_atlas.resolvers.lsp import (
    LspClient,
    LspError,
    LspResolver,
    LspServerSpec,
    SpawnError,
)
from code_atlas.resolvers.lsp.spawn import spawn_server

ROOT_URI = "file:///repo"


def _spec(**kw) -> LspServerSpec:
    base = dict(
        name="fake", cmd=("python3", "-c", "pass"), root_uri=ROOT_URI,
        enabled=True, request_timeout_s=2.0,
        languages=frozenset({"python"}),
    )
    base.update(kw)
    return LspServerSpec(**base)


def _client(server: FakeLspServer, **kw) -> LspClient:
    transport, _ = start_fake(server)
    client = LspClient(_spec(**kw))
    client.attach(transport)
    return client


def _universe(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    s = scan(repo)
    eng = ParseEngine()
    results = eng.parse_batch(
        [(f.path, (repo / f.path).read_bytes()) for f in s.files])
    b = GraphBuilder()
    b.build("repo", s, results)
    return b.universe


# -- transport/lifecycle ----------------------------------------------------


def test_handshake_negotiates_capabilities() -> None:
    client = _client(FakeLspServer())
    assert client.available
    caps = client.capabilities
    assert caps.definition and caps.references
    assert caps.server_name == "fake-lsp"
    assert caps.server_version == "0.0.1"
    client.shutdown()


def test_capability_gating_blocks_method(tmp_path: Path) -> None:
    """Servidor sem referencesProvider → diagnóstico, sem request."""
    server = FakeLspServer(caps=frozenset({"definitionProvider"}))
    client = _client(server)
    assert client.capabilities.definition
    assert not client.capabilities.references

    u = _universe(tmp_path)
    resolver = LspResolver(client, u)
    req = ResolutionRequest(
        name="x", kind="reference", path="pkg/util.py",
        span=SourceSpan(start_byte=0, end_byte=0,
                        start_line=1, start_col=0, end_line=1, end_col=0))
    res = resolver.resolve(req)
    assert res.diagnostics[0].code == "capability-unsupported"
    client.shutdown()


def test_hanging_server_times_out_and_cancels() -> None:
    """Servidor que nunca responde → TimeoutError no handshake (0.3s),
    nunca bloqueio infinito."""
    transport, _ = start_fake(FakeLspServer(hang=True))
    client = LspClient(_spec(request_timeout_s=0.3))
    with pytest.raises(TimeoutError):
        client.attach(transport)


def test_crash_mid_request_marks_unavailable() -> None:
    server = FakeLspServer(crash_after=1)  # initialize ok; 2º request mata
    client = _client(server)
    assert client.available
    with pytest.raises((LspError, TimeoutError)):
        client.request("textDocument/documentSymbol", {})
    client.close()
    assert not client.available


def test_shutdown_sequence() -> None:
    client = _client(FakeLspServer())
    client.shutdown()
    assert not client.available


def test_spawn_missing_binary_typed_error() -> None:
    spec = _spec(cmd=("definitely-not-a-real-binary-xyz",))
    with pytest.raises(SpawnError, match="not found"):
        spawn_server(spec)


def test_disabled_spec_no_client() -> None:
    from code_atlas.resolvers.lsp import make_client

    assert make_client(_spec(enabled=False)) is None


# -- resolver ---------------------------------------------------------------


def _req(kind="reference", span_line=1):
    return ResolutionRequest(
        name="helper", kind=kind, path="pkg/engine.py", language="python",
        span=SourceSpan(start_byte=0, end_byte=0, start_line=span_line,
                        start_col=15, end_line=span_line, end_col=21))


def test_lsp_resolver_conformance(tmp_path: Path) -> None:
    client = _client(FakeLspServer())
    resolver = LspResolver(client, _universe(tmp_path))
    assert isinstance(resolver, SemanticResolver)
    caps = resolver.capabilities()
    assert caps.tier == 2  # LSP
    assert caps.needs_external_process
    client.shutdown()


def test_lsp_resolves_reference_to_module(tmp_path: Path) -> None:
    server = FakeLspServer(reference_locations=[
        {"uri": f"{ROOT_URI}/pkg/util.py",
         "range": {"start": {"line": 0, "character": 4},
                   "end": {"line": 0, "character": 10}}},
    ])
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    assert res.reference is not None
    assert res.reference.status == "resolved"
    assert res.reference.provenance.resolver_id == "lsp:fake"
    assert "fake-lsp" in res.reference.provenance.detail
    assert res.reference.target_id is not None
    client.shutdown()


def test_lsp_foreign_uri_external(tmp_path: Path) -> None:
    server = FakeLspServer(reference_locations=[
        {"uri": "file:///other-site/lib.py",
         "range": {"start": {"line": 0, "character": 0},
                   "end": {"line": 0, "character": 3}}},
    ])
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    assert res.reference is not None
    assert res.reference.status == "external"
    client.shutdown()


def test_lsp_empty_result_unresolved(tmp_path: Path) -> None:
    client = _client(FakeLspServer(reference_locations=[]))
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    assert res.reference is not None
    assert res.reference.status == "unresolved"
    client.shutdown()


def test_lsp_unavailable_server_diag(tmp_path: Path) -> None:
    client = _client(FakeLspServer())
    client.close()
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    assert res.diagnostics[0].code == "server-unavailable"


def test_lsp_definition_locations_defs(tmp_path: Path) -> None:
    server = FakeLspServer(definition_locations=[
        {"uri": f"{ROOT_URI}/pkg/util.py",
         "range": {"start": {"line": 0, "character": 4},
                   "end": {"line": 0, "character": 10}}},
    ])
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req(kind="definition"))
    assert res.definitions
    assert res.definitions[0].path == "pkg/util.py"
    assert res.reference is not None and res.reference.status == "resolved"
    client.shutdown()

# -- lf-0029: documentSymbol, publishDiagnostics, recovery -----------------


def test_document_symbol_request(tmp_path: Path) -> None:
    client = _client(FakeLspServer())
    out = client.document_symbol("file:///repo/pkg/util.py")
    assert out == []
    client.shutdown()


def test_document_symbol_capability_gated(tmp_path: Path) -> None:
    import pytest as _pt
    client = _client(FakeLspServer(caps=frozenset({"definitionProvider"})))
    with _pt.raises(LspError):
        client.document_symbol("file:///repo/x.py")
    client.shutdown()


def test_publish_diagnostics_captured(tmp_path: Path) -> None:
    uri = f"{ROOT_URI}/pkg/engine.py"  # path do _req()
    server = FakeLspServer(
        push_diagnostics_uri=uri,
        push_diagnostics=[{"severity": 2, "message": "warn x"}],
        reference_locations=[],
    )
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    codes = [d.code for d in res.diagnostics]
    assert "server-diagnostic" in codes
    client.shutdown()


def test_recovery_restart_after_crash(tmp_path: Path) -> None:
    """Crash mid-request → adapter tenta UM restart; se o restart
    consegue reanexar transporte vivo, o retry resolve."""
    server = FakeLspServer(
        crash_after=1,
        reference_locations=[{
            "uri": f"{ROOT_URI}/pkg/util.py",
            "range": {"start": {"line": 0, "character": 4},
                      "end": {"line": 0, "character": 10}},
        }],
    )
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))

    # restart real chamaria spawn — no teste reanexa um fake vivo.
    def fake_restart() -> None:
        client.close()
        transport, _ = start_fake(FakeLspServer(
            reference_locations=server.reference_locations))
        client.attach(transport)
    client.restart = fake_restart  # type: ignore[method-assign]

    res = resolver.resolve(_req())
    assert res.reference is not None
    assert res.reference.status == "resolved"
    client.shutdown()


def test_restart_failure_typed_diagnostic(tmp_path: Path) -> None:
    """Crash + restart impossível → server-error tipado, nunca crash."""
    server = FakeLspServer(crash_after=1, reference_locations=[
        {"uri": f"{ROOT_URI}/pkg/util.py",
         "range": {"start": {"line": 0, "character": 4},
                   "end": {"line": 0, "character": 10}}},
    ])
    client = _client(server)
    resolver = LspResolver(client, _universe(tmp_path))
    res = resolver.resolve(_req())
    assert res.diagnostics
    # crash pode se manifestar como timeout ou erro de transporte —
    # ambos tipados e com restart falho registrado
    assert res.diagnostics[0].code in ("server-error", "request-timeout")
    assert "restart failed" in res.diagnostics[0].message
