import pytest

from code_atlas.context.obsidian import project_obsidian_relations


def test_obsidian_relations_are_unresolved_and_body_free() -> None:
    result = project_obsidian_relations(
        {
            "schema": "theforge/ObsidianNoteRef/v1",
            "vault_id": "vault-1",
            "path": "runbooks/spark.md",
            "source": "product-forge",
            "content_hash": "a" * 64,
            "wikilinks": ["Spark Runtime#Skew"],
            "relative_links": ["docs/diagnosis.md"],
            "attachments": ["assets/diagram.png"],
            "content": "never accepted by this adapter",
        }
    )
    assert result["schema"] == "codeatlas/ObsidianDocumentRelations/v1"
    assert result["body_included"] is False
    assert len(result["relations"]) == 3
    assert all(item["resolution_status"] == "unresolved" for item in result["relations"])
    assert "content" not in result


@pytest.mark.parametrize("path", ["../outside.md", "/root.md", "notes//bad.md"])
def test_obsidian_relations_reject_unsafe_source_path(path: str) -> None:
    with pytest.raises(ValueError, match=r"relative|unsafe"):
        project_obsidian_relations(
            {
                "schema": "theforge/ObsidianNoteRef/v1",
                "vault_id": "vault-1",
                "path": path,
                "content_hash": "a" * 64,
            }
        )
