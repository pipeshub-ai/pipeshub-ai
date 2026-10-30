"""`describe_entity_filter`: a request's entityFilters in words for the prompt."""

from __future__ import annotations

import pytest

from app.modules.retrieval.entity_filters import describe_entity_filter


@pytest.mark.parametrize(("raw", "expected"), [
    ({"kinds": ["person"], "name": "Maya"}, 'an entity (person) whose name starts with "Maya"'),
    # Ids, kinds and name constrain one entity together, as the graph matches them.
    ({"entityIds": ["e1"], "kinds": ["person"]}, "the entity picked for this conversation (person)"),
    (
        {"entityIds": ["e1", "e2"], "name": "North"},
        'one of 2 entities picked for this conversation whose name starts with "North"',
    ),
    # A value kind is its own constraint, never part of the named entity's.
    (
        {"kinds": ["person", "currency"], "name": "Maya"},
        'an entity (person) whose name starts with "Maya" and any currency value',
    ),
    (
        {"kinds": ["organization", "date"], "mentionedDate": {"from": "2025-01-01"}},
        "a date on or after 2025-01-01 and any entity of kind organization",
    ),
    ({"name": "Northwind"}, 'an entity whose name starts with "Northwind"'),
    ({"kinds": ["person", "organization"]}, "any entity of kind person or organization"),
    ({"entityIds": ["e1"]}, "the entity picked for this conversation"),
    ({"entityIds": ["e1", "e2"]}, "one of 2 entities picked for this conversation"),
    ({"amount": {"min": 50000, "currency": "USD"}}, "an amount of at least 50,000 USD"),
    ({"amount": {"min": 10, "max": 20}}, "an amount from 10 to 20"),
    ({"amount": {"max": 5}}, "an amount of at most 5"),
    ({"amount": {"min": -1e308}}, "any amount"),
    ({"amount": {"currency": "EUR"}}, "an amount in EUR"),
    # The graph's date end is exclusive.
    (
        {"mentionedDate": {"from": "2025-01-01", "to": "2026-01-01"}},
        "a date from 2025-01-01 up to but not including 2026-01-01",
    ),
    ({"mentionedDate": {"from": "2025-12-31T15:30:00Z"}}, "a date on or after 2025-12-31T15:30:00Z"),
    ({"amount": {"min": 1234567, "max": 1234568}}, "an amount from 1,234,567 to 1,234,568"),
    ({"amount": {"min": 50000.5}}, "an amount of at least 50,000.5"),
    ({"quantity": {"min": 5, "dimension": "kg"}}, 'a quantity of at least 5 "kg"'),
    # The API takes percentages as fractions.
    ({"percent": {"min": 0.1, "max": 0.5}}, "a percentage from 10% to 50%"),
    ({"percent": {"max": 0.125}}, "a percentage of at most 12.5%"),
    ({"mentionedDate": {"to": "2025-12-31"}}, "a date before 2025-12-31"),
    (
        {"kinds": ["person"], "name": "Maya", "amount": {"min": 1000, "currency": "USD"}},
        'an entity (person) whose name starts with "Maya" and an amount of at least 1,000 USD',
    ),
])
def test_a_filter_is_described(raw: dict, expected: str) -> None:
    assert describe_entity_filter(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "x", {}, {"kinds": ["bogus"]}, {"name": "  "}, {"amount": {"min": 5, "max": 1}}, {"entityIds": "abc"},
])
def test_an_invalid_or_empty_filter_is_not_described(raw: object) -> None:
    assert describe_entity_filter(raw) is None


def test_free_text_is_one_line_quoted_and_capped() -> None:
    described = describe_entity_filter({"name": 'a"b\nc ' + "x" * 200})
    assert described is not None and "\n" not in described
    assert '"a\\"b c ' in described and described.endswith('…"')


@pytest.mark.parametrize("epoch_ms", [9007199254740991, 1e20])
def test_a_date_out_of_the_calendar_still_describes(epoch_ms: float) -> None:
    """The gateway accepts any integer; describing it must never fail the turn."""
    described = describe_entity_filter({"mentionedDate": {"from": epoch_ms}})
    assert described is not None and described.startswith("a date on or after ")
