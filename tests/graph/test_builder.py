"""Testes do graph builder — grafo real, tipado, determinístico.

Ground truth montada por fixture repo + parse real; asserts verificam
tipos, proveniência e status de resolução (resolved/ambiguous/
unresolved/external preservados).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_atlas.discovery.scanner import scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.queries import GraphView
from code_atlas.graph.validation import validate_graph
from code_atlas.parsers import ParseEngine


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text('"""pkg"""\n')
    (r / "pkg" / "util.py").write_text(
        "class Base: pass\n\ndef helper(): return 1\n"
    )
    (r / "pkg" / "core.py").write_text(
        "from pkg.util import helper, Base\n"
        "import os\n\n"
        "class Engine(Base):\n"
        "    def run(self): return helper()\n"
    )
    (r / "main.py").write_text(
        "import pkg.core\n\ndef main(): return pkg.core.Engine\n"
    )
    (r / "broken.py").write_text("def x(:\n")
    return r


@pytest.fixture()
def built(repo: Path):
    s = scan(repo)
    eng = ParseEngine()
    results = eng.parse_batch(
        [(f.path, (repo / f.path).read_bytes()) for f in s.files]
    )
    return GraphBuilder().build("proj", s, results), s


def _edges(g, type_):
    return [e for e in g.edges if e.type == type_]


def test_node_kinds_and_contains(built) -> None:
    g, _ = built
    assert validate_graph(g) == []
    kinds = {n.kind for n in g.nodes}
    assert {"repository", "package", "file", "module", "class", "function",
            "method"} <= kinds
    contains = _edges(g, "CONTAINS")
    assert all(e.to_id is not None for e in contains)
    view = GraphView(g)
    repo_nid = next(n.node_id for n in g.nodes if n.kind == "repository")
    # repo → package → file → module encadeado
    pkg_nids = {e.to_id for e in view.edges_from(repo_nid, "CONTAINS")}
    assert "package:pkg" in pkg_nids
    file_nids = {e.to_id for e in view.edges_from("package:pkg", "CONTAINS")}
    assert "file:pkg/util.py" in file_nids
    mod_nids = {e.to_id for e in view.edges_from("file:pkg/util.py", "CONTAINS")}
    assert "module:python:pkg/util.py" in mod_nids


def test_defines_hierarchy(built) -> None:
    g, _ = built
    view = GraphView(g)
    defines = view.edges_from("module:python:pkg/core.py", "DEFINES")
    targets = {e.to_id for e in defines}
    # módulo define a classe; o método é definido pela classe
    assert targets == {"sym:python:class:pkg.core.Engine"}
    m = view.edges_from("sym:python:class:pkg.core.Engine", "DEFINES")
    assert {e.to_id for e in m} == {"sym:python:method:pkg.core.Engine.run"}


def test_imports_resolution_statuses(built) -> None:
    g, _ = built
    imports = _edges(g, "IMPORTS")
    by_text = {e.to_text: e for e in imports}
    # `from pkg.util import helper, Base` → to_text por membro,
    # resolvendo para o módulo local pkg/util.py
    for member in ("pkg.util.helper", "pkg.util.Base"):
        assert by_text[member].to_id == "module:python:pkg/util.py"
        assert by_text[member].resolution_status == "resolved"
    assert by_text["os"].resolution_status == "external"
    assert by_text["os"].to_id is None
    assert by_text["pkg.core"].resolution_status == "resolved"


def test_calls_resolution(built) -> None:
    g, _ = built
    calls = _edges(g, "CALLS")
    # helper() em core.py resolve p/ sym local via from-import
    hit = [e for e in calls if e.to_text == "helper"]
    assert hit and hit[0].resolution_status == "resolved"
    assert hit[0].to_id == "sym:python:function:pkg.util.helper"
    # pkg.core.Engine em main.py — chamada? é acesso (não call): ver
    # construção `pkg.core.Engine` vira REFERENCES? não — é identifier path;
    # o teste real: to_text preservado quando não resolve
    unresolved = [e for e in calls if e.resolution_status == "unresolved"]
    assert all(e.to_text for e in unresolved)


def test_extends_resolves_through_import(built) -> None:
    g, _ = built
    ext = _edges(g, "EXTENDS")
    assert len(ext) == 1
    assert ext[0].to_id == "sym:python:class:pkg.util.Base"
    assert ext[0].resolution_status == "resolved"


def test_provenance_on_edges(built) -> None:
    g, _ = built
    for e in g.edges:
        if e.type in ("IMPORTS", "EXTENDS", "CALLS"):
            assert e.resolver
            assert e.confidence_class in ("resolved", "syntactic")
            for i in e.evidence_index:
                ev = g.evidence[i]
                assert ev.path and ev.content_hash and ev.extractor
                assert "+" in ev.extractor_version


def test_broken_file_marked_not_silent(built, repo: Path) -> None:
    eng = ParseEngine()
    res = eng.parse("broken.py", (repo / "broken.py").read_bytes())
    assert res.has_error
    assert any(d.code in ("error-node", "missing-node") for d in res.diagnostics)


def test_determinism(built, repo: Path) -> None:
    g1, s = built
    eng2 = ParseEngine()
    results2 = eng2.parse_batch(
        [(f.path, (repo / f.path).read_bytes()) for f in s.files]
    )
    g2 = GraphBuilder().build("proj", s, results2)
    assert [n.node_id for n in g1.nodes] == [n.node_id for n in g2.nodes]
    assert [
        (e.type, e.from_id, e.to_id, e.to_text, e.resolution_status)
        for e in g1.edges
    ] == [
        (e.type, e.from_id, e.to_id, e.to_text, e.resolution_status)
        for e in g2.edges
    ]


def test_no_dynamic_call_as_resolved(tmp_path: Path) -> None:
    """`obj.m()` nunca resolve por coincidência textual."""
    r = tmp_path / "p2"
    r.mkdir()
    (r / "a.py").write_text("def f(o): return o.m()\n")
    (r / "b.py").write_text("class M:\n    def m(self): pass\n")
    s = scan(r)
    eng = ParseEngine()
    res = eng.parse_batch(
        [(f.path, (r / f.path).read_bytes()) for f in s.files]
    )
    g = GraphBuilder().build("p2", s, res)
    calls = [e for e in g.edges if e.type == "CALLS" and e.to_text == "o.m"]
    assert calls and calls[0].resolution_status == "unresolved"
