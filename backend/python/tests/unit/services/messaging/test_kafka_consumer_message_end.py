"""KafkaMessagingConsumer: exactly one ``message.end`` per delivery, written inside the message's context.

Each row drives deliveries through the real consume loop with a mocked AIOKafkaConsumer (no poll timing).
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka.structs import TopicPartition

from app.core import envelope, org_context
from app.exceptions.non_retryable import NonRetryableError
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.utils.logger import log_record_fields
from app.utils.structured_logging import DefaultLoggingProvider

LOGGER = "test.p8.kafka_consumer"
TOPIC = "entity-events"
TP = TopicPartition(TOPIC, 0)
KEY = envelope.derive_key("p8-kafka-consumer-test")
SEEDED = "Ada SEEDED Lovelace"
CTX = org_context.OrgContext("org_a", "user_a", "user", "jwt", "req-producer")


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


class _Inbox:
    def __init__(self, fresh: bool) -> None:
        self.fresh = fresh

    async def claim(self, topic: str, event_id: str, org_id: str) -> bool:
        return self.fresh


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys, _Inbox(fresh=True))
    yield
    envelope.configure(None)


def _consumer(handler=None, retry_manager=None) -> KafkaMessagingConsumer:
    config = KafkaConsumerConfig(
        topics=[TOPIC], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["localhost:9092"],
    )
    c = KafkaMessagingConsumer(logging.getLogger(LOGGER), config, retry_manager=retry_manager)
    c.consumer = MagicMock()
    c.consumer.commit = AsyncMock()
    c.cleanup = AsyncMock()
    c.message_handler = handler
    return c


def _handler(result: bool = True, *, raises: BaseException | None = None) -> Callable[..., Awaitable[bool]]:
    calls = []

    async def handle(message) -> bool:
        calls.append(message)
        logging.getLogger(LOGGER).info("handler line")
        if raises is not None:
            raise raises
        return result

    handle.calls = calls  # type: ignore[attr-defined]
    return handle


def _record(value: bytes, offset: int = 7) -> MagicMock:
    m = MagicMock()
    m.topic, m.partition, m.offset, m.value = TOPIC, 0, offset, value
    return m


def _signed(payload: dict | None = None, *, event_type: str = "orgUpdated") -> bytes:
    return json.dumps(envelope.sign(KEY, TOPIC, payload or {"orgId": "org_a"}, CTX, event_type=event_type)).encode()


async def _deliver(c: KafkaMessagingConsumer, *records: MagicMock) -> None:
    """One poll per record, then stop."""
    batches = [{TP: [r]} for r in records]

    async def getmany(**_: object) -> dict:
        if batches:
            return batches.pop(0)
        c.running = False
        return {}

    c.consumer.getmany = getmany
    c.running = True
    with patch("app.services.messaging.kafka.consumer.consumer.asyncio.sleep", AsyncMock()):
        await c._KafkaMessagingConsumer__consume_loop()


def _ends(caplog) -> list[dict[str, object]]:
    return [log_record_fields(r) | {"level": r.levelno} for r in caplog.records if r.getMessage() == "message.end"]


def _one_end(caplog) -> dict[str, object]:
    ends = _ends(caplog)
    assert len(ends) == 1, ends
    return ends[0]


def _row(end: dict[str, object]) -> tuple:
    return end["outcome"], end["disposition"], end.get("reason"), end.get("attempt"), end["level"]


class TestDeliveries:
    async def test_success(self, caplog) -> None:
        c = _consumer(_handler())
        await _deliver(c, _record(_signed()))
        end = _one_end(caplog)
        assert _row(end) == ("success", "ack", None, 1, logging.INFO)
        assert (end["message_id"], end["topic"], end["event_type"], end["org_id"]) == (
            f"{TOPIC}-0-7", TOPIC, "orgUpdated", "org_a",
        )
        assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
        for record in caplog.records:
            if record.name == LOGGER and record.getMessage() != "Starting Kafka consumer loop":
                assert log_record_fields(record).get("message_id") == f"{TOPIC}-0-7", record.getMessage()
        handler_line = next(r for r in caplog.records if r.getMessage() == "handler line")
        assert log_record_fields(handler_line)["org_id"] == "org_a"
        c.consumer.commit.assert_awaited_once()

    async def test_a_settled_offset_read_again_is_skipped(self, caplog) -> None:
        handler = _handler()
        await _deliver(_consumer(handler), _record(_signed()), _record(_signed()))
        assert [_row(e) for e in _ends(caplog)] == [
            ("success", "ack", None, 1, logging.INFO),
            ("skipped", "ack", "already_settled", 1, logging.INFO),
        ]
        assert len(handler.calls) == 1

    async def test_duplicate_envelope(self, caplog) -> None:
        envelope.configure(envelope._keys, _Inbox(fresh=False))
        await _deliver(_consumer(_handler()), _record(_signed()))
        assert _row(_one_end(caplog)) == ("skipped", "ack", "duplicate", 1, logging.INFO)

    async def test_not_json_is_dropped_without_its_value(self, caplog) -> None:
        value = f'{{"registeredName": "{SEEDED}"'.encode()
        await _deliver(_consumer(_handler()), _record(value))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "unparseable", 1, logging.ERROR)
        assert end["size_bytes"] == len(value)
        assert SEEDED in value.decode()  # control
        assert all(SEEDED not in r.getMessage() for r in caplog.records)

    async def test_bytes_that_are_not_utf8_are_dropped_without_their_value(self, caplog) -> None:
        await _deliver(_consumer(_handler()), _record(b"\xff\xfeSEEDED"))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "unparseable", 1, logging.ERROR)
        assert all("SEEDED" not in r.getMessage() for r in caplog.records)

    async def test_rejected_envelope(self, caplog) -> None:
        body = envelope.sign(KEY, TOPIC, {"orgId": "org_a"}, CTX, event_type="orgUpdated")
        body["payload"] = body["payload"].replace("org_a", "org_b")
        handler = _handler()
        await _deliver(_consumer(handler), _record(json.dumps(body).encode()))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "envelope_rejected", 1, logging.ERROR)
        assert end["code"] == "bad_signature" and "error_class" not in end and handler.calls == []

    async def test_message_that_is_not_a_stream_message_never_logs_its_payload(self, caplog) -> None:
        value = _signed({"orgId": "org_a", "registeredName": SEEDED}, event_type="")
        await _deliver(_consumer(_handler()), _record(value))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "unparseable", 1, logging.ERROR)
        assert end["error_type"] == "ValidationError"
        for record in caplog.records:
            assert SEEDED not in " ".join([record.getMessage(), str(log_record_fields(record)), record.exc_text or ""])

    async def test_handler_false_is_retried_then_dead_lettered(self, caplog) -> None:
        c = _consumer(_handler(False), retry_manager=_Tracker())
        await _deliver(c, _record(_signed()), _record(_signed()), _record(_signed()))
        assert [_row(e) for e in _ends(caplog)] == [
            ("failure", "retry", "handler_false", 1, logging.WARNING),
            ("failure", "retry", "handler_false", 2, logging.WARNING),
            ("failure", "dead_letter", "handler_false", 3, logging.ERROR),
        ]
        assert all(e["org_id"] == "org_a" for e in _ends(caplog))
        c.consumer.commit.assert_awaited_once()

    async def test_transient_exception_is_retried_with_its_class(self, caplog) -> None:
        await _deliver(_consumer(_handler(raises=ConnectionError("down")), _Tracker()), _record(_signed()))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "retry", "exception", 1, logging.WARNING)
        assert end["error_class"] == "unavailable"

    async def test_terminal_exception_is_dead_lettered(self, caplog) -> None:
        await _deliver(_consumer(_handler(raises=NonRetryableError("bad")), _Tracker()), _record(_signed()))
        assert _row(_one_end(caplog)) == ("failure", "dead_letter", "exception", 1, logging.ERROR)

    async def test_no_retry_manager_commits_the_failure_so_it_is_dead_lettered(self, caplog) -> None:
        c = _consumer(_handler(False))
        await _deliver(c, _record(_signed()))
        assert _row(_one_end(caplog)) == ("failure", "dead_letter", "handler_false", 1, logging.ERROR)
        c.consumer.commit.assert_awaited_once()

    async def test_redelivery_out_of_attempts_is_dead_lettered_unprocessed(self, caplog) -> None:
        handler = _handler()
        await _deliver(_consumer(handler, _Tracker({f"{TOPIC}-0-7": 3})), _record(_signed()))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "attempts_exhausted", 4, logging.ERROR)
        assert handler.calls == []

    async def test_an_error_outside_the_handler_is_retried(self, caplog) -> None:
        c = _consumer(_handler(), _Tracker())
        with patch.object(c, "_KafkaMessagingConsumer__process_message", AsyncMock(side_effect=KeyError("bug"))):
            await _deliver(c, _record(_signed()))
        end = _one_end(caplog)
        assert (end["disposition"], end["reason"], end["error_class"], end["level"]) == (
            "retry", "exception", "internal", logging.ERROR,
        )

    async def test_cancellation_during_the_commit_is_a_retry_not_an_ack(self, caplog) -> None:
        c = _consumer(_handler())
        c.consumer.commit = AsyncMock(side_effect=asyncio.CancelledError())
        await _deliver(c, _record(_signed()))
        assert _row(_one_end(caplog)) == ("failure", "retry", "cancelled", 1, logging.INFO)

    async def test_repeated_errors_outside_the_handler_are_given_up_on(self, caplog) -> None:
        c = _consumer(_handler(), _Tracker())
        with patch.object(c, "_KafkaMessagingConsumer__process_message", AsyncMock(side_effect=KeyError("bug"))):
            await _deliver(c, _record(_signed()), _record(_signed()), _record(_signed()))
        assert [(e["disposition"], e["attempt"]) for e in _ends(caplog)] == [
            ("retry", 1), ("retry", 2), ("dead_letter", 3),
        ]
        c.consumer.commit.assert_awaited_once()

    async def test_cancellation_inside_the_handler_ends_it_and_stops_the_loop(self, caplog) -> None:
        handler = _handler(raises=asyncio.CancelledError())
        await _deliver(_consumer(handler), _record(_signed()), _record(_signed(), offset=8))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "retry", "cancelled", 1, logging.INFO)
        assert len(handler.calls) == 1
