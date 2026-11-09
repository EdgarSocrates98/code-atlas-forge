"""Body-free Obsidian document relations for Code Atlas federation.

Code Atlas owns graph relations, not vault access.  This adapter consumes a
versioned NoteRef projection from a trusted producer and emits unresolved
document references with hash/path evidence.  It never invents code symbols
and never stores a note body.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

SCHEMA = "codeatlas/ObsidianDocumentRelations/v1"
NOTE_REF_SCHEMA = "theforge/ObsidianNoteRef/v1"


def _relative(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if not value or value.startswith(("/", "//")) or ":" in value:
        raise ValueError("Obsidian reference path must be relative")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Obsidian reference path is unsafe")
    return value


def project_obsidian_relations(note_ref: Mapping[str, Any]) -> dict[str, Any]:
    """Project parsed NoteRef metadata into Atlas relation candidates."""
    if note_ref.get("schema") != NOTE_REF_SCHEMA:
        raise ValueError("unsupported Obsidian NoteRef schema")
    path = _relative(str(note_ref.get("path") or ""))
    vault_id = str(note_ref.get("vault_id") or "").strip()
    content_hash = str(note_ref.get("content_hash") or "").strip()
    if not vault_id:
        raise ValueError("Obsidian NoteRef vault_id is required")
    if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
        raise ValueError("Obsidian NoteRef content_hash must be sha256")
    source = f"obsidian:{vault_id}:{path}"
    evidence = {
        "kind": "obsidian-note",
        "ref": source,
        "path": path,
        "content_hash": content_hash,
        "provenance": str(note_ref.get("source") or "unknown"),
    }
    references: list[dict[str, str]] = []
    targets = [
        ("wikilinks", str(target).split("#", 1)[0].strip())
        for target in note_ref.get("wikilinks") or []
    ]
    targets.extend(
        ("relative_links", str(target).split("#", 1)[0].strip())
        for target in note_ref.get("relative_links") or []
    )
    targets.extend(
        ("attachments", str(target).strip())
        for target in note_ref.get("attachments") or []
    )
    seen: set[str] = set()
    for kind, target in targets:
        if not target:
            continue
        try:
            target_path = _relative(target)
        except ValueError:
            continue
        target_ref = f"obsidian:{vault_id}:{target_path}"
        if target_ref in seen or target_ref == source:
            continue
        seen.add(target_ref)
        references.append(
            {
                "relation_type": "REFERENCES",
                "from": source,
                "to_unresolved": target_ref,
                "target_kind": kind,
                "resolution_status": "unresolved",
                "confidence_class": "syntactic",
            }
        )
    return {
        "schema": SCHEMA,
        "version": "v1",
        "source": source,
        "content_hash": content_hash,
        "evidence": [evidence],
        "relations": references,
        "diagnostics": [],
        "body_included": False,
        "identity": hashlib.sha256(source.encode("utf-8")).hexdigest()[:24],
    }


__all__ = ["project_obsidian_relations"]
