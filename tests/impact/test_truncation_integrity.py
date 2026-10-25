"""lf-0151 (Cycle 8.1 Phase 8) — integridade de truncamento do blast.

O gap medido no scorecard do Cycle 8: ``max_nodes`` (scan de
vizinhos por expansão) e ``max_depth`` (fronteira do BFS) cortavam
trabalho real **sem** levantar ``truncated``. Estes testes fixam o
contrato novo: todo bound que descarta trabalho declara
``truncated`` + ``truncation_reasons`` — e cortes de fronteira sem
trabalho restante continuam honestamente ``False`` (sonda +1/fim
de fronteira — nada de falso positivo por convenção).

Fixture mínima montada à mão (``migrate`` + inserts): grafo de
``relations``/``domain_relations`` excedendo cada cap — nenhum repo
indexado é executado.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from code_atlas.impact import blast_radius, domain_traversal
from code_atlas.impact.contracts import (
    ImpactLimits,
    ImpactRequest,
)
from code_atlas.impact.engine import ImpactEngine
from code_atlas.impact.report import to_json, to_markdown
from code_atlas.storage.migrations import migrate

_SNAP = 1


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.execute(
        "INSERT INTO repositories (root_handle) VALUES ('/r')")
    conn.execute(
        "INSERT INTO snapshots (repository_id, worktree_digest,"
        " status) VALUES (1, 'd', 'READY')")
    conn.execute(
        "INSERT INTO source_files (repository_id, relative_path,"
        " language) VALUES (1, 'a.py', 'python')")
    return conn


def _sym(conn: sqlite3.Connection, qname: str) -> int:
    """Símbolo + ocorrência no snapshot 1; retorna o occurrence id."""
    conn.execute(
        "INSERT INTO symbols (repository_id, language, qualified_name,"
        " kind) VALUES (1, 'python', ?, 'function')", (qname,))
    sid = int(conn.execute(
        "SELECT max(id) AS i FROM symbols").fetchone()["i"])
    conn.execute(
        "INSERT INTO symbol_occurrences"
        " (snapshot_id, logical_symbol_id, source_file_id)"
        " VALUES (?, ?, 1)", (_SNAP, sid))
    return int(conn.execute(
        "SELECT max(id) AS i FROM symbol_occurrences").fetchone()["i"])


def _rel(conn: sqlite3.Connection, consumer: int, target: int,
         status: str = "resolved") -> None:
    conn.execute(
        "INSERT INTO relations"
        " (snapshot_id, from_id, to_id, type, resolution_status,"
        "  confidence_class, resolver)"
        " VALUES (?, ?, ?, 'CALLS', ?, 'resolved', 'test')",
        (_SNAP, consumer, target, status))


def _ent(conn: sqlite3.Connection, qname: str) -> int:
    """Entidade + ocorrência de domínio; retorna o occurrence id."""
    conn.execute(
        "INSERT INTO domain_entities"
        " (repository_id, domain, kind, identity_key, qualified_name)"
        " VALUES (1, 'sql', 'data_table', ?, ?)",
        (f"dom:sql/test//ns//{qname}", qname))
    eid = int(conn.execute(
        "SELECT max(id) AS i FROM domain_entities").fetchone()["i"])
    conn.execute(
        "INSERT INTO domain_entity_occurrences (snapshot_id, entity_id)"
        " VALUES (?, ?)", (_SNAP, eid))
    return int(conn.execute(
        "SELECT max(id) AS i FROM domain_entity_occurrences"
    ).fetchone()["i"])


def _dom_rel(conn: sqlite3.Connection, frm: int, to: int,
             rtype: str = "DERIVES_FROM",
             status: str = "resolved") -> None:
    conn.execute(
        "INSERT INTO domain_relations"
        " (snapshot_id, from_entity_occ_id, to_entity_occ_id, type,"
        "  resolution_status, confidence_class, resolver)"
        " VALUES (?, ?, ?, ?, ?, 'syntactic', 'test')",
        (_SNAP, frm, to, rtype, status))


def _chain(conn: sqlite3.Connection, length: int) -> int:
    """Cadeia seed ← c0 ← c1 ← … — retorna a ocorrência-semente."""
    prev = _sym(conn, "m.seed")
    for i in range(length):
        nxt = _sym(conn, f"m.chain{i}")
        _rel(conn, nxt, prev)
        prev = nxt
    # retorna o occurrence da semente (primeiro da cadeia)
    return int(conn.execute(
        "SELECT o.id FROM symbol_occurrences o"
        " JOIN symbols s ON s.id = o.logical_symbol_id"
        " WHERE s.qualified_name = 'm.seed'").fetchone()["id"])


def _dom_chain(conn: sqlite3.Connection, length: int) -> int:
    """Cadeia de entidades seed ← d0 ← d1 ← … — retorna a semente."""
    prev = _ent(conn, "seed")
    seed_occ = prev
    for i in range(length):
        nxt = _ent(conn, f"d{i}")
        _dom_rel(conn, nxt, prev)
        prev = nxt
    return seed_occ


# ---------------------------------------------------------------------
# blast_radius (grafo de código) — o gap original do scorecard
# ---------------------------------------------------------------------


def test_code_edge_scan_cap_is_declared() -> None:
    """Fan-out > ``max_nodes``: a varredura lê o prefixo
    ``ORDER BY id`` e declara — o bug medido do scorecard."""
    conn = _db()
    seed = _sym(conn, "m.seed")
    for i in range(8):
        _rel(conn, _sym(conn, f"m.c{i}"), seed)
    out, trunc = blast_radius.blast(
        conn, _SNAP, {"py:function:m.seed": seed},
        ImpactLimits(max_nodes=5))
    assert len(out) == 5  # só o prefixo determinístico emitiu
    assert trunc is True


def test_code_edge_scan_exact_fit_not_truncated() -> None:
    """Fit exato (vizinhos == ``max_nodes``): a sonda +1 prova que
    nada ficou de fora — honestamente não-truncado."""
    conn = _db()
    seed = _sym(conn, "m.seed")
    for i in range(5):
        _rel(conn, _sym(conn, f"m.c{i}"), seed)
    out, trunc = blast_radius.blast(
        conn, _SNAP, {"py:function:m.seed": seed},
        ImpactLimits(max_nodes=5))
    assert len(out) == 5
    assert trunc is False


def test_code_depth_frontier_is_declared() -> None:
    """Cadeia mais funda que ``max_depth``: o nó da fronteira ainda
    tinha consumidor atravessável → corte declarado."""
    conn = _db()
    seed = _chain(conn, 4)  # seed ← c0 ← c1 ← c2 ← c3
    out, trunc = blast_radius.blast(
        conn, _SNAP, {"k": seed},
        ImpactLimits(max_depth=2, max_nodes=50, max_paths=50))
    assert trunc is True
    hops = {c.qualified_name: (c.path.hops if c.path else -1)
            for c in out}
    assert hops["m.chain0"] == 1
    assert hops["m.chain1"] == 2
    assert "m.chain2" not in hops  # nunca alcançado — e declarado


def test_code_depth_boundary_clean_not_truncated() -> None:
    """Fronteira exata: último emitido em ``hops == max_depth`` SEM
    consumidores restantes — nada foi descartado."""
    conn = _db()
    seed = _chain(conn, 2)  # seed ← c0 ← c1 — exatamente max_depth
    out, trunc = blast_radius.blast(
        conn, _SNAP, {"k": seed},
        ImpactLimits(max_depth=2, max_nodes=50, max_paths=50))
    assert len(out) == 2
    assert trunc is False


def test_code_path_cap_unchanged() -> None:
    """``max_paths`` — comportamento preservado: emissão para no cap."""
    conn = _db()
    seed = _sym(conn, "m.seed")
    for i in range(4):
        _rel(conn, _sym(conn, f"m.c{i}"), seed)
    out, trunc = blast_radius.blast(
        conn, _SNAP, {"k": seed}, ImpactLimits(max_paths=2))
    assert len(out) == 2
    assert trunc is True


# ---------------------------------------------------------------------
# domain_traversal (grafo misto) — razões discriminadas
# ---------------------------------------------------------------------


def test_domain_edge_scan_cap_reason() -> None:
    """``domain_relations`` além do cap: ``max-nodes-edge-scan``."""
    conn = _db()
    seed_occ = _ent(conn, "seed")
    for i in range(8):
        _dom_rel(conn, _ent(conn, f"c{i}"), seed_occ)
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={},
        ent_origins={"dom:sql/test//ns//seed": seed_occ},
        limits=ImpactLimits(max_nodes=5))
    assert len(out.candidates) == 5
    assert out.truncated is True
    assert out.reasons == ("max-nodes-edge-scan",)


def test_domain_depth_frontier_reason() -> None:
    """Cadeia de domínio além de ``max_depth`` com trabalho restante."""
    conn = _db()
    seed_occ = _dom_chain(conn, 4)  # seed ← d0 ← d1 ← d2 ← d3
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={}, ent_origins={"k": seed_occ},
        limits=ImpactLimits(max_depth=2, max_nodes=50, max_paths=50))
    assert out.truncated is True
    assert "max-depth-frontier" in out.reasons
    assert all(c.path is not None and c.path.hops <= 2
               for c in out.candidates)


def test_domain_frontier_uncounted_uncertainty() -> None:
    """Nó de fronteira com dep ``to_unresolved`` nunca contada —
    a incerteza descartada também é trabalho perdido → corte."""
    conn = _db()
    seed_occ = _ent(conn, "seed")
    d0 = _ent(conn, "d0")
    _dom_rel(conn, d0, seed_occ)  # d0 DERIVES_FROM seed → hops 1
    conn.execute(
        "INSERT INTO domain_relations"
        " (snapshot_id, from_entity_occ_id, type, resolution_status,"
        "  confidence_class, resolver, to_unresolved)"
        " VALUES (?, ?, 'DEPENDS_ON', 'unresolved', 'syntactic',"
        "  'test', 'ext.thing')", (_SNAP, d0))
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={}, ent_origins={"k": seed_occ},
        limits=ImpactLimits(max_depth=1, max_nodes=50, max_paths=50))
    assert out.truncated is True
    assert "max-depth-frontier" in out.reasons
    # d0 nunca foi expandido: a dep não entrou no agregado — e o
    # corte é o que declara essa perda.
    assert not out.uncertain


def test_domain_clean_frontier_not_truncated() -> None:
    """Fronteira exata de domínio sem trabalho restante → False."""
    conn = _db()
    seed_occ = _dom_chain(conn, 2)  # exatamente max_depth hops
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={}, ent_origins={"k": seed_occ},
        limits=ImpactLimits(max_depth=2, max_nodes=50, max_paths=50))
    assert len(out.candidates) == 2
    assert out.truncated is False
    assert out.reasons == ()


def test_domain_path_cap_reason() -> None:
    conn = _db()
    seed_occ = _ent(conn, "seed")
    for i in range(4):
        _dom_rel(conn, _ent(conn, f"c{i}"), seed_occ)
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={}, ent_origins={"k": seed_occ},
        limits=ImpactLimits(max_paths=2))
    assert len(out.candidates) == 2
    assert out.truncated is True
    assert out.reasons == ("max-paths",)


def test_mixed_scan_and_frontier_reasons() -> None:
    """Dois bounds reais no mesmo blast → as duas razões declaradas."""
    conn = _db()
    seed_occ = _ent(conn, "seed")
    # fan-out > max_nodes na semente + um braço de profundidade 2.
    prev = seed_occ
    for i in range(7):
        c = _ent(conn, f"c{i}")
        _dom_rel(conn, c, prev)
        if i == 0:
            leaf = _ent(conn, "leaf")
            _dom_rel(conn, leaf, c)  # leaf DERIVES_FROM c0
    out = domain_traversal.blast(
        conn, _SNAP, sym_origins={}, ent_origins={"k": seed_occ},
        limits=ImpactLimits(max_depth=1, max_nodes=5, max_paths=50))
    assert out.truncated is True
    assert "max-nodes-edge-scan" in out.reasons
    assert "max-depth-frontier" in out.reasons


# ---------------------------------------------------------------------
# nível engine — o envelope carrega as razões (schema v1 aditivo)
# ---------------------------------------------------------------------


def _engine_db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.execute(
        "INSERT INTO repositories (root_handle, identity_key)"
        " VALUES (?, 'path:' || ?)", (str(root), str(root)))
    conn.execute(
        "INSERT INTO snapshots (repository_id, worktree_digest,"
        " status) VALUES (1, 'd', 'READY')")
    conn.execute(
        "INSERT INTO source_files (repository_id, relative_path,"
        " language) VALUES (1, 'a.py', 'python')")
    return conn


def test_engine_result_carries_truncation_reasons(tmp_path) -> None:
    """E2E: cadeia de símbolos além de ``max_depth`` → a razão chega
    ao ``ImpactResult`` e ao JSON (campo aditivo, schema v1)."""
    root = tmp_path / "repo"
    root.mkdir()
    conn = _engine_db(root)
    _chain(conn, 3)  # seed ← c0 ← c1 ← c2
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(root), symbol="m.seed",
        limits=ImpactLimits(max_depth=1, max_nodes=50)))
    assert res.truncated is True
    assert "max-depth-frontier" in res.truncation_reasons
    # contrato: boolean é a união das razões — nunca diverge.
    assert res.truncated == bool(res.truncation_reasons)
    payload = json.loads(to_json(res))
    assert payload["truncation_reasons"] == \
        list(res.truncation_reasons)
    md = to_markdown(res)
    assert "max-depth-frontier" in md
    conn.close()


def test_engine_domain_frontier_reason(tmp_path) -> None:
    """E2E com seed de entidade: fronteira de domínio declarada."""
    root = tmp_path / "repo"
    root.mkdir()
    conn = _engine_db(root)
    _dom_chain(conn, 2)  # seed ← d0 ← d1
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(root), entity="seed",
        limits=ImpactLimits(max_depth=1, max_nodes=50)))
    assert res.truncated is True
    assert "max-depth-frontier" in res.truncation_reasons
    conn.close()


def test_engine_unbounded_run_stays_clean(tmp_path) -> None:
    """Grafo pequeno dentro de todos os bounds → False + sem razões."""
    root = tmp_path / "repo"
    root.mkdir()
    conn = _engine_db(root)
    _chain(conn, 2)
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(root), symbol="m.seed"))
    assert res.truncated is False
    assert res.truncation_reasons == ()
    assert len(res.impacts) == 2
    conn.close()


# ---------------------------------------------------------------------
# determinismo mid-order: prefixo estável independente do cap
# ---------------------------------------------------------------------


def test_truncated_prefix_is_deterministic() -> None:
    """Dois runs com o mesmo cap emitem o MESMO prefixo
    ``ORDER BY id`` — truncamento nunca é ordenação aleatória."""
    conn = _db()
    seed = _sym(conn, "m.seed")
    for i in range(9):
        _rel(conn, _sym(conn, f"m.c{i}"), seed)
    limits = ImpactLimits(max_nodes=4)
    keys_a = [c.symbol_key for c in blast_radius.blast(
        conn, _SNAP, {"k": seed}, limits)[0]]
    keys_b = [c.symbol_key for c in blast_radius.blast(
        conn, _SNAP, {"k": seed}, limits)[0]]
    assert keys_a == keys_b
    # e o prefixo truncado é prefixo do run sem cap de scan
    full, _ = blast_radius.blast(
        conn, _SNAP, {"k": seed}, ImpactLimits())
    assert keys_a == [c.symbol_key for c in full[:len(keys_a)]]
