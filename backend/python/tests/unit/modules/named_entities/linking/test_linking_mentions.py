from __future__ import annotations

import pytest

from app.modules.named_entities.domain.kinds import (
    INTERCHANGEABLE_KINDS,
    EntityKind,
    interchangeable_kinds,
    kind_family,
)
from app.modules.named_entities.linking.mentions import (
    MAX_MENTIONS,
    NAME_KINDS,
    parse_mentions,
)


def _keys(name: str, kind: str | None = None) -> dict[EntityKind, str]:
    (mention,) = parse_mentions([{"name": name, **({"kind": kind} if kind else {})}]).mentions
    return dict(mention.keys)


def test_an_organization_is_keyed_as_indexing_keyed_it() -> None:
    assert _keys("Acme Inc.", "organization")[EntityKind.ORGANIZATION] == "name:organization:acme"
    assert _keys("  ACME, Inc ", "Organization")[EntityKind.ORGANIZATION] == "name:organization:acme"


def test_a_name_without_a_kind_is_tried_under_every_name_kind() -> None:
    keys = _keys("Northwind")
    assert set(keys) == set(NAME_KINDS) - {EntityKind.EMAIL, EntityKind.URL}
    assert keys[EntityKind.PRODUCT] == "name:product:northwind"


def test_a_location_hint_covers_the_whole_location_family() -> None:
    assert set(_keys("Paris", "location")) == set(INTERCHANGEABLE_KINDS)
    assert set(_keys("Paris", "city")) == set(INTERCHANGEABLE_KINDS)


def test_contacts_get_their_own_keys() -> None:
    assert _keys("Jane@Example.com", "email") == {EntityKind.EMAIL: "email:jane@example.com"}
    assert EntityKind.URL in _keys("https://status.northwind.example/incidents")


def test_a_contact_kind_needs_text_its_recognizer_would_have_stored() -> None:
    assert parse_mentions([{"name": "Northwind", "kind": "url"}]).mentions == []
    assert parse_mentions([{"name": "jane at example", "kind": "email"}]).mentions == []


@pytest.mark.parametrize("raw", [[{"name": ""}], [{"name": "   "}], [{}], [None], [{"name": 7}], [], None, ""])
def test_nothing_without_a_name_is_looked_up(raw: object) -> None:
    assert parse_mentions(raw).mentions == []


def test_a_bare_string_is_a_name() -> None:
    (mention,) = parse_mentions(["Acme"]).mentions
    assert mention.name == "Acme" and mention.kind_hint is None


def test_value_kinds_and_unknown_kinds_say_why() -> None:
    parsed = parse_mentions([{"name": "Q3 2026", "kind": "date_range"}, {"name": "Acme", "kind": "company"}])
    assert [m.name for m in parsed.mentions] == ["Acme"]
    assert parsed.mentions[0].kind_hint is None
    assert any("find_records_by_value" in note for note in parsed.notes)
    assert any("unknown kind 'company'" in note for note in parsed.notes)


def test_phone_and_ip_are_never_looked_up_by_name() -> None:
    (mention,) = parse_mentions([{"name": "Acme", "kind": "phone"}]).mentions
    assert EntityKind.PHONE not in mention.kinds


def test_at_most_five_names_and_duplicates_once() -> None:
    names = [{"name": f"Org {i}", "kind": "organization"} for i in range(MAX_MENTIONS + 1)]
    parsed = parse_mentions([{"name": "Org 0", "kind": "organization"}, *names])
    assert [m.name for m in parsed.mentions] == [f"Org {i}" for i in range(MAX_MENTIONS)]
    assert any("first 5" in note for note in parsed.notes)


def test_hinted_accepts_the_requested_kind_family_only() -> None:
    (hinted, free) = parse_mentions([{"name": "Paris", "kind": "city"}, {"name": "Acme"}]).mentions
    assert hinted.hinted("state") and not hinted.hinted("organization")
    assert free.hinted("organization")



@pytest.mark.parametrize(
    ("kind", "family"),
    [
        (EntityKind.CITY, set(INTERCHANGEABLE_KINDS)),
        (EntityKind.PRODUCT, {EntityKind.PRODUCT, EntityKind.ORGANIZATION}),
        (EntityKind.ORGANIZATION, {EntityKind.PRODUCT, EntityKind.ORGANIZATION}),
        (EntityKind.PERSON, {EntityKind.PERSON}),
        (EntityKind.SKILL, {EntityKind.SKILL}),
        (EntityKind.CURRENCY, {EntityKind.CURRENCY}),
    ],
)
def test_kind_families(kind: EntityKind, family: set[EntityKind]) -> None:
    assert set(kind_family(kind)) == family


def test_only_location_kinds_are_interchangeable() -> None:
    assert set(interchangeable_kinds(EntityKind.STATE)) == set(INTERCHANGEABLE_KINDS)
    assert interchangeable_kinds(EntityKind.PRODUCT) == (EntityKind.PRODUCT,)


def test_a_product_hint_also_looks_the_name_up_as_an_organization_but_settles_only_as_a_product() -> None:
    (mention,) = parse_mentions([{"name": "Halcyon Ledger", "kind": "product"}]).mentions
    assert set(_keys("Halcyon Ledger", "product")) == {EntityKind.PRODUCT, EntityKind.ORGANIZATION}
    assert mention.hinted("organization") and not mention.settles("organization") and mention.settles("product")
    (city,) = parse_mentions([{"name": "Hamburg", "kind": "state"}]).mentions
    assert city.settles("city")


def test_one_name_asked_as_a_product_and_as_an_organization_keeps_both_hints() -> None:
    parsed = parse_mentions([{"name": "Mosaic", "kind": "product"}, {"name": "Mosaic", "kind": "organization"}])
    assert [m.kind_hint for m in parsed.mentions] == [EntityKind.PRODUCT, EntityKind.ORGANIZATION]
    assert len(parse_mentions([{"name": "Mosaic", "kind": "product"}] * 2).mentions) == 1
