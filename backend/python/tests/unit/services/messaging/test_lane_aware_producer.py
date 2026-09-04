"""Unit tests for the lane-routing producer decorator.

The decorator is what makes laning reach all twelve existing publish sites
without editing any of them, so these tests exercise the message *shapes*
those sites actually use rather than a single idealised one.
"""
from __future__ import annotations

import logging

import pytest

from app.services.messaging.lanes.hash_router import (
    KafkaLaneRouter,
    RedisLaneRouter,
    stable_lane,
)
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY, LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer

_TOPIC = "record-events"
_LANES = 8


class _RecordingProducer:
    """Stands in for the real producer; records exactly what it was asked to
    publish so routing can be asserted without a broker."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict, str | None]] = []
        self.events: list[tuple[str, str, dict, str | None]] = []
        self.batches: list[tuple[str, list]] = []
        self.lifecycle: list[str] = []
        self.batch_results: dict[str, list[bool]] = {}

    async def initialize(self) -> None:
        self.lifecycle.append("initialize")

    async def cleanup(self) -> None:
        self.lifecycle.append("cleanup")

    async def start(self) -> None:
        self.lifecycle.append("start")

    async def stop(self) -> None:
        self.lifecycle.append("stop")

    async def send_message(self, topic, message, key=None) -> bool:
        self.messages.append((topic, message, key))
        return True

    async def send_event(self, topic, event_type, payload, key=None) -> bool:
        self.events.append((topic, event_type, payload, key))
        return True

    async def send_messages(self, topic, messages) -> list[bool]:
        self.batches.append((topic, list(messages)))
        if topic in self.batch_results:
            return self.batch_results[topic]
        return [True] * len(messages)


@pytest.fixture
def inner():
    return _RecordingProducer()


def _wrap(inner, router=None, **config_overrides) -> LaneAwareProducer:
    config = LaneConfig(lane_count=_LANES, **config_overrides)
    return LaneAwareProducer(
        logging.getLogger("test_lane_aware_producer"),
        inner,
        router or RedisLaneRouter(config.lane_count),
        config,
    )


def _expected_stream(connector_id: str) -> str:
    return f"{_TOPIC}.{stable_lane(connector_id, _LANES)}"


class TestSendEvent:
    async def test_routes_by_connector_id_from_the_payload(self, inner):
        producer = _wrap(inner)
        await producer.send_event(
            topic=_TOPIC,
            event_type="newRecord",
            payload={"recordId": "r1", "orgId": "o1", "connectorId": "conn-1"},
        )
        topic, event_type, _payload, key = inner.events[0]
        assert topic == _expected_stream("conn-1")
        assert event_type == "newRecord"
        assert key == "conn-1"

    async def test_payload_is_forwarded_unmodified(self, inner):
        producer = _wrap(inner)
        payload = {"recordId": "r1", "connectorId": "conn-1"}
        await producer.send_event(
            topic=_TOPIC, event_type="newRecord", payload=payload
        )
        assert inner.events[0][2] == payload

    async def test_caller_supplied_key_is_replaced_by_the_lane_key(self, inner):
        """Call sites pass ``key=record.id`` today. Lane placement has to win,
        or records for one connector scatter across lanes."""
        producer = _wrap(inner)
        await producer.send_event(
            topic=_TOPIC,
            event_type="newRecord",
            payload={"connectorId": "conn-1"},
            key="rec-123",
        )
        assert inner.events[0][3] == "conn-1"


class TestSendMessage:
    async def test_routes_from_a_standard_envelope(self, inner):
        producer = _wrap(inner)
        await producer.send_message(
            _TOPIC,
            {"eventType": "newRecord", "payload": {"connectorId": "conn-1"}},
        )
        assert inner.messages[0][0] == _expected_stream("conn-1")

    async def test_routes_from_a_flat_dict(self, inner):
        """Two call sites hand a flat dict straight to send_message."""
        producer = _wrap(inner)
        await producer.send_message(_TOPIC, {"connectorId": "conn-1"})
        assert inner.messages[0][0] == _expected_stream("conn-1")


class TestMissingLaneKey:
    async def test_unkeyed_events_share_the_default_lane(self, inner):
        """``bulkDeleteRecords`` publishes with no key at all today. It must
        land somewhere deterministic, not be scattered."""
        producer = _wrap(inner)
        for _ in range(5):
            await producer.send_event(
                topic=_TOPIC, event_type="bulkDeleteRecords", payload={"orgId": "o1"}
            )
        destinations = {topic for topic, _t, _p, _k in inner.events}
        assert destinations == {_expected_stream(DEFAULT_LANE_KEY)}

    async def test_empty_string_counts_as_missing(self, inner):
        producer = _wrap(inner)
        await producer.send_event(
            topic=_TOPIC, event_type="newRecord", payload={"connectorId": ""}
        )
        assert inner.events[0][0] == _expected_stream(DEFAULT_LANE_KEY)

    async def test_missing_key_is_warned_once_not_per_message(self, inner, caplog):
        producer = _wrap(inner)
        with caplog.at_level(logging.WARNING):
            for _ in range(10):
                await producer.send_event(
                    topic=_TOPIC, event_type="newRecord", payload={}
                )
        assert sum("default lane" in r.message for r in caplog.records) == 1


class TestNonLanedTopics:
    async def test_other_topics_pass_through_untouched(self, inner):
        producer = _wrap(inner)
        await producer.send_event(
            topic="entity-events",
            event_type="userAdded",
            payload={"connectorId": "conn-1"},
            key="user-1",
        )
        assert inner.events[0][0] == "entity-events"
        assert inner.events[0][3] == "user-1"

    async def test_batches_on_other_topics_pass_through(self, inner):
        producer = _wrap(inner)
        await producer.send_messages(
            "entity-events", [("k1", {"connectorId": "conn-1"})]
        )
        assert inner.batches == [("entity-events", [("k1", {"connectorId": "conn-1"})])]


class TestSendMessagesBatching:
    async def test_one_delegated_call_per_lane_not_per_message(self, inner):
        """Sending one at a time would undo the producer batching that
        KafkaProducer.send_messages exists for."""
        producer = _wrap(inner)
        messages = [
            (None, {"payload": {"connectorId": "conn-1"}}) for _ in range(20)
        ]
        await producer.send_messages(_TOPIC, messages)

        assert len(inner.batches) == 1
        topic, batched = inner.batches[0]
        assert topic == _expected_stream("conn-1")
        assert len(batched) == 20

    async def test_a_mixed_batch_is_split_by_lane(self, inner):
        producer = _wrap(inner)
        messages = [
            (None, {"payload": {"connectorId": f"conn-{i}"}}) for i in range(20)
        ]
        await producer.send_messages(_TOPIC, messages)

        expected_lanes = {
            _expected_stream(f"conn-{i}") for i in range(20)
        }
        assert {topic for topic, _b in inner.batches} == expected_lanes
        assert sum(len(b) for _t, b in inner.batches) == 20

    async def test_results_come_back_in_the_callers_order(self, inner):
        """Callers use the result list positionally to record which records
        were accepted; regrouping by lane must not permute it."""
        producer = _wrap(inner)
        messages = [
            (None, {"payload": {"connectorId": "conn-a"}}),
            (None, {"payload": {"connectorId": "conn-b"}}),
            (None, {"payload": {"connectorId": "conn-a"}}),
        ]
        lane_a = _expected_stream("conn-a")
        lane_b = _expected_stream("conn-b")
        assert lane_a != lane_b, "fixture requires two distinct lanes"
        inner.batch_results = {lane_a: [True, False], lane_b: [False]}

        results = await producer.send_messages(_TOPIC, messages)

        assert results == [True, False, False]

    async def test_empty_batch(self, inner):
        assert await _wrap(inner).send_messages(_TOPIC, []) == []


class TestKafkaRouting:
    async def test_topic_is_unchanged_and_key_carries_the_lane(self, inner):
        producer = _wrap(inner, router=KafkaLaneRouter(_LANES))
        await producer.send_event(
            topic=_TOPIC, event_type="newRecord", payload={"connectorId": "conn-1"}
        )
        topic, _event_type, _payload, key = inner.events[0]
        assert topic == _TOPIC
        assert key == "conn-1"

    async def test_a_batch_stays_one_call_because_the_topic_is_one_topic(
        self, inner
    ):
        producer = _wrap(inner, router=KafkaLaneRouter(_LANES))
        messages = [
            (None, {"payload": {"connectorId": f"conn-{i}"}}) for i in range(20)
        ]
        await producer.send_messages(_TOPIC, messages)

        assert len(inner.batches) == 1
        assert inner.batches[0][0] == _TOPIC
        assert [key for key, _m in inner.batches[0][1]] == [
            f"conn-{i}" for i in range(20)
        ]


class TestLifecycleDelegation:
    async def test_lifecycle_calls_reach_the_inner_producer(self, inner):
        producer = _wrap(inner)
        await producer.initialize()
        await producer.start()
        await producer.stop()
        await producer.cleanup()
        assert inner.lifecycle == ["initialize", "start", "stop", "cleanup"]


class TestConfigurableLaneKey:
    async def test_lane_key_field_can_be_changed(self, inner):
        producer = _wrap(inner, lane_key_field="orgId")
        await producer.send_event(
            topic=_TOPIC,
            event_type="newRecord",
            payload={"orgId": "org-9", "connectorId": "conn-1"},
        )
        assert inner.events[0][0] == _expected_stream("org-9")
