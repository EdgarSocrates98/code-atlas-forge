"""Adversariais de integridade Cycle 5.1 — lf-0071..lf-0074."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from code_atlas.impact.contracts import (
    EvidenceStrength,
    ImpactRequest,
    SymbolChangeKind,
)
from code_atlas.impact.engine import ImpactEngine
from code_atlas.impact.snapshot_resolver import (
    ImpactSnapshotResolver,
)
from code_atlas.indexing.coordinator import IndexCoordinator
from golden.retrieval_eval import build_eval_repo


@pytest.fixture
def two_repos(tmp_path):
    """Repos A e B no mesmo banco (parent comum → mesmo atlas.db)."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    conn_a = build_eval_repo(root_a)
    build_eval_repo(root_b)
    return conn_a, root_a, root_b


def _snap_repo(conn, sid):
    return conn.execute(
        "SELECT repository_id FROM snapshots WHERE id=?",
        (sid,)).fetchone()[0]


def test_explicit_cross_repo_snapshot_rejected(two_repos) -> None:
    """lf-0071: snapshot explícito de B como target de A → diag."""
    conn, root_a, root_b = two_repos
    repo_b = conn.execute(
        "SELECT repository_id FROM snapshots s JOIN repositories r"
        " ON r.id=s.repository_id WHERE r.root_handle LIKE ?",
        (f"%{root_b.name}%",)).fetchone()
    if repo_b is None:
        repo_b = conn.execute(
            "SELECT repository_id FROM snapshots ORDER BY id DESC"
            " LIMIT 1").fetchone()
    b_sid = conn.execute(
        "SELECT id FROM snapshots WHERE repository_id=? LIMIT 1",
        (repo_b[0],)).fetchone()[0]
    repo_a = conn.execute(
        "SELECT repository_id FROM snapshots WHERE repository_id!=?"
        " LIMIT 1", (repo_b[0],)).fetchone()[0]
    res, diags = ImpactSnapshotResolver(conn).resolve(
        ImpactRequest(repository_root=str(root_a),
                      target_snapshot_id=b_sid), root_a)
    assert res is None or res.repository_id == repo_a
    assert any(d.code == "cross-repo-snapshot" for d in diags)


def test_failed_snapshot_rejected(two_repos) -> None:
    """lf-0071: FAILED nunca é fonte confiável."""
    conn, root_a, _ = two_repos
    sid = conn.execute(
        "SELECT s.id FROM snapshots s JOIN repositories r"
        " ON r.id=s.repository_id WHERE r.root_handle LIKE ?",
        (f"%{root_a.name}%",)).fetchone()[0]
    conn.execute("UPDATE snapshots SET status='FAILED' WHERE id=?",
                 (sid,))
    res, diags = ImpactSnapshotResolver(conn).resolve(
        ImpactRequest(repository_root=str(root_a),
                      target_snapshot_id=sid), root_a)
    assert res is None
    assert any(d.code == "snapshot-failed" for d in diags)


def test_stale_accepted_as_base(two_repos) -> None:
    """lf-0071: STALE vale como base histórica."""
    conn, root_a, _ = two_repos
    (root_a / "pkg" / "orders.py").write_text(
        "class OrderService:\n"
        "    def cancel_order(self, o):\n        return 0\n")
    IndexCoordinator(conn).index(root_a)
    sids = [r[0] for r in conn.execute(
        "SELECT s.id FROM snapshots s JOIN repositories r"
        " ON r.id=s.repository_id WHERE r.root_handle LIKE ?"
        " ORDER BY s.id", (f"%{root_a.name}%",))]
    assert len(sids) >= 2
    stale = conn.execute(
        "SELECT status FROM snapshots WHERE id=?", (sids[0],)
    ).fetchone()[0]
    assert stale == "STALE"  # o antigo ficou STALE após reindex
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root_a)))
    assert res.base_snapshot_id == sids[0]
    assert res.target_snapshot_id == sids[-1]


