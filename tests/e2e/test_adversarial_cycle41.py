"""lf-0056 — adversariais da superfície nova do Cycle 4.1 (Phase 10).

Cobre o que o ciclo adicionou: grounding de refs, atlas_evidence
escopado, enforcement do payload serializado e resiliência do
servidor MCP a exceções internas.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "golden"))

from retrieval_eval import build_eval_repo

from code_atlas.context.contracts import ContextRequest
from code_atlas.context.engine import ContextEngine
from code_atlas.mcp import AtlasMcpServer
from code_atlas.search.refs import EvidenceGrounder


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repo"
    conn = build_eval_repo(root)
    yield conn, str(root.resolve())
    conn.close()


def _call(srv: AtlasMcpServer, name: str, args: dict) -> dict:
    out = io.StringIO()
    srv.serve(io.StringIO(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": args}}) + "\n"), out)
    return json.loads(out.getvalue().strip())


def _server_on(conn: sqlite3.Connection, root: str) -> AtlasMcpServer:
    """Servidor com _ctx injetado — conn NOVA por call, ciclo de vida
    real (``finally: ctx.conn.close()`` fecha a conexão a cada call)."""
    from code_atlas.mcp import _Ctx
    from code_atlas.storage import connect
    db = Path(root).parent / "atlas.db"  # build_eval_repo grava aqui

    def fake_ctx(_pr) -> _Ctx:
        c = connect(db)
        c.row_factory = sqlite3.Row
        return _Ctx(conn=c, root=root, repo_id=1, snapshot_id=1)
    srv = AtlasMcpServer()
    srv._ctx = fake_ctx
    return srv


def test_tool_exception_never_kills_server(env) -> None:
    """lf-0054: exceção dentro de uma tool → erro tipado, servidor vivo."""
    conn, root = env
    srv = _server_on(conn, root)
    # força erro interno real (tabela ausente)
    conn.execute("DROP TABLE evidence")
    bad = _call(srv, "atlas_evidence", {"name": "x"})
    assert "error" in bad and bad["error"]["code"] == -32603
    # servidor segue respondendo chamadas válidas
    ok = _call(srv, "atlas_status", {})
    assert "result" in ok


def test_evidence_never_fabricated_for_missing_symbol(env) -> None:
    conn, root = env
    srv = _server_on(conn, root)
    res = _call(srv, "atlas_evidence", {"name": "ghost.Nope.missing"})
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["refs"] == []
    # evidência escopada ao símbolo → nada quando o símbolo não existe
    assert payload["evidence"] == []


def test_cross_snapshot_ref_rejected(env) -> None:
    conn, _ = env
    grounder = EvidenceGrounder(conn)
    occ = int(conn.execute(
        "SELECT MIN(id) FROM symbol_occurrences").fetchone()[0])
    # ref válida no snapshot 1
    assert grounder.occurrence_ref(1, 1, occ) is not None
    # mesma ocorrência declarada em snapshot/repo errados → rejeitada
    assert grounder.occurrence_ref(1, 999, occ) is None
    assert grounder.occurrence_ref(999, 1, occ) is None


def test_payload_budget_declares_overflow(env) -> None:
    conn, root = env
    pack = ContextEngine(conn).build(ContextRequest(
        task="charge", repository_root=root, budget_tokens=64))
    # envelope sozinho > 64 → declarado, nunca truncado silencioso
    assert pack.manifest.tokens_payload > 64
    assert "payload-over-budget" in pack.diagnostics.codes
    assert pack.diagnostics.mandatory_context_omitted


def test_evidence_refs_bounded(env) -> None:
    """Muitas ocorrências → refs limitadas (sem fanout não-ilimitado)."""
    conn, root = env
    srv = _server_on(conn, root)
    res = _call(srv, "atlas_evidence", {"name": "pkg.orders.validate_total"})
    payload = json.loads(res["result"]["content"][0]["text"])
    assert len(payload["refs"]) <= 25
    assert all(r["snapshot_id"] == payload["snapshot_id"]
               for r in payload["refs"])
