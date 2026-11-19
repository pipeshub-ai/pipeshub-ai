"""The ``record-title-events`` message and the topic it travels on."""
from __future__ import annotations

import pytest

from app.services.messaging.config import REQUIRED_TOPICS, Topic
from app.services.messaging.record_titles import (
    DELETE,
    RECORD_TITLE_MAX_CHARS,
    UPSERT,
    build_record_title_message,
    clean_record_title,
)


class TestCleanTitle:
    @pytest.mark.parametrize("name", [None, "", "   ", "\n\t", 5, ["a"], b"bytes"])
    def test_blank_or_non_text_is_none(self, name: object) -> None:
        assert clean_record_title(name) is None

    def test_a_name_is_kept_as_it_is(self) -> None:
        assert clean_record_title("  Q3 plan ") == "  Q3 plan "

    def test_a_long_name_is_cut(self) -> None:
        assert len(clean_record_title("x" * 10_000_000) or "") == RECORD_TITLE_MAX_CHARS


class TestMessage:
    def test_shape(self) -> None:
        message = build_record_title_message(
            org_id="o", record_id="r", record_name="n", connector_id="c", record_group_id="g",
            event_type=UPSERT, timestamp=7,
        )
        assert message == {
            "eventType": "recordTitle",
            "timestamp": 7,
            "payload": {
                "orgId": "o", "recordId": "r", "recordName": "n", "connectorId": "c",
                "recordGroupId": "g", "eventType": "upsert", "timestamp": 7,
            },
        }

    def test_a_delete_names_no_record(self) -> None:
        payload = build_record_title_message(
            org_id="o", record_id="r", record_name=None, connector_id=None, record_group_id=None,
            event_type=DELETE, timestamp=1,
        )["payload"]
        assert payload["eventType"] == "delete" and payload["recordName"] is None


def test_the_topic_is_required_and_has_no_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.config import MessageBrokerType
    from app.services.messaging.messaging_factory import lane_topics_for

    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "8")
    assert Topic.RECORD_TITLE_EVENTS.value == "record-title-events"
    assert "record-title-events" in REQUIRED_TOPICS
    assert lane_topics_for("record-title-events", MessageBrokerType.REDIS) == ["record-title-events"]
    assert lane_topics_for("record-title-events", MessageBrokerType.KAFKA) == ["record-title-events"]
