"""Avaliação de retrieval/contexto do Cycle 4 (lf-0044).

Fixture controlada + ``retrieval_judgments.json`` commitado (ground
truth verificável). Métricas por estratégia: Precision@K, Recall@K,
MRR, NDCG@K, evidence validity, latência (n>=3 runs → mediana + range).
Contexto: tokens entregues, budget compliance, evidence density,
required-context coverage, missing-critical rate, redundância.

Sem provider de IA → métricas de tarefa agêntica ponta-a-ponta ficam
em ``not_run``; nenhuma economia de tokens é declarada como prova.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

from code_atlas.context.contracts import ContextRequest
from code_atlas.context.engine import ContextEngine
from code_atlas.eval.metrics import MetricRecord, aggregate, evaluate_query
from code_atlas.indexing import IndexCoordinator
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.engine import SearchEngine
from code_atlas.storage import connect, migrate

HERE = Path(__file__).parent
JUDGMENTS = HERE / "retrieval_judgments_v2.json"

EVAL_REPO: dict[str, str] = {
    "pkg/__init__.py": "",
    "pkg/orders.py": (
        "def validate_total(items):\n    return sum(i.price for i in items)\n\n\n"
        "class OrderService:\n    def create_order(self, items, gateway):\n"
        "        total = validate_total(items)\n"
        "        gateway.charge(total)\n        return total\n\n"
        "    def cancel_order(self, oid):\n        return oid\n"
    ),
    "pkg/inventory.py": (
        "def reserve_stock(sku, qty):\n    return (sku, qty)\n\n\n"
        "def release_stock(sku, qty):\n    return (sku, qty)\n"
    ),
    "pkg/payments.py": (
        "class CardGateway:\n    def charge(self, amount):\n        ...\n\n\n"
        "def charge_card(gw, amount):\n    return gw.charge(amount)\n\n\n"
        "def refund_card(gw, amount):\n    return gw.refund(amount)\n"
    ),
    "pkg/api.py": (
        "from .orders import OrderService\nfrom .payments import charge_card\n\n\n"
        "def checkout(items, gw):\n"
        "    svc = OrderService()\n"
        "    total = svc.create_order(items, gw)\n"
        "    charge_card(gw, total)\n    return total\n"
    ),
    "web/cart.ts": (
        "export interface CartItem { sku: string }\n\n"
        "export function renderCart(items: CartItem[]) {\n"
        "    return items.length;\n}\n"
    ),
    "tests/test_orders.py": (
        "from pkg.orders import OrderService\n\n\n"
        "def test_create():\n    svc = OrderService()\n    assert svc\n"
    ),
}


def build_eval_repo(root: Path) -> sqlite3.Connection:
    for rel, content in EVAL_REPO.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    conn = connect(root.parent / "atlas.db")
    migrate(conn)
    IndexCoordinator(conn).index(root)
    conn.row_factory = sqlite3.Row
    return conn


def _qnames(res: Any) -> list[str]:
    return [h.qualified_name for h in res.items]


def _ndcg(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(
        1.0 / (i + 1) for i, q in enumerate(ranked[:k]) if q in relevant
    )
    ideal = min(len(relevant), k)
    idcg = sum(1.0 / (i + 1) for i in range(ideal))
    return dcg / idcg if idcg else 0.0


def retrieval_block(tmp: Path, *, runs: int = 3) -> dict[str, Any]:
    root = tmp / "repo"
    conn = build_eval_repo(root)
    try:
        tasks = json.loads(JUDGMENTS.read_text())["tasks"]
        eng_v1 = SearchEngine(conn, enable_rerank=False)
        # (label, engine, strategy) — hybrid_v1 mede RRF puro p/
        # comparação honesta com o rerank task-aware (lf-0050)
        strategies: list[tuple[str, SearchEngine, str]] = [
            ("structural", SearchEngine(conn), "structural"),
            ("lexical", SearchEngine(conn), "lexical"),
            ("graph", SearchEngine(conn), "graph"),
            ("hybrid", SearchEngine(conn), "hybrid"),
            ("hybrid_v1", eng_v1, "hybrid"),
        ]
        k = 10
        out: dict[str, Any] = {"runs": runs, "k": k, "strategies": {}}
        for label, engine, strat in strategies:
            records: list[MetricRecord] = []
            ev_vals: list[float] = []
            lat_vals: list[float] = []
            fp = 0
            for task in tasks:
                rel = {q: g for q, g in task["relevant"].items()}
                lat: list[float] = []
                last = None
                for _ in range(runs):
                    t0 = time.monotonic()
                    last = engine.search(SearchRequest(
                        query=task["query"], repository_root=str(root),
                        strategy=strat, limit=k,
                    ))
                    lat.append((time.monotonic() - t0) * 1000.0)
                ranked = _qnames(last)
                records.append(evaluate_query(
                    task["id"], ranked, rel, k))
                fp += len([q for q in ranked if rel.get(q, 0) == 0])
                ev = [h for h in last.items if h.evidence]
                ev_vals.append(
                    len(ev) / len(last.items) if last.items else 1.0)
                lat_vals.append(statistics.median(lat))
            rep = aggregate(records)
            by_cat: dict[str, list[MetricRecord]] = {}
            by_lang: dict[str, list[MetricRecord]] = {}
            for rec, task in zip(records, tasks, strict=True):
                by_cat.setdefault(task["category"], []).append(rec)
                by_lang.setdefault(task["language"], []).append(rec)
            sm: dict[str, Any] = rep.macro | rep.micro  # chaves planas
            sm["evidence_validity"] = round(
                statistics.median(ev_vals), 4)
            sm["latency_ms"] = round(statistics.median(lat_vals), 3)
            sm["latency_range_ms"] = [
                round(min(lat_vals), 2), round(max(lat_vals), 2)]
            sm["false_positive_hits"] = fp
            sm["macro"] = rep.macro
            sm["micro"] = rep.micro
            sm["median"] = rep.median
            sm["no_relevant"] = rep.no_relevant
            sm["per_category"] = {
                c: aggregate(rs).macro for c, rs in by_cat.items()}
            sm["per_language"] = {
                lang: aggregate(rs).macro for lang, rs in by_lang.items()}
            # aliases retro-compatíveis (macro)
            sm["precision"] = round(rep.macro["precision_at_k"], 4)
            sm["recall"] = round(
                rep.macro.get("recall_at_k", 0.0), 4)
            sm["mrr"] = round(rep.macro["mrr"], 4)
            sm["ndcg"] = round(rep.macro["ndcg_at_k"], 4)
            out["strategies"][label] = sm
        out["context"] = _context_block(conn, root, tasks)
        out["not_run"] = [
            "tarefas agenticas ponta-a-ponta (sem provider de IA no "
            "ambiente — Grill Gate lf-0035)",
            "repos publicos externos (rede indisponivel — offline-first)",
        ]
        out["token_economy_rule"] = (
            "economia de tokens NUNCA declarada como 'pack < repo'; "
            "exige contabilidade de chamadas/falhas/expansoes "
            "(agente real = not_run)"
        )
        return out
    finally:
        conn.close()


def _context_block(
    conn: sqlite3.Connection, root: Path, tasks: list[dict],
) -> dict[str, Any]:
    engine = ContextEngine(conn)
    budgets = (4000, 800)
    per_task: list[dict[str, Any]] = []
    for task in tasks:
        required = set(task["required_context"])
        row: dict[str, Any] = {"task": task["id"], "levels": {}}
        for budget in budgets:
            pack = engine.build(ContextRequest(
                repository_root=str(root), task=task["query"],
                level="focus", budget_tokens=budget,
            ))
            delivered = {it.symbol_key for it in pack.items}
            ids = [it.item_id for it in pack.items]
            with_ev = sum(1 for it in pack.items if it.evidence)
            est = sum(it.est_tokens for it in pack.items)
            row["levels"][str(budget)] = {
                "tokens_estimated": est,
                "budget_compliant": est <= pack.budget.limit,
                "items": len(pack.items),
                "evidence_density": (
                    round(with_ev / len(pack.items), 4)
                    if pack.items else 0.0
                ),
                "required_coverage": (
                    round(len(delivered & required) / len(required), 4)
                    if required else 0.0
                ),
                "redundant_ids": len(ids) - len(set(ids)),
            }
        critical = engine.build(ContextRequest(
            repository_root=str(root), task=task["query"],
            level="focus", budget_tokens=4000,
        ))
        got = {it.symbol_key for it in critical.items}
        row["missing_critical"] = sorted(required - got)
        per_task.append(row)
    miss = sum(1 for r in per_task if r["missing_critical"])
    return {
        "budgets": list(budgets),
        "missing_critical_rate": round(miss / len(per_task), 4),
        "budget_violations": sum(
            1 for r in per_task for lv in r["levels"].values()
            if not lv["budget_compliant"]
        ),
        "per_task": per_task,
    }
