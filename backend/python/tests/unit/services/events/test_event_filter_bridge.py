"""Trigger filters and normalized payloads have to meet in the middle.

Subscriptions are written as `on_event("slack.message.posted", channel="C1")`
and stored as a flat dict, while verifiers normalize provider payloads into
objects (`channel: {id, name}`). `fire_event` used to compare the two with flat
top-level equality, so the documented form matched nothing and event triggers
were effectively dead.
"""
from __future__ import annotations

import pytest

from app.services.events.catalog.registry import get_catalog
from app.services.events.domain.matching import (
    evaluate_filter,
    predicates_from_filter,
)

SLACK_PAYLOAD = {
    "channel": {"id": "C123", "name": "general"},
    "user": {"id": "U1", "name": "alice"},
    "text": "deploy finished",
}


def _matches(event_filter: dict) -> bool:
    return evaluate_filter(predicates_from_filter(event_filter), SLACK_PAYLOAD)


class TestFlatFilterBridge:
    def test_event_type_is_not_treated_as_a_payload_field(self) -> None:
        # It selects the trigger; the payload never carries it.
        assert predicates_from_filter({"event_type": "slack.message.posted"}) == []

    def test_naming_an_entity_matches_its_normalized_object(self) -> None:
        assert _matches({"event_type": "slack.message.posted", "channel": "C123"})

    def test_a_dot_path_matches_the_leaf(self) -> None:
        assert _matches({"event_type": "slack.message.posted", "channel.id": "C123"})

    def test_a_different_entity_does_not_match(self) -> None:
        assert not _matches({"event_type": "slack.message.posted", "channel": "C999"})

    def test_all_predicates_must_hold(self) -> None:
        assert _matches({"channel": "C123", "user": "U1"})
        assert not _matches({"channel": "C123", "user": "U-other"})

    def test_an_explicit_operator_survives_the_bridge(self) -> None:
        assert _matches({"text": {"op": "contains", "value": "deploy"}})
        assert not _matches({"text": {"op": "contains", "value": "rollback"}})

    def test_a_field_absent_from_the_payload_does_not_match(self) -> None:
        assert not _matches({"thread_ts": "1700000000.1"})


class TestCatalogValidation:
    def test_builtin_descriptors_are_loaded(self) -> None:
        # Nothing imported `catalog/*.py`, so the registry was empty and every
        # event type validated as unknown.
        assert get_catalog().get("slack.message.posted") is not None

    @pytest.mark.parametrize("field", ["channel", "channel.id"])
    def test_both_filter_spellings_validate(self, field: str) -> None:
        errors = get_catalog().validate_filter(
            "slack.message.posted", [{"field": field, "op": "eq", "value": "C1"}],
        )
        assert errors == []

    def test_an_unfilterable_field_is_rejected(self) -> None:
        errors = get_catalog().validate_filter(
            "slack.message.posted", [{"field": "nonsense", "op": "eq", "value": "x"}],
        )
        assert [e.code for e in errors] == ["UNKNOWN_FILTER_FIELD"]

    def test_a_typod_event_type_is_rejected(self) -> None:
        errors = get_catalog().validate_filter("slack.mesage.postd", [])
        assert [e.code for e in errors] == ["UNKNOWN_EVENT_TYPE"]