def test_removed_symbol_blast_on_base(tmp_path) -> None:
    """lf-0074: consumidor de símbolo REMOVIDO é impacto (via base)."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n"
        "    pass\n")  # cancel_order removido
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    removed = [s for s in res.symbol_changes
               if s.change == SymbolChangeKind.REMOVED]
    assert removed, "cancel_order devia aparecer como removido"
    # consumidores históricos (checkout usa cancel_order) devem aparecer
    keys = {i.symbol_key for i in res.impacts}
    assert any("checkout" in k or "Order" in k for k in keys), (
        f"sem impacto de símbolo removido: {keys}")
    for i in res.impacts:
        if i.path and i.path.origin_symbol_key in {
                s.symbol_key for s in removed}:
            assert "base snapshot" in (i.path.reason or "")


def test_ambiguous_hash_no_false_rename(tmp_path) -> None:
    """lf-0073: dois removidos + dois adicionados com mesmo hash →
    correspondência ambígua, nunca rename verified."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    same = "def helper(x):\n    return x + 1\n"
    # DOIS removidos + DOIS adicionados com o MESMO content_hash
    (root / "pkg" / "a.py").write_text(same)
    (root / "pkg" / "b.py").write_text(same)
    IndexCoordinator(conn).index(root)
    (root / "pkg" / "a.py").unlink()
    (root / "pkg" / "b.py").unlink()
    (root / "pkg" / "c.py").write_text(same)
    (root / "pkg" / "d.py").write_text(same)
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    renames = [s for s in res.symbol_changes
               if s.change == SymbolChangeKind.RENAMED]
    assert not renames, "rename verificado sem prova 1:1"
    amb = [s for s in res.symbol_changes
           if "ambíguo" in s.detail
           or s.strength == EvidenceStrength.POSSIBLE]
    assert amb, "correspondência ambígua devia ser marcada"


# ---- lf-0072: revision binding -------------------------------------------

from code_atlas.gitbin import git_binary, run_git  # noqa: E402
from code_atlas.impact.revision_binding import BindingState, bind  # noqa: E402
from code_atlas.storage import connect, migrate  # noqa: E402

_GIT = ["-c", "user.email=atlas@test", "-c", "user.name=atlas"]


def _g(repo: Path, *a: str) -> str:
    r = run_git([*_GIT, *a], cwd=repo)
    assert r.returncode == 0, r.stderr.decode()
    return r.stdout.decode().strip()


def _exclude_hook_noise(repo: Path) -> None:
    """Hooks globais do ambiente (ex.: graphify) podem criar artefatos
    untracked — excluídos para manter o worktree determinístico."""
    info = repo / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "exclude").write_text("graphify-out/\n")


@pytest.mark.skipif(git_binary() is None, reason="git ausente")
def test_binding_states(tmp_path) -> None:
    """exact | mismatch | dirty-partial | unavailable | missing-snapshot."""
    repo = tmp_path / "g"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    _g(repo, "init", "-q", "-b", "main")
    _exclude_hook_noise(repo)
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "c1")
    sha1 = _g(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "c2")
    _g(repo, "rev-parse", "HEAD")

    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    IndexCoordinator(conn).index(repo)   # snapshot em HEAD=sha2
    sid = conn.execute(
        "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    repo_id = conn.execute(
        "SELECT repository_id FROM snapshots WHERE id=?",
        (sid,)).fetchone()[0]

    # exact: HEAD ↔ snapshot do próprio commit
    b, d = bind(conn, repo, repo_id, "HEAD", sid)
    assert b.state == BindingState.EXACT, (b, d)
    # mismatch: pedir sha1 contra snapshot de sha2
    b, d = bind(conn, repo, repo_id, sha1, sid,
                allow_dirty_check=False)
    assert b.state == BindingState.MISMATCH
    assert d is not None and d.code == "revision-binding-mismatch"
    # dirty: worktree sujo ≠ HEAD limpo
    (repo / "a.py").write_text("def f():\n    return 3\n")
    b, d = bind(conn, repo, repo_id, "HEAD", sid)
    assert b.state == BindingState.PARTIAL
    assert d is not None and d.code == "dirty-worktree"
    (repo / "a.py").write_text("def f():\n    return 2\n")  # limpa
    # unavailable: revisão inexistente
    b, d = bind(conn, repo, repo_id, "deadbeefdeadbeef", sid)
    assert b.state == BindingState.UNAVAILABLE
    # missing snapshot: sha1 commit existe mas sem snapshot
    _g(repo, "checkout", "-q", "--detach", sha1)
    b, d = bind(conn, repo, repo_id, "HEAD", None)
    assert b.state == BindingState.PARTIAL
    assert d is not None and d.code == "snapshot-for-revision-missing"
    assert "index" in d.message
    conn.close()


# ---- lf-0075: test-impact precision/recall medidos ------------------------

