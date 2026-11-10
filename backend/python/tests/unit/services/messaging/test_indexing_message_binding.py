"""Indexing consumers: the message's root, org and ids are bound for a record's whole run, including
the failure, dead-letter and timing lines after the handler and the main-loop recovery path; parse
failures never log the value or a validation error's text."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import TopicPartition
from pydantic import ValidationError

from app.exceptions.non_retryable import NonRetryableError
from app.services.messaging.config import (
    PipelineEvent,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as indexing_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.utils.logger import log_record_fields
from app.utils.structured_logging import DefaultLoggingProvider

LOGGER = "test.p8.indexing_binding"
SEEDED = "Ada SEEDED Lovelace"
SHORT_SEED = "AdaSEED"  # pydantic cuts the middle of a long input; a short value is quoted whole
PAYLOAD = {"orgId": "org_a", "recordId": "rec-1", "connectorId": "conn-1"}


class _Tracker:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.counts = dict(counts or {})

    async def get_count(self, message_id: str) -> int:
        return self.counts.get(message_id, 0)

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        self.counts[message_id] = self.counts.get(message_id, 0) + 1
        return self.counts[message_id], self.counts[message_id] >= max_attempts

    async def clear(self, message_id: str) -> None:
        self.counts.pop(message_id, None)


@pytest.fixture(autouse=True)
def _capture(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    caplog.set_level(logging.DEBUG, logger=LOGGER)


def _value(event_type: str = "newRecord", payload: dict | None = None) -> str:
    return json.dumps({"eventType": event_type, "payload": payload or PAYLOAD, "requestId": "req-producer"})


def _failing_handler(exc: Exception) -> Callable[[object], AsyncGenerator[PipelineEvent, None]]:
    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        logging.getLogger(LOGGER).info("handler line")
        raise exc
        yield

    return handler


def _lines(caplog, *messages: str) -> dict[str, dict[str, object]]:
    found = {}
    for record in caplog.records:
        for text in messages:
            if record.name == LOGGER and record.getMessage().startswith(text):
                found[text] = log_record_fields(record) | {"request_id": record.request_id}
    assert set(found) == set(messages), sorted(r.getMessage() for r in caplog.records)
    return found


def _assert_bound(fields: dict[str, object], message_id: str, topic: str) -> None:
    assert {k: fields.get(k) for k in ("topic", "message_id", "event_type", "record_id", "org_id")} == {
        "topic": topic, "message_id": message_id, "event_type": "newRecord", "record_id": "rec-1", "org_id": "org_a",
    }, fields
    assert "req-producer" in str(fields["request_id"])


def _no_seed(caplog) -> None:
    for record in caplog.records:
        text = " ".join([record.getMessage(), str(log_record_fields(record)), record.exc_text or ""])
        assert SEEDED not in text and SHORT_SEED not in text, text


def _redis(retry_manager=None) -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["record-events"])
    c = IndexingRedisStreamsConsumer(logging.getLogger(LOGGER), config, retry_manager=retry_manager, producer=None)
    c._pending_message_is_owned = AsyncMock(return_value=True)
    c.parsing_semaphore = asyncio.Semaphore(1)
    c.indexing_semaphore = asyncio.Semaphore(1)
    c._ack_message = AsyncMock()
    c.redis = AsyncMock()
    return c


def _kafka_message(value: bytes, offset: int = 3) -> MagicMock:
    m = MagicMock()
    m.topic, m.partition, m.offset, m.value = "record-events", 0, offset, value
    return m


def _kafka() -> IndexingKafkaConsumer:
    config = KafkaConsumerConfig(
        topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["broker:9092"],
    )
    c = IndexingKafkaConsumer(logging.getLogger(LOGGER), config)
    c.parsing_semaphore = asyncio.Semaphore(1)
    c.indexing_semaphore = asyncio.Semaphore(1)
    c.consumer = MagicMock()
    c.consumer.commit = AsyncMock()
    return c


class TestRedisIndexing:
    async def test_lines_after_a_transient_failure_carry_the_message(self, caplog) -> None:
        c = _redis()
        c.message_handler = _failing_handler(ConnectionError("down"))
        assert await c._process_message_wrapper("record-events", "5-0", {"value": _value()}) is False
        lines = _lines(caplog, "handler line", "Error in process_message_wrapper", "Transient error for")
        for fields in lines.values():
            _assert_bound(fields, "5-0", "record-events")

    async def test_requeue_line_carries_the_message(self, caplog) -> None:
        c = _redis(retry_manager=_Tracker())
        c._requeue_message = AsyncMock()
        c.message_handler = _failing_handler(ConnectionError("down"))
        await c._process_message_wrapper("record-events", "5-0", {"value": _value()})
        _assert_bound(_lines(caplog, "Re-queued")["Re-queued"], "5-0", "record-events")

    async def test_dead_letter_line_after_a_terminal_failure_carries_the_message(self, caplog) -> None:
        c = _redis()
        c.message_handler = _failing_handler(NonRetryableError("bad"))
        await c._process_message_wrapper("record-events", "5-0", {"value": _value()})
        _assert_bound(_lines(caplog, "Dead-lettered")["Dead-lettered"], "5-0", "record-events")

    async def test_a_recovered_entry_out_of_attempts_is_dead_lettered_in_its_context(self, caplog) -> None:
        c = _redis(retry_manager=_Tracker({"7-0": 3}))
        handled = await c._IndexingRedisStreamsConsumer__recover_entry(
            "record-events", "7-0", {"value": _value()}, owned=False,
        )
        assert handled is False
        _assert_bound(_lines(caplog, "Dead-lettered")["Dead-lettered"], "7-0", "record-events")

    async def test_a_validation_error_text_is_not_logged(self, caplog) -> None:
        c = _redis()
        value = json.dumps({"payload": {"n": SHORT_SEED}})  # no eventType
        with pytest.raises(ValidationError) as control:
            StreamMessage(**json.loads(value))
        assert SHORT_SEED in str(control.value)
        assert await c._parse_message("8-0", {"value": value}, "record-events") is None
        failed = _lines(caplog, "Failed to parse message")["Failed to parse message"]
        assert (failed["message_id"], failed["topic"], failed["error_type"]) == ("8-0", "record-events", "ValidationError")
        _no_seed(caplog)


class TestKafkaIndexing:
    async def test_lines_after_a_terminal_failure_carry_the_message(self, caplog) -> None:
        c = _kafka()
        c.message_handler = _failing_handler(NonRetryableError("bad"))
        await c._IndexingKafkaConsumer__process_message_wrapper(_kafka_message(_value().encode()))
        lines = _lines(caplog, "handler line", "Error in process_message_wrapper")
        for fields in lines.values():
            _assert_bound(fields, "record-events-0-3", "record-events")

    @pytest.mark.parametrize("value", [
        f'{{"payload": {{"recordName": "{SEEDED}"'.encode(),
        b"\xff\xfe" + SEEDED.encode(),
    ])
    async def test_a_message_that_cannot_be_parsed_is_logged_without_its_value(self, caplog, value) -> None:
        assert await _kafka()._IndexingKafkaConsumer__parse_message(_kafka_message(value)) is None
        records = [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.ERROR]
        assert len(records) == 1
        fields = log_record_fields(records[0])
        assert (fields["message_id"], fields["size_bytes"]) == ("record-events-0-3", len(value))
        _no_seed(caplog)

    async def test_a_message_the_read_phase_cannot_enqueue_is_logged_without_its_error_text(self, caplog) -> None:
        # A transient failure (the key store, the network) reaches the read phase's own error line; its text can
        # carry anything, so only the type is logged. A message that is not a StreamMessage is poison (F19) instead.
        config = KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["broker:9092"],
        )
        c = IndexingKafkaConsumer(
            logging.getLogger(LOGGER), config,
            fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"), default_quantum=1,
                max_buffered_messages=10, max_per_entity_messages=10, max_dwell_seconds=900.0,
            ),
        )
        c.consumer = MagicMock()
        c.consumer.commit = AsyncMock()
        c.consumer.getmany = AsyncMock(
            return_value={TopicPartition("record-events", 0): [_kafka_message(_value().encode())]}
        )
        c.running = True
        with patch.object(
            indexing_module, "open_stream_message", AsyncMock(side_effect=ConnectionError(f"key store said {SHORT_SEED}")),
        ):
            await c._IndexingKafkaConsumer__read_phase()
        enqueue = _lines(caplog, "Error enqueuing message")["Error enqueuing message"]
        assert enqueue["error_type"] == "ConnectionError"
        _no_seed(caplog)


class TestKafkaIndexingWithFairSchedulingOff:
    """FAIR_SCHEDULING_ENABLED=false: the FIFO consume loop's own error line never carries a parse error's text."""

    @pytest.fixture(autouse=True)
    def _fair_scheduling_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAIR_SCHEDULING_ENABLED", "false")

    async def _consume_once(self, c: IndexingKafkaConsumer, value: bytes) -> None:
        batches = [{TopicPartition("record-events", 0): [_kafka_message(value)]}]

        async def getmany(**_: object) -> dict:
            if batches:
                return batches.pop(0)
            c.running = False
            return {}

        c.consumer.getmany = getmany
        c.running = True
        with patch.object(c, "_IndexingKafkaConsumer__start_processing_task", AsyncMock()):
            await c._IndexingKafkaConsumer__consume_loop()

    async def test_a_transient_parse_error_is_logged_by_type_only(self, caplog) -> None:
        c = _kafka()
        assert c._scheduler is None
        with patch.object(
            indexing_module, "open_stream_message", AsyncMock(side_effect=ConnectionError(f"key store said {SHORT_SEED}")),
        ):
            await self._consume_once(c, _value().encode())
        line = _lines(caplog, "Error processing message")["Error processing message"]
        assert line["error_type"] == "ConnectionError"
        _no_seed(caplog)

    async def test_a_message_that_is_not_a_stream_message_is_dropped_without_its_text(self, caplog) -> None:
        value = json.dumps({"payload": {"n": SHORT_SEED}}).encode()  # no eventType
        with pytest.raises(ValidationError) as control:
            StreamMessage(**json.loads(value))
        assert SHORT_SEED in str(control.value)
        await self._consume_once(_kafka(), value)
        assert _lines(caplog, "Failed to parse message")
        _no_seed(caplog)

    async def test_a_fetched_back_message_that_fails_to_parse_is_logged_by_type_only(self, caplog) -> None:
        c = _kafka()
        position = (TopicPartition("record-events", 0), 3)
        c._remembered = MagicMock()
        c._remembered.peek.return_value = [position]
        c._scheduler = MagicMock()
        with patch.object(
            c, "_IndexingKafkaConsumer__parse_message", AsyncMock(side_effect=ConnectionError(f"key store said {SHORT_SEED}")),
        ):
            await c._IndexingKafkaConsumer__buffer_fetched_back(
                {("org_a",): [position]}, {position: _kafka_message(b"{}")}, set(),
            )
        line = _lines(caplog, "Could not parse fetched-back message")["Could not parse fetched-back message"]
        assert line["error_type"] == "ConnectionError"
        _no_seed(caplog)
