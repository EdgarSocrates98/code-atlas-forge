from code_atlas.context.contracts import (
    Budget,
    ContextItem,
    ContextPack,
    PackDiagnostics,
    PackEvidence,
    PackManifest,
)
from code_atlas.context.knowledge import to_knowledge_context_pack


def test_context_pack_projection_is_citable_and_budgeted() -> None:
    pack = ContextPack(
        manifest=PackManifest(
            schema_version=2,
            repository="repo-a",
            snapshot_id=7,
            level="focus",
            estimator="heuristic-v1",
            tokens_estimated=True,
            selected=1,
            omitted=0,
            truncated=False,
            freshness="snapshot:7",
            ranking_explanation="direct symbol match",
        ),
        budget=Budget(
            limit=1000,
            reserved_meta=10,
            reserved_evidence=10,
            reserved_diagnostics=10,
            available_items=970,
            estimator="heuristic-v1",
        ),
        items=(ContextItem(
            item_id="symbol:main",
            kind="symbol",
            priority=1,
            title="main",
            body="def main(): pass",
            est_tokens=5,
            symbol_key="repo.main",
            path="src/main.py",
            evidence=(PackEvidence(kind="occurrence", ref="occ:1"),),
        ),),
        selected_symbols=("repo.main",),
        relevant_files=("src/main.py",),
        dependencies=(),
        unresolved_refs=(),
        expansion_refs=(),
        diagnostics=PackDiagnostics(),
    )
    projected = to_knowledge_context_pack(
        pack, query="find main", workspace_id="ws-1", principal="agent-1"
    )
    assert projected["schema"] == "theforge/KnowledgeContextPack/v1"
    assert projected["estimated_tokens"] == 5
    assert projected["evidence_ids"] == ["occ:1"]
    assert projected["items"][0]["path"] == "src/main.py"
    assert projected["items"][0]["source_id"] == "repo-a"
    assert projected["items"][0]["workspace_id"] == "ws-1"
    assert projected["items"][0]["version"] == "7"
    assert len(projected["items"][0]["content_hash"]) == 64


def test_context_pack_identity_changes_with_budget_and_selection() -> None:
    pack = ContextPack(
        manifest=PackManifest(
            schema_version=2,
            repository="repo-a",
            snapshot_id=7,
            level="focus",
            estimator="heuristic-v1",
            tokens_estimated=True,
            selected=1,
            omitted=0,
            truncated=False,
            freshness="snapshot:7",
            ranking_explanation="direct symbol match",
        ),
        budget=Budget(
            limit=1000,
            reserved_meta=10,
            reserved_evidence=10,
            reserved_diagnostics=10,
            available_items=970,
            estimator="heuristic-v1",
        ),
        items=(),
        selected_symbols=(),
        relevant_files=(),
        dependencies=(),
        unresolved_refs=(),
        expansion_refs=(),
        diagnostics=PackDiagnostics(),
    )
    first = to_knowledge_context_pack(
        pack, query="find main", workspace_id="ws-1", principal="agent-1")
    second = to_knowledge_context_pack(
        pack, query="find main", workspace_id="ws-1", principal="agent-1",
        product_id="p1")
    assert first["pack_id"] != second["pack_id"]

