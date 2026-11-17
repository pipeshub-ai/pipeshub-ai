"""F19: a message that is valid JSON but not a StreamMessage must be dead-lettered once, not re-read forever.

On main, ``IndexingKafkaConsumer.__parse_message`` lets pydantic's ValidationError (no ``eventType``, ``payload`` not an
object) and TypeError (JSON that is not an object) escape. The read paths then treat it as transient and seek back to the
same offset: under fair scheduling the partition never moves again; under FIFO the messages behind it are re-processed on
every poll. The Redis twin (``redis_streams/indexing_consumer.py`` ``_parse_message``) already returns None for both.

Runs the real consume loop against the in-memory broker in ``tests.support.fake_kafka``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from aiokafka.structs import ConsumerRecord
from pydantic import ValidationError

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as consumer_module
from app.services.messaging.kafka.consumer import remembered as remembered_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_kafka import FakeAIOKafkaConsumer, FakeKafkaBroker
from tests.support.undecodable_json import DEEP, UNDECODABLE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from aiokafka import TopicPartition

TOPIC = "record-events"
GROUP = "records_consumer_group"
# A person's name in a payload: must never reach a log line. Short, because pydantic cuts the middle of a long input_value.
SEEDED = "Qa Fakeperson"

# Each id names the exception main lets escape. Values that are str are sent as they are.
POISON = {
    "validation_no_event_type": {"payload": {"n": SEEDED}},
    "validation_payload_is_a_list": {"eventType": "newRecord", "payload": [SEEDED]},
    "validation_event_type_not_a_string": {"eventType": 5, "payload": {}},
    "validation_no_payload": {"eventType": "newRecord"},
    "type_json_list": ["not", "an", "object"],
    "type_json_number": 42,
    "type_double_encoded_number": json.dumps("42"),
    **UNDECODABLE,  # F34
    "nesting_too_deep_offloaded_to_a_thread": "[" * 150_000 + "]" * 150_000,  # 300 KB: parsed off the loop
}
GOOD = {
    "eventType": "newRecord",
    "timestamp": 1,
    "payload": {"recordId": "rec-good", "orgId": "org-1", "connectorId": "conn-1",
                "extension": "txt", "mimeType": "text/plain"},
}


def _raw(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _record(value: object, offset: int = 0) -> ConsumerRecord:
    raw = _raw(value)
    return ConsumerRecord(topic=TOPIC, partition=0, offset=offset, timestamp=0, timestamp_type=0, key=None,
                          value=raw.encode(), checksum=None, serialized_key_size=0,
                          serialized_value_size=len(raw), headers=())


def _consumer(fair: bool, sink: object = None) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("test.f19"),
        KafkaConsumerConfig(topics=[TOPIC], client_id="f19", group_id=GROUP, auto_offset_reset="earliest",
                            enable_auto_commit=False, bootstrap_servers=["kafka:9092"]),
        fair_scheduler_config=FairSchedulerConfig(enabled=fair, key_fields=("orgId", "connectorId")),
        disposition_sink=sink,
    )


async def _parse(consumer: IndexingKafkaConsumer, record: ConsumerRecord) -> StreamMessage | None:
    return await consumer._IndexingKafkaConsumer__parse_message(record)


class TestParseMessageTreatsSchemaFailuresAsPoison:
    @pytest.mark.parametrize("value", POISON.values(), ids=POISON.keys())
    async def test_returns_none(self, value: object) -> None:
        assert await _parse(_consumer(fair=True), _record(value)) is None

    async def test_logs_the_error_type_not_the_payload(self, caplog: pytest.LogCaptureFixture) -> None:
        record = _record(POISON["validation_no_event_type"])
        with pytest.raises(ValidationError) as control:
            StreamMessage(**json.loads(record.value))
        assert SEEDED in str(control.value)

        with caplog.at_level(logging.DEBUG, logger="test.f19"):
            await _parse(_consumer(fair=True), record)

        lines = [r.getMessage() for r in caplog.records]
        assert any("ValidationError" in line and "record-events-0-0" in line for line in lines)
        assert not any(SEEDED in line for line in lines)

    @pytest.mark.parametrize("error", [
        ConnectionError("key store down"),
        ValueError("ENVELOPE_MAX_AGE_SECONDS must be a positive number"),  # F34: only a decode's ValueError is poison
        RecursionError("not from the decoder"),
    ], ids=["transient", "misconfigured", "recursion_outside_the_decoder"])
    async def test_a_failure_that_is_not_the_message_still_raises(self, error: Exception) -> None:
        # The fix must stay narrow: anything but the message itself failing inside open_stream_message is retried.
        with patch.object(consumer_module, "open_stream_message", AsyncMock(side_effect=error)), \
                pytest.raises(type(error)):
            await _parse(_consumer(fair=True), _record(GOOD))

    async def test_an_undecodable_message_logs_a_json_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="test.f19"):
            assert await _parse(_consumer(fair=True), _record(DEEP)) is None
        assert [r.getMessage() for r in caplog.records] == ["JSON parsing failed for message record-events-0-0"]
        assert caplog.records[0].error_type == "RecursionError"  # why valid-looking JSON failed: the decoder's cause

    async def test_a_good_message_still_parses(self) -> None:
        parsed = await _parse(_consumer(fair=True), _record(GOOD))
        assert parsed is not None
        assert parsed.payload["recordId"] == "rec-good"


class Handler:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        self.seen.append(str(message.payload["recordId"]))
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData())
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)


# Main's seek-back loop never yields while the broker has data, which would starve the test's own coroutine. After
# POLL_CAP polls the fake returns nothing and yields, so main fails on the assertions, not on pytest-timeout. The fix needs
# a handful of polls.
POLL_CAP = 300


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    broker = FakeKafkaBroker()
    broker.polls = 0
    real_getmany = FakeAIOKafkaConsumer.getmany

    async def capped_getmany(self, *partitions, **kwargs) -> dict[TopicPartition, list[ConsumerRecord]]:
        broker.polls += 1
        if broker.polls > POLL_CAP:
            await asyncio.sleep(0.005)
            return {}
        return await real_getmany(self, *partitions, **kwargs)

    monkeypatch.setattr(FakeAIOKafkaConsumer, "getmany", capped_getmany)
    factory = broker.consumer_factory()
    with patch.object(consumer_module, "AIOKafkaConsumer", factory), \
            patch.object(remembered_module, "AIOKafkaConsumer", factory):
        yield broker


async def _until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


@pytest.mark.parametrize("fair", [True, False], ids=["fair_scheduling", "fifo"])
@pytest.mark.parametrize("poison", ["validation_no_event_type", "type_json_list", "nesting_too_deep",
                                    "integer_too_long"])
async def test_poison_ahead_of_a_good_message_is_dead_lettered_once_and_the_partition_moves_on(
    broker: FakeKafkaBroker, fair: bool, poison: str
) -> None:
    broker.produce(TOPIC, _raw(POISON[poison]))  # offset 0
    broker.produce(TOPIC, GOOD)  # offset 1
    sink = AsyncMock()
    handler = Handler()
    consumer = _consumer(fair=fair, sink=sink)
    await consumer.start(handler)
    try:
        reached = await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2 and bool(handler.seen))
        settled_at = broker.polls
        await _until(lambda: broker.polls >= settled_at + 20)  # keep polling: a seek-back would re-deliver here
    finally:
        await consumer.stop()

    assert reached, f"committed={broker.committed_offset(GROUP, TOPIC)} handled={len(handler.seen)} polls={broker.polls}"
    assert handler.seen == ["rec-good"], f"good message handled {len(handler.seen)} times"
    assert broker.committed_offset(GROUP, TOPIC) == 2
    sink.on_message_abandoned.assert_awaited_once()
    args, kwargs = sink.on_message_abandoned.await_args
    assert args == (None,)
    assert kwargs["reason"] == "terminal error"
