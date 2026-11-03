"""How connectors publish record events (``KafkaService``), end to end.

Every connector sync hands its new and changed records to ``KafkaService``,
which puts one event per record on the record-events stream for the
indexing service. These tests publish through the real Redis Streams
producer into an in-memory Redis and read the stream back, so they check
what the indexing service will actually receive: every record once, in
order, keyed by record id, with the result for each record reported
truthfully when part of a batch fails.
"""
from __future__ import annotations

import json
from logging import getLogger
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.connectors.services.kafka_service import KafkaService
from app.services.messaging.config import RedisStreamsConfig, Topic
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from pydantic import JsonValue


def _event(record_id: str | None, org_id: str = "org-a") -> dict:
    payload: dict = {"orgId": org_id}
    if record_id is not None:
        payload["recordId"] = record_id
    return {"eventType": "newRecord", "timestamp": 123, "payload": payload}


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=False)


@pytest.fixture
def service(provider: FakeRedisConnectionProvider) -> KafkaService:
    producer = RedisStreamsProducer(getLogger("test"), RedisStreamsConfig(), provider=provider)
    service = KafkaService(MagicMock(), getLogger("test"))
    service.set_producer(producer)
    return service


async def _read(provider: FakeRedisConnectionProvider, stream: str) -> list[dict]:
    entries = await provider.get_client().xrange(stream)
    return [{"key": fields.get("key"), **json.loads(fields["value"])} for _id, fields in entries]


class TestRecordEvents:
    async def test_a_batch_lands_once_per_record_in_order_keyed_by_record_id(self, service, provider) -> None:
        events = [_event("r1"), _event("r2"), _event("r3")]
        assert await service.publish_events(Topic.RECORD_EVENTS.value, events) == [True, True, True]

        landed = await _read(provider, Topic.RECORD_EVENTS.value)
        assert [e["payload"]["recordId"] for e in landed] == ["r1", "r2", "r3"]
        # The key is what keeps one record's events in order on one partition.
        assert [e["key"] for e in landed] == ["r1", "r2", "r3"]

    async def test_an_event_without_a_record_id_is_keyed_by_its_timestamp(self, service, provider) -> None:
        await service.publish_event(Topic.RECORD_EVENTS.value, _event(None))
        landed = await _read(provider, Topic.RECORD_EVENTS.value)
        assert [e["key"] for e in landed] == ["123"]

    async def test_an_empty_batch_publishes_nothing_and_starts_nothing(self, provider) -> None:
        producer = MagicMock(spec=IMessagingProducer)
        service = KafkaService(MagicMock(), getLogger("test"), producer=producer)
        assert await service.publish_events(Topic.RECORD_EVENTS.value, []) == []
        producer.start.assert_not_called()

    async def test_the_org_id_travels_with_each_event(self, service, provider) -> None:
        await service.publish_events(
            Topic.RECORD_EVENTS.value, [_event("a1", org_id="org-a"), _event("b1", org_id="org-b")]
        )
        landed = await _read(provider, Topic.RECORD_EVENTS.value)
        assert {e["payload"]["recordId"]: e["payload"]["orgId"] for e in landed} == {"a1": "org-a", "b1": "org-b"}

    async def test_send_event_to_kafka_builds_a_record_event_the_indexer_understands(
        self, service, provider
    ) -> None:
        await service.send_event_to_kafka({
            "orgId": "org-a", "recordId": "r9", "recordName": "plan.pdf",
            "extension": "pdf", "mimeType": "application/pdf",
        })
        [landed] = await _read(provider, Topic.RECORD_EVENTS.value)
        assert landed["key"] == "r9"
        assert landed["eventType"] == "newRecord"
        assert landed["payload"]["recordId"] == "r9"
        assert landed["payload"]["orgId"] == "org-a"


class _FlakyProducer(IMessagingProducer):
    """A producer without a batch override, whose second send fails."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, str | None]] = []
        self.started = 0

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        return None

    async def send_message(self, topic: str, message: dict[str, JsonValue], key: str | None = None) -> bool:
        if len(self.sent) == 1 and not getattr(self, "_failed", False):
            self._failed = True
            raise ConnectionError("broker unreachable")
        self.sent.append((topic, message, key))
        return True

    async def send_event(self, topic: str, event_type: str, payload: dict[str, JsonValue],
                         key: str | None = None) -> bool:
        return await self.send_message(topic, {"eventType": event_type, "payload": payload}, key)


class TestPartialBatchFailure:
    async def test_each_record_reports_its_own_outcome_when_one_send_fails(self) -> None:
        producer = _FlakyProducer()
        service = KafkaService(MagicMock(), getLogger("test"), producer=producer)
        results = await service.publish_events(
            Topic.RECORD_EVENTS.value, [_event("r1"), _event("r2"), _event("r3")]
        )
        assert results == [True, False, True]
        assert [m["payload"]["recordId"] for _t, m, _k in producer.sent] == ["r1", "r3"]

    async def test_the_producer_is_started_once_across_publishes(self) -> None:
        producer = _FlakyProducer()
        producer._failed = True
        service = KafkaService(MagicMock(), getLogger("test"), producer=producer)
        await service.publish_events(Topic.RECORD_EVENTS.value, [_event("r1")])
        await service.publish_event(Topic.RECORD_EVENTS.value, _event("r2"))
        assert producer.started == 1


class TestNotifications:
    async def test_a_notification_goes_to_the_notification_stream(self, service, provider) -> None:
        assert await service.publish_notification({"type": "syncFailed", "orgId": "org-a"}) is True
        [landed] = await _read(provider, Topic.NOTIFICATION.value)
        assert landed["type"] == "syncFailed"
        assert landed["key"].startswith("syncFailed-")

    async def test_a_failed_notification_raises_so_the_caller_knows(self, provider) -> None:
        producer = MagicMock(spec=IMessagingProducer)
        producer.send_message.side_effect = ConnectionError("broker unreachable")
        service = KafkaService(MagicMock(), getLogger("test"), producer=producer)
        with pytest.raises(ConnectionError):
            await service.publish_notification({"type": "syncFailed"})

    async def test_publishing_without_a_producer_says_how_to_fix_it(self) -> None:
        service = KafkaService(MagicMock(), getLogger("test"))
        with pytest.raises(RuntimeError, match="set_producer"):
            await service.publish_events(Topic.RECORD_EVENTS.value, [_event("r1")])
