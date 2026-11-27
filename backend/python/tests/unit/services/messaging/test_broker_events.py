"""``message.published`` and ``message.consumed``: what the broker wrappers announce, and what they never carry."""

import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import platform_events
from app.platform_events import catalog
from app.services.messaging import broker_events, message_log
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.platform_event_capture import event_calls

SECRET = "SEEDED-payload-text"
PUBLISHED = {"broker", "topic", "message_type", "outcome", "duration_seconds"}


@pytest.fixture(autouse=True)
def _fresh_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broker_events, "_types", set())


def _message(event_type: object = "newRecord") -> dict:
    return {"eventType": event_type, "payload": {"recordId": "rec-1", "text": SECRET}}


def _facts(mock: MagicMock) -> list[dict]:
    return [call.kwargs for call in mock.call_args_list]


class TestMessageType:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("newRecord", "newRecord"), ("connector.sync-started:v2", "connector.sync-started:v2"),
            (None, "unknown"), ("", "unknown"), (7, "unknown"),
            ("has space", "other"), ("x" * 65, "other"), ("9starts-with-digit", "other"), ("a@b.co", "other"),
        ],
    )
    def test_a_short_name_passes_and_anything_else_is_unknown_or_other(self, value: object, expected: str) -> None:
        assert broker_events.message_type(value) == expected

    def test_distinct_types_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(broker_events, "MAX_MESSAGE_TYPES", 2)

        assert [broker_events.message_type(v) for v in ("a", "b", "c", "a", "c")] == ["a", "b", "other", "a", "other"]

    def test_lag_needs_a_real_timestamp(self) -> None:
        now = time.time() * 1000
        assert 1000 <= broker_events.lag_ms(now - 1000) < 30_000
        assert broker_events.lag_ms(now + 60_000) == 0  # a broker clock ahead of ours is no negative lag
        assert [broker_events.lag_ms(v) for v in (None, 0, -1, "soon", True, MagicMock())] == [None] * 6
        assert broker_events.stream_entry_ms("1700000000123-4") == 1700000000123
        assert broker_events.stream_entry_ms(b"1700000000123-0") == 1700000000123
        assert broker_events.stream_entry_ms("abc") is None


class TestPublished:
    def _kafka(self, producer: MagicMock) -> KafkaMessagingProducer:
        p = KafkaMessagingProducer(MagicMock(), MagicMock())
        p.producer = producer
        return p

    def _redis(self, redis: MagicMock) -> RedisStreamsProducer:
        config = RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"])
        p = RedisStreamsProducer(logging.getLogger("test.broker_events"), config)
        p.redis = redis
        return p

    @pytest.mark.asyncio
    async def test_kafka_send_announces_success_without_payload_or_key(self) -> None:
        producer = MagicMock()
        producer.send_and_wait = AsyncMock(return_value=MagicMock())
        with event_calls("message.published") as published:
            await self._kafka(producer)._send_message("record-events", _message(), "key-SEEDED")

        (facts,) = _facts(published)
        assert set(facts) == PUBLISHED
        assert (facts["broker"], facts["topic"], facts["message_type"], facts["outcome"]) == ("kafka", "record-events", "newRecord", "success")
        assert facts["duration_seconds"] >= 0
        assert SECRET not in repr(facts) and "key-SEEDED" not in repr(facts) and "rec-1" not in repr(facts)

    @pytest.mark.asyncio
    async def test_kafka_send_failure_is_announced_and_still_raised(self) -> None:
        producer = MagicMock()
        producer.send_and_wait = AsyncMock(side_effect=RuntimeError("broker down"))
        with event_calls("message.published") as published, pytest.raises(RuntimeError):
            await self._kafka(producer)._send_message("record-events", _message(), None)

        assert [f["outcome"] for f in _facts(published)] == ["failure"]

    @pytest.mark.asyncio
    async def test_kafka_batch_announces_each_message_with_its_own_outcome(self) -> None:
        import asyncio

        async def send(topic: str, key: bytes | None = None, value: bytes | None = None) -> asyncio.Future:
            future = asyncio.get_running_loop().create_future()
            if b"failing" in value:
                future.set_exception(RuntimeError("not delivered"))
            else:
                future.set_result(MagicMock())
            return future

        producer = MagicMock()
        producer.send = send
        with event_calls("message.published") as published:
            acked = await self._kafka(producer)._send_messages(
                "record-events", [(None, _message("newRecord")), ("k", _message("failing")), (None, _message("deleteRecord"))]
            )

        assert acked == [True, False, True]
        assert [(f["message_type"], f["outcome"]) for f in _facts(published)] == [
            ("newRecord", "success"), ("failing", "failure"), ("deleteRecord", "success"),
        ]

    @pytest.mark.asyncio
    async def test_redis_send_announces_success_and_failure(self) -> None:
        redis = MagicMock()
        redis.xadd = AsyncMock(side_effect=["1-0", RuntimeError("down")])
        producer = self._redis(redis)
        with event_calls("message.published") as published:
            await producer._send_message("sync-events", _message("connector.sync"), "key-SEEDED")
            with pytest.raises(RuntimeError):
                await producer._send_message("sync-events", _message("connector.sync"), None)

        facts = _facts(published)
        assert [(f["broker"], f["topic"], f["message_type"], f["outcome"]) for f in facts] == [
            ("redis", "sync-events", "connector.sync", "success"), ("redis", "sync-events", "connector.sync", "failure"),
        ]
        assert all(set(f) == PUBLISHED for f in facts)
        assert SECRET not in repr(facts) and "key-SEEDED" not in repr(facts)

    @pytest.mark.asyncio
    async def test_redis_batch_announces_per_message_and_a_lost_pipeline_as_all_failed(self) -> None:
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(side_effect=[["1-0", RuntimeError("no")], RuntimeError("connection lost")])
        redis = MagicMock()
        redis.pipeline = MagicMock(return_value=pipeline)
        producer = self._redis(redis)
        batch = [(None, _message("a")), ("k", _message("b"))]
        with event_calls("message.published") as published:
            assert await producer._send_messages("t", batch) == [True, False]
            assert await producer._send_messages("t", batch) == [False, False]

        assert [(f["message_type"], f["outcome"]) for f in _facts(published)] == [
            ("a", "success"), ("b", "failure"), ("a", "failure"), ("b", "failure"),
        ]

    @pytest.mark.asyncio
    async def test_nothing_is_built_when_nobody_listens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        listeners = platform_events._listeners
        saved = {name: listeners.pop(name) for name in ("message.published", platform_events.WILDCARD) if name in listeners}
        typed = MagicMock(side_effect=broker_events.type_of)
        monkeypatch.setattr(broker_events, "type_of", typed)
        producer = MagicMock()
        producer.send_and_wait = AsyncMock(return_value=MagicMock())
        try:
            await self._kafka(producer)._send_message("record-events", _message(), None)
        finally:
            listeners.update(saved)

        typed.assert_not_called()


