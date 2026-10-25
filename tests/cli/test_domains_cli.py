"""lf-0123 Phase 15 — ``code-atlas domains`` + ``index --domains``.

Superfície read-only de domínio: listagem de adapters, entidades,
cartão de entidade e linhagem bounded. Honestidade de ausência:
adapters desabilitados → vazio com nota, nunca erro.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_atlas import cli

SQL_FILES = {
    "landing.sql": (
        "CREATE TABLE landing.orders (id INT, amount DOUBLE);\n"
        "INSERT INTO landing.orders SELECT id, amount FROM staging.raw_orders;\n"
    ),
    "mart.sql": (
        "CREATE VIEW mart.v_orders AS SELECT id, amount FROM landing.orders;\n"
    ),
}


@pytest.fixture()
def dom_repo(tmp_path: Path) -> Path:
    """Repo mínimo com artefatos SQL → entidades + linhagem real."""
    repo = tmp_path / "mini"
    repo.mkdir()
    for rel, content in SQL_FILES.items():
        (repo / rel).write_text(content)
    return repo


def _out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _err(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


class TestDomainsList:
    def test_list_json(
        self, ws: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # não precisa de repo indexado — listar não extrai
        assert cli.main(["domains", "list", "--json"]) == 0
        payload = _out(capsys)
        ids = {a["domain_id"] for a in payload["adapters"]}
        assert {"sql", "pyspark", "terraform", "aws-data", "cicd",
                "api"} == ids
        sql = next(a for a in payload["adapters"]
                   if a["domain_id"] == "sql")
        assert sql["enabled"] is True
        assert ".sql" in sql["artifact_support"]["extensions"]
        assert "data_table" in sql["entity_kinds"]
        assert payload["notes"]  # opt-in declarado

    def test_list_human(self, ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["domains", "list"]) == 0
        out = capsys.readouterr().out
        assert "sql" in out and "opt-in" in out


class TestIndexDomainsFlag:
    def test_index_with_domains(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["index", str(dom_repo), "--domains", "sql",
                         "--json"]) == 0
        payload = _out(capsys)
        assert payload["status"] == "success"
        assert payload["domains"] == ["sql"]
        assert payload["cross_domain"] is False
        assert payload["stats"]["domain_adapters"] == 1
        assert payload["stats"]["domain_sql_entities"] > 0

    def test_index_default_domains_off(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # retrocompatível: sem flag → registry None → zero rows de domínio
        assert cli.main(["index", str(dom_repo), "--json"]) == 0
        payload = _out(capsys)
        assert payload["domains"] == []
        assert payload["cross_domain"] is False
        assert payload["stats"].get("domain_adapters", 0) == 0

    def test_index_unknown_domain_is_typed_error(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["index", str(dom_repo), "--domains", "bogus",
                         "--json"]) == 1
        payload = _err(capsys)
        assert "unknown domain" in payload["error"]
        assert "sql" in payload["error"]  # lista os válidos

    def test_index_cross_domain_requires_domains(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["index", str(dom_repo), "--cross-domain",
                         "--json"]) == 1
        assert "requires --domains" in _err(capsys)["error"]

    def test_index_domains_all(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["index", str(dom_repo), "--domains", "all",
                         "--json"]) == 0
        payload = _out(capsys)
        assert sorted(payload["domains"]) == [
            "api", "aws-data", "cicd", "pyspark", "sql", "terraform"]
        # os que não casam artefato nenhum produzem zero entidades — não erro
        assert payload["stats"]["domain_sql_entities"] > 0


@pytest.fixture()
def indexed(
    ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    """Repo indexado com adapter ``sql`` habilitado."""
    assert cli.main(["index", str(dom_repo), "--domains", "sql",
                     "--json"]) == 0
    capsys.readouterr()
    return dom_repo


class TestDomainsEntities:
    def test_entities_listing(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entities", "--repo", str(indexed),
                         "--json"]) == 0
        payload = _out(capsys)
        assert payload["total"] > 0
        names = {i["display_name"] for i in payload["items"]}
        assert "landing.orders" in names
        assert "mart.v_orders" in names
        item = payload["items"][0]
        assert {"entity_id", "domain", "kind", "qualified_name",
                "identity_key", "occurrences"} <= set(item)

    def test_entities_filters(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(
            ["domains", "entities", "--repo", str(indexed),
             "--domain", "sql", "--kind", "data_table",
             "--q", "orders", "--json"]) == 0
        payload = _out(capsys)
        assert payload["total"] >= 1
        assert all(i["kind"] == "data_table" for i in payload["items"])
        assert all("orders" in i["display_name"] for i in payload["items"])

    def test_entities_invalid_kind(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(
            ["domains", "entities", "--repo", str(indexed),
             "--kind", "frobnicate", "--json"]) == 1
        assert "unknown --kind" in _err(capsys)["error"]

    def test_entities_bounded(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(
            ["domains", "entities", "--repo", str(indexed),
             "--limit", "2", "--json"]) == 0
        payload = _out(capsys)
        assert len(payload["items"]) == 2
        assert payload["truncated"] is True
        assert payload["total"] > 2

    def test_entities_absent_domain_honest(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # index SEM --domains → zero rows → vazio com nota, rc 0
        assert cli.main(["index", str(dom_repo), "--json"]) == 0
        capsys.readouterr()
        assert cli.main(["domains", "entities", "--repo", str(dom_repo),
                         "--json"]) == 0
        payload = _out(capsys)
        assert payload["total"] == 0
        assert payload["items"] == []
        assert payload["notes"]
        assert "opt-in" in payload["notes"][0]

    def test_entities_human_absent(
        self, ws: Path, dom_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["index", str(dom_repo)]) == 0
        capsys.readouterr()
        assert cli.main(["domains", "entities", "--repo", str(dom_repo)]) == 0
        out = capsys.readouterr().out
        assert "opt-in" in out and "total=0" in out


class TestDomainsEntity:
    def test_entity_by_identity_key(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entities", "--repo", str(indexed),
                         "--kind", "data_table", "--json"]) == 0
        items = _out(capsys)["items"]
        key = next(i["identity_key"] for i in items
                   if i["display_name"] == "landing.orders")
        # sem --repo: a chave dom: carrega o repo no segmento 1
        assert cli.main(["domains", "entity", key, "--json"]) == 0
        payload = _out(capsys)
        ent = payload["entity"]
        ident = ent["identity"]
        assert ident["display_name"] == "landing.orders"
        assert ident["domain"] == "sql"
        assert ident["namespace"] == "landing"
        assert ident["identity_key"] == key
        assert ent["occurrences"]  # path/line/col reais
        assert ent["occurrences"][0]["path"] == "landing.sql"
        assert ent["relations_summary"]
        assert ent["evidence_count"] >= 1
        total = sum(r["count"] for r in ent["relations_summary"])
        assert total > 0

    def test_entity_by_numeric_id_and_name(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entity", "mart.v_orders", "--repo",
                         str(indexed), "--json"]) == 0
        payload = _out(capsys)
        assert payload["entity"]["identity"]["kind"] == "data_view"
        eid = payload["entity"]["entity_id"]
        assert cli.main(["domains", "entity", str(eid), "--json"]) == 0
        again = _out(capsys)
        assert again["entity"]["entity_id"] == eid

    def test_entity_ambiguous_is_honest(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # 'id' casa colunas landing.orders.id e mart.v_orders.id
        assert cli.main(["domains", "entity", "id", "--repo",
                         str(indexed), "--json"]) == 1
        payload = _err(capsys)
        assert "ambiguous" in payload["error"]

    def test_entity_not_found(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entity", "no.such.table", "--repo",
                         str(indexed), "--json"]) == 1
        assert "not found" in _err(capsys)["error"]

    def test_entity_missing_arg_usage(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entity"]) == 2  # EXIT_USAGE

    def test_entity_human_card(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "entity", "landing.orders", "--repo",
                         str(indexed)]) == 0
        out = capsys.readouterr().out
        assert "landing.orders" in out
        assert "identity_key: dom:sql/" in out
        assert "occurrences" in out and "relations" in out


class TestDomainsLineage:
    def test_lineage_up(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage", "mart.v_orders",
                         "--direction", "up", "--depth", "2",
                         "--json"]) == 0
        payload = _out(capsys)
        assert payload["entity"]["display_name"] == "mart.v_orders"
        refs = {n["ref"] for n in payload["nodes"]}
        assert "landing.orders" in refs
        assert "staging.raw_orders" in refs  # depth 2
        types = {e["type"] for e in payload["edges"]}
        assert types <= {"READS_FROM", "WRITES_TO", "TRANSFORMS",
                         "DERIVES_FROM"}
        assert all(e["resolution_status"] == "resolved"
                   for e in payload["edges"])

    def test_lineage_down(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage", "staging.raw_orders",
                         "--direction", "down", "--depth", "3",
                         "--json"]) == 0
        payload = _out(capsys)
        refs = {n["ref"] for n in payload["nodes"]}
        assert "landing.orders" in refs      # d1
        assert "mart.v_orders" in refs       # d2 via landing.orders

    def test_lineage_depth_bounded(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage", "mart.v_orders",
                         "--direction", "up", "--depth", "1",
                         "--json"]) == 0
        payload = _out(capsys)
        assert max(n["depth"] for n in payload["nodes"]) <= 1
        refs = {n["ref"] for n in payload["nodes"]}
        assert "staging.raw_orders" not in refs  # só alcançável em d2

    def test_lineage_direction_both_human(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage", "landing.orders",
                         "--direction", "both"]) == 0
        out = capsys.readouterr().out
        assert "upstream" in out and "downstream" in out
        assert "WRITES_TO" in out or "DERIVES_FROM" in out

    def test_lineage_missing_arg_usage(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage"]) == 2

    def test_lineage_not_found(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["domains", "lineage", "ghost.table", "--repo",
                         str(indexed), "--json"]) == 1
        assert "not found" in _err(capsys)["error"]

    def test_lineage_no_edges_note(
        self, indexed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # coluna folha sem relações de fluxo próprias → nota honesta
        assert cli.main(["domains", "entities", "--repo", str(indexed),
                         "--kind", "data_column", "--json"]) == 0
        col = _out(capsys)["items"][0]
        rc = cli.main(["domains", "lineage", col["identity_key"],
                       "--json"])
        payload = _out(capsys)
        assert rc == 0
        if not payload["edges"]:
            assert payload["notes"]


class TestDomainsHelp:
    def test_help_lists_domains(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        assert "domains" in capsys.readouterr().out

    def test_domains_subcommand_help(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main(["domains", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for word in ("list", "entities", "entity", "lineage"):
            assert word in out