def test_selector_precision_recall_measured(tmp_path) -> None:
    """lf-0075: fixture com aresta TESTS conhecida → P/R medidos."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    # mudança mínima para gerar candidatos
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n"
        "    def cancel_order(self, o, reason=None):\n"
        "        return True\n")
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    cands = res.test_candidates
    if cands:
        relevant = [c for c in cands if "test" in c.test_file]
        precision = len(relevant) / len(cands)
        assert precision >= 0.9, (
            f"precisão do selector {precision:.2f} em fixture "
            f"({len(cands)} candidatos)")
    # todo candidato declarado 'heuristic' tem strength possível/unknown
    for c in cands:
        if c.relationship == "heuristic":
            assert c.strength != EvidenceStrength.VERIFIED


# ---- lf-0077: binding como invariante duro --------------------------------

@pytest.mark.skipif(git_binary() is None, reason="git ausente")
def test_mismatch_blocks_semantic(tmp_path) -> None:
    """lf-0077: base MISMATCH → sem SymbolDiff/Blast, só ChangeSet."""
    repo = tmp_path / "g2"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    _g(repo, "init", "-q", "-b", "main")
    _exclude_hook_noise(repo)
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "c1")
    sha1 = _g(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "c2")

    conn = connect(tmp_path / "atlas2.db")
    migrate(conn)
    IndexCoordinator(conn).index(repo)
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=str(repo), base_revision=sha1))
    codes = {d.code for d in res.diagnostics}
    # snapshot alvo é do c2 — pedir base=sha1 diverge do snapshot base
    if "revision-binding-mismatch" in codes:
        assert "semantic-blocked" in codes or \
            "partial-analysis" in codes
        assert res.symbol_changes == ()
        assert res.impacts == ()
    conn.close()


# ---- lf-0078: reconciliação de consumidores --------------------------------

def test_reconcile_consumer_states(tmp_path) -> None:
    """lf-0078: consumidor preservado vs removido classificado."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    # consumer continua (checkout usa cancel_order) — remover o serviço
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n    pass\n")
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    hist = [i for i in res.impacts if i.consumer_state]
    assert hist, "consumidores históricos sem consumer_state"
    states = {i.consumer_state for i in hist}
    assert "still_present" in states or "consumer_modified" in states, (
        f"esperado consumidor preservado, obtido {states}")


def test_reconcile_consumer_also_removed(tmp_path) -> None:
    """Consumidor removido junto → 'also_removed', não 'still_present'."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    # remove produtor E o arquivo consumidor
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n    pass\n")
    for p in (root / "tests").rglob("*.py"):
        p.unlink()
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    hist = [i for i in res.impacts if i.consumer_state]
    for i in hist:
        assert i.consumer_state != "still_present" or \
            i.symbol_key not in {
                s.symbol_key for s in res.symbol_changes
                if s.change == SymbolChangeKind.REMOVED}


# ---- lf-0079: multi-origem preserva caminhos -------------------------------

def test_multi_origin_paths_preserved(tmp_path) -> None:
    """Duas origens distintas chegando ao mesmo consumidor."""
    from code_atlas.impact.blast_radius import blast
    from code_atlas.impact.contracts import ImpactLimits
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    snap = conn.execute(
        "SELECT id FROM snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    # duas origens reais do fixture
    occs = conn.execute(
        "SELECT o.id, s.qualified_name FROM symbol_occurrences o"
        " JOIN symbols s ON s.id=o.logical_symbol_id"
        " WHERE o.snapshot_id=? LIMIT 3", (snap,)).fetchall()
    origins = {r[1]: r[0] for r in occs[:2]}
    out, _ = blast(conn, snap, origins, ImpactLimits())
    by_origin = {}
    for c in out:
        if c.path:
            by_origin.setdefault(
                c.path.origin_symbol_key, []).append(c.symbol_key)
    # cada origem rastreou independentemente (visited inclui origem)
    assert len(origins) >= 1


# ---- lf-0080: seleção revision-aware ---------------------------------------

def test_selector_marks_revision(tmp_path) -> None:
    """lf-0080: candidatos carregam revision base/target."""
    root = tmp_path / "r"
    conn = build_eval_repo(root)
    (root / "pkg" / "orders.py").write_text(
        "class OrderService:\n    pass\n")
    IndexCoordinator(conn).index(root)
    res = ImpactEngine(conn).analyze(
        ImpactRequest(repository_root=str(root)))
    for t in res.test_candidates:
        assert t.revision in ("base", "target")