class TestConsumed:
    log = logging.getLogger("test.broker_events")

    def test_a_delivery_with_a_broker_is_announced_once_when_it_ends(self) -> None:
        with event_calls("message.consumed") as consumed:
            with message_log.delivery(
                self.log, topic="record-events", message_id="m1", broker="kafka", published_at_ms=time.time() * 1000 - 2000
            ):
                message_log.bind_opened(None, _message("newRecord"))
                message_log.end("ack")
                message_log.end("retry")

        (facts,) = _facts(consumed)
        assert set(facts) == {"broker", "topic", "message_type", "outcome", "duration_seconds", "lag_ms"}
        assert (facts["broker"], facts["topic"], facts["message_type"], facts["outcome"]) == ("kafka", "record-events", "newRecord", "success")
        assert 2000 <= facts["lag_ms"] < 30_000
        assert SECRET not in repr(facts) and "m1" not in repr(facts) and "rec-1" not in repr(facts)

    @pytest.mark.parametrize(
        ("end", "expected"),
        [
            (lambda: message_log.end("ack"), "success"),
            (lambda: (message_log.note(outcome="skipped", reason="duplicate"), message_log.end("ack")), "skipped"),
            (lambda: message_log.end("retry"), "retry"),
            (lambda: message_log.end("dead_letter", reason="attempts_exhausted"), "dead_letter"),
            (lambda: None, "retry"),  # a delivery nobody ended is read again
        ],
    )
    def test_the_outcome_is_how_the_delivery_was_settled(self, end, expected: str) -> None:
        with event_calls("message.consumed", "outcome", "lag_ms") as consumed:
            with message_log.delivery(self.log, topic="t", message_id="m", broker="redis"):
                end()

        consumed.assert_called_once_with(expected, None)

    def test_a_delivery_without_a_broker_is_not_announced(self) -> None:
        with event_calls("message.consumed") as consumed:
            with message_log.delivery(self.log, topic="t", message_id="m"):
                message_log.end("ack")

        consumed.assert_not_called()


def test_the_events_are_catalogued_without_payload_or_key_fields() -> None:
    for name in ("message.published", "message.consumed"):
        fields = catalog.EVENTS[name].fields
        assert not {"payload", "key", "message", "message_id", "record_id", "value"} & set(fields), name
