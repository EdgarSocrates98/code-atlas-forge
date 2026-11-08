"""Adapter from Code Atlas ContextPack to the Forge knowledge envelope.

The adapter is deliberately dependency-free: Code Atlas remains the owner of
code retrieval and can publish a stable, redacted projection to other Forges
without importing the The Forge control plane.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from code_atlas.context.contracts import ContextPack

KNOWLEDGE_CONTEXT_PACK_SCHEMA = "theforge/KnowledgeContextPack/v1"


def _item_id(value: str) -> str:
    return "code-atlas:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def to_knowledge_context_pack(
    pack: ContextPack,
    *,
    query: str,
    workspace_id: str,
    principal: str,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Project a verified ContextPack into the cross-Forge contract.

    Bodies are retained because they are already selected by the Atlas budget
    engine; paths and evidence remain explicit so consumers can cite rather
    than treat the pack as an opaque prompt string.
    """
    selected = [_item_id(item.item_id) for item in pack.items]
    evidence = [e.ref for item in pack.items for e in item.evidence]
    evidence.extend(e.ref for e in pack.dependencies)
    source_id = pack.manifest.repository
    source_version = str(pack.manifest.snapshot_id)
    identity = {
        "repository": source_id,
        "snapshot": pack.manifest.snapshot_id,
        "query": query,
        "workspace_id": workspace_id,
        "product_id": product_id,
        "selected_items": selected,
        "evidence_ids": sorted(set(evidence)),
        "source_versions": {source_id: source_version},
        "strategy": {
            "level": pack.manifest.level,
            "estimator": pack.manifest.estimator,
            "ranking": pack.manifest.ranking_explanation,
        },
        "budget": pack.budget.limit,
    }
    pack_id = "code-atlas:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        "schema": KNOWLEDGE_CONTEXT_PACK_SCHEMA,
        "pack_id": pack_id,
        "query": query,
        "workspace_id": workspace_id,
        "principal": principal,
        **({"product_id": product_id} if product_id is not None else {}),
        "source_versions": {source_id: source_version},
        "selected_items": selected,
        "evidence_ids": sorted(set(evidence)),
        "estimated_tokens": sum(item.est_tokens for item in pack.items),
        "budget_tokens": pack.budget.limit,
        "truncations": ["context-budget"] if pack.manifest.truncated else [],
        "degraded_state": "partial" if pack.manifest.truncated else "none",
        "diagnostics": list(pack.diagnostics.codes) + list(pack.unresolved_refs),
        "items": [
            {
                "item_id": item_id,
                "source_id": source_id,
                "document_id": item.path or item.item_id,
                "workspace_id": workspace_id,
                "version": source_version,
                "product_id": product_id,
                "content_hash": hashlib.sha256(item.body.encode("utf-8")).hexdigest(),
                "kind": item.kind,
                "title": item.title,
                "body": item.body,
                "path": item.path,
                "symbol_key": item.symbol_key,
            }
            for item_id, item in zip(selected, pack.items, strict=False)
        ],
    }

