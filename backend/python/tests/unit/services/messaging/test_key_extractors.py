"""Unit tests for hierarchical fairness-key extraction."""
from __future__ import annotations

import pytest

from app.services.messaging.config import StreamMessage
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor


def _message(payload: dict) -> StreamMessage:
    return StreamMessage(eventType="newRecord", payload=payload)


class TestDefaultOrgAndConnector:
    def test_extracts_both_levels_outermost_first(self):
        extractor = CompositeKeyExtractor()
        key = extractor.extract(
            _message({"orgId": "org-123", "connectorId": "conn-9"})
        )
        assert key == ("org-123", "conn-9")

    def test_default_fields_are_org_then_connector(self):
        assert CompositeKeyExtractor().fields == ("orgId", "connectorId")

    def test_two_users_in_one_org_get_distinct_keys(self):
        """The reason ``connectorId`` is in the default key at all: every
        user in an org shares its ``orgId``, so org alone cannot separate
        one user's sync from another's."""
        extractor = CompositeKeyExtractor()
        user_a = extractor.extract(
            _message({"orgId": "org-1", "connectorId": "drive-user-a"})
        )
        user_b = extractor.extract(
            _message({"orgId": "org-1", "connectorId": "drive-user-b"})
        )
        assert user_a != user_b
        assert user_a[0] == user_b[0]


class TestMissingFields:
    def test_missing_level_collapses_to_the_sentinel(self):
        extractor = CompositeKeyExtractor()
        assert extractor.extract(_message({"orgId": "org-1"})) == (
            "org-1",
            "__default__",
        )

    def test_empty_string_is_treated_as_missing(self):
        extractor = CompositeKeyExtractor()
        assert extractor.extract(
            _message({"orgId": "", "connectorId": "conn-1"})
        ) == ("__default__", "conn-1")

    def test_every_field_missing_still_yields_a_full_depth_key(self):
        """A malformed payload must not change the tree shape -- it shares
        one queue with other malformed payloads instead."""
        extractor = CompositeKeyExtractor()
        assert extractor.extract(_message({"recordId": "abc"})) == (
            "__default__",
            "__default__",
        )

    def test_custom_sentinel(self):
        extractor = CompositeKeyExtractor(fields=("orgId",), default="__none__")
        assert extractor.extract(_message({"recordId": "x"})) == ("__none__",)


class TestConfigurableFields:
    def test_single_field_gives_a_one_level_key(self):
        extractor = CompositeKeyExtractor(fields=("orgId",))
        assert extractor.extract(_message({"orgId": "org-1"})) == ("org-1",)

    def test_arbitrary_field_order(self):
        extractor = CompositeKeyExtractor(fields=("connectorId", "orgId"))
        assert extractor.extract(
            _message({"orgId": "org-1", "connectorId": "conn-9"})
        ) == ("conn-9", "org-1")

    def test_non_string_values_are_coerced(self):
        extractor = CompositeKeyExtractor(fields=("version",))
        assert extractor.extract(_message({"version": 3})) == ("3",)

    def test_empty_field_list_is_rejected(self):
        """An empty key would make every message share one queue, silently
        disabling fairness -- fail at construction instead."""
        with pytest.raises(ValueError):
            CompositeKeyExtractor(fields=())
