"""Identidade canônica de entidades de domínio (lf-0110).

Caso adversarial central da spec: homônimos em escopos diferentes
(catálogo, conta/região, ambiente, snapshot) NUNCA colidem.
"""

from __future__ import annotations

import pytest

from code_atlas.domains import DomainIdentity


def _ident(**kw: object) -> DomainIdentity:
    base = {
        "domain": "sql",
        "repository": "git-remote:git@x/r",
        "qualified_name": "orders",
    }
    base.update(kw)
    return DomainIdentity(**base)  # type: ignore[arg-type]


def test_required_fields() -> None:
    for field in ("domain", "repository", "qualified_name"):
        kw = {"domain": "d", "repository": "r", "qualified_name": "q"}
        kw[field] = ""
        with pytest.raises(ValueError, match=f"requires {field}"):
            DomainIdentity(**kw)


def test_fields_are_stripped() -> None:
    i = _ident(qualified_name="  orders  ")
    assert i.qualified_name == "orders"


def test_same_name_different_catalog_never_collides() -> None:
    """Duas tabelas `orders` em catálogos Glue distintos ≠ mesma entidade."""
    a = _ident(catalog="glue:111111111111:us-east-1", namespace="sales")
    b = _ident(catalog="glue:222222222222:us-east-1", namespace="sales")
    assert a.canonical_key() != b.canonical_key()
    assert a.logical_key() != b.logical_key()


def test_terraform_same_resource_different_region_never_collides() -> None:
    """aws_s3_bucket.logs em contas/regiões distintas ≠ mesma entidade."""
    a = DomainIdentity(
        domain="terraform", repository="r", qualified_name="aws_s3_bucket.logs",
        catalog="aws:111111111111:us-east-1",
    )
    b = DomainIdentity(
        domain="terraform", repository="r", qualified_name="aws_s3_bucket.logs",
        catalog="aws:111111111111:eu-west-1",
    )
    assert a.canonical_key() != b.canonical_key()


def test_environment_and_snapshot_change_identity() -> None:
    base = _ident(catalog="c")
    prod = _ident(catalog="c", environment="prod")
    snap = _ident(catalog="c", snapshot="sha:abc")
    assert len({base.canonical_key(), prod.canonical_key(),
                snap.canonical_key()}) == 3
    # logical_key ignora snapshot — identidade lógica é estável
    assert snap.logical_key() == base.logical_key()
    assert snap.logical().canonical_key() == base.canonical_key()


def test_namespace_vs_catalog_positions_do_not_collide() -> None:
    """Separadores dentro de um segmento não viram fronteira posicional."""
    a = _ident(catalog="a:b", namespace="c")
    b = _ident(catalog="a", namespace="b:c")
    assert a.canonical_key() != b.canonical_key()
    # round-trip prova que os segmentos decodificam corretamente
    assert DomainIdentity.parse_key(a.canonical_key()) == (
        "sql", "git-remote:git@x/r", "", "a:b", "c", "", "orders", "",
    )


def test_different_domains_never_collide() -> None:
    a = _ident(domain="sql")
    b = _ident(domain="terraform")
    assert a.canonical_key() != b.canonical_key()


def test_canonical_key_is_deterministic() -> None:
    assert _ident(catalog="c").canonical_key() == _ident(catalog="c").canonical_key()
    assert _ident(catalog="c").canonical_key().startswith("dom:")


def test_display_is_human_readable() -> None:
    i = _ident(catalog="glue:1:us-east-1", namespace="sales",
               environment="prod")
    assert i.display() == "sql:glue:1:us-east-1.sales.orders@prod"


def test_parse_key_rejects_foreign_scheme() -> None:
    assert DomainIdentity.parse_key("sym:python:function:x") is None
