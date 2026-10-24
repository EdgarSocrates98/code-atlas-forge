"""lf-0055 — avaliação real-world sobre corpus vendored (offline).

Duas raízes indexadas separadamente (pyapp/tsapi) — isolamento real
por repositório. Métricas v2 por estratégia/categoria/linguagem.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from code_atlas.eval.metrics import MetricRecord, aggregate, evaluate_query
from code_atlas.indexing import IndexCoordinator
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.storage import connect, migrate

HERE = Path(__file__).parent
CORPUS = HERE.parent / "corpus" / "realworld"
JUDGMENTS = HERE / "realworld_judgments.json"


def index_corpus(tmp: Path) -> dict[str, sqlite3.Connection]:
    """Um db por projeto — o corpus herda o git remote do repo hospedeiro
    (mesmo identity_key → mesmo repo_id por design), então cada projeto
    precisa do próprio banco para isolamento real."""
    conns: dict[str, sqlite3.Connection] = {}
    for proj in ("pyapp", "tsapi"):
        conn = connect(tmp / f"{proj}.db")
        migrate(conn)
        IndexCoordinator(conn).index(CORPUS / proj)
        conn.row_factory = sqlite3.Row
        conns[proj] = conn
    return conns


def run(tmp: Path) -> dict[str, Any]:
    conns = index_corpus(tmp)
    tasks = json.loads(JUDGMENTS.read_text())["tasks"]
    by_strat: dict[str, list[MetricRecord]] = {}
    by_cat: dict[str, list[MetricRecord]] = {}
    by_lang: dict[str, list[MetricRecord]] = {}
    for t in tasks:
        proj = "tsapi" if t["language"] == "typescript" else "pyapp"
        eng = SearchEngine(conns[proj])
        root = str((CORPUS / proj).resolve())
        for strat in ("structural", "lexical", "hybrid"):
            res = eng.search(SearchRequest(
                query=t["query"], strategy=strat,
                repository_root=root, limit=10))
            rec = evaluate_query(
                t["id"], [h.qualified_name for h in res.items],
                t["relevant"], 10)
            by_strat.setdefault(strat, []).append(rec)
            by_cat.setdefault(f"{strat}:{t['category']}", []).append(rec)
            by_lang.setdefault(f"{strat}:{t['language']}", []).append(rec)
    return {
        "corpus": "vendored:pyapp+tsapi",
        "tasks": len(tasks),
        "macro": {s: aggregate(rs).macro for s, rs in by_strat.items()},
        "by_category": {c: aggregate(rs).macro
                        for c, rs in by_cat.items()},
        "by_language": {lang: aggregate(rs).macro
                        for lang, rs in by_lang.items()},
        "agent_e2e": {"not_run": "sem agente autorizado no ambiente"},
    }
