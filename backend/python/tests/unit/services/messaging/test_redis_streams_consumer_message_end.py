"""RedisStreamsConsumer: exactly one ``message.end`` per delivery, written inside the message's context.

Each row drives one delivery through the real loop or PEL drain, with a mocked Redis client.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core import envelope, org_context
from app.exceptions.non_retryable import NonRetryableError
from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.utils.logger import log_record_fields
from app.utils.structured_logging import DefaultLoggingProvider

LOGGER = "test.p8.redis_consumer"
TOPIC = "entity-events"
KEY = envelope.derive_key("p8-redis-consumer-test")
SEEDED = "Ada SEEDED Lovelace"
CTX = org_context.OrgContext("org_a", "user_a", "user", "jwt", "req-producer")
LOOP_LINES = {"Starting Redis Streams consumer loop"}  # the loop's own, outside any delivery


class _Tracker:
    """In-memory IRetryTracker: failure counts per message id."""

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


def _consumer(handler=None, retry_manager=None) -> RedisStreamsConsumer:
    config = RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=[TOPIC])
    c = RedisStreamsConsumer(logging.getLogger(LOGGER), config, retry_manager=retry_manager)
    c.redis = AsyncMock()
    c.redis.xpending_range = AsyncMock(return_value=[])
    c.cleanup = AsyncMock()
    c.running = True
    c.message_handler = handler
    return c


def _handler(result: bool = True, *, raises: BaseException | None = None) -> Callable[..., Awaitable[bool]]:
    calls = []

    async def handle(message: StreamMessage) -> bool:
        calls.append(message)
        logging.getLogger(LOGGER).info("handler line")
        if raises is not None:
            raise raises
        return result

    handle.calls = calls  # type: ignore[attr-defined]
    return handle


def _signed(payload: dict | None = None, *, event_type: str = "orgUpdated") -> dict[str, str]:
    body = envelope.sign(KEY, TOPIC, payload or {"orgId": "org_a"}, CTX, event_type=event_type)
    return {"value": json.dumps(body)}


async def _deliver(c: RedisStreamsConsumer, *entries: tuple[str, dict[str, str]]) -> None:
    """Run the consume loop over one read of ``entries``, then stop."""
    batches = [[(TOPIC, list(entries))]]

    async def read() -> list:
        if batches:
            return batches.pop(0)
        c.running = False
        return []

    c._read_new_messages = read
    c._drain_pending = AsyncMock(return_value=False)
    await c._consume_loop()


async def _drain(c: RedisStreamsConsumer, *, claimed=(), own=()) -> None:
    c.redis.xautoclaim = AsyncMock(return_value=("0-0", list(claimed), []))
    c.redis.xreadgroup = AsyncMock(side_effect=[[(TOPIC, list(own))], []] if own else [[]])
    await c._drain_pending()


def _ends(caplog) -> list[dict[str, object]]:
    return [log_record_fields(r) | {"level": r.levelno} for r in caplog.records if r.getMessage() == "message.end"]


def _one_end(caplog) -> dict[str, object]:
    ends = _ends(caplog)
    assert len(ends) == 1, ends
    return ends[0]


def _row(end: dict[str, object]) -> tuple:
    return end["outcome"], end["disposition"], end.get("reason"), end.get("attempt"), end["level"]


def _all_carry(caplog, **expected: object) -> None:
    for record in caplog.records:
        if record.name != LOGGER or record.getMessage() in LOOP_LINES:
            continue
        fields = log_record_fields(record)
        assert {k: fields.get(k) for k in expected} == expected, (record.getMessage(), fields)


class TestNewReads:
    async def test_success(self, caplog) -> None:
        await _deliver(_consumer(_handler()), ("1-0", _signed()))
        end = _one_end(caplog)
        assert _row(end) == ("success", "ack", None, 1, logging.INFO)
        assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
        assert end["topic"] == TOPIC and end["event_type"] == "orgUpdated"
        handler_line = next(r for r in caplog.records if r.getMessage() == "handler line")
        assert log_record_fields(handler_line)["message_id"] == "1-0"
        _all_carry(caplog, message_id="1-0")
        verified = [r for r in caplog.records if r.getMessage() in ("handler line", "message.end")]
        assert len(verified) == 2 and all(log_record_fields(r)["org_id"] == "org_a" for r in verified)

    async def test_duplicate_envelope(self, caplog) -> None:
        envelope.configure(envelope._keys, _Inbox(fresh=False))
        handler = _handler()
        await _deliver(_consumer(handler), ("1-0", _signed()))
        assert _row(_one_end(caplog)) == ("skipped", "ack", "duplicate", 1, logging.INFO)
        assert handler.calls == []

    async def test_no_value_field(self, caplog) -> None:
        await _deliver(_consumer(_handler()), ("1-0", {"init": "1"}))
        assert _row(_one_end(caplog)) == ("skipped", "ack", "no_value", 1, logging.INFO)

    async def test_not_json(self, caplog) -> None:
        value = f'{{"registeredName": "{SEEDED}"'
        await _deliver(_consumer(_handler()), ("1-0", {"value": value}))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "unparseable", 1, logging.ERROR)
        assert end["size_bytes"] == len(value)
        assert all(SEEDED not in r.getMessage() for r in caplog.records)

    async def test_not_an_object_is_retried_as_before(self, caplog) -> None:
        await _deliver(_consumer(_handler()), ("1-0", {"value": "[1, 2]"}))
        assert _row(_one_end(caplog)) == ("failure", "retry", "unparseable", 1, logging.WARNING)

    async def test_rejected_envelope_shows_the_claimed_org_and_its_code(self, caplog) -> None:
        body = envelope.sign(KEY, TOPIC, {"orgId": "org_a"}, CTX, event_type="orgUpdated")
        body["payload"] = body["payload"].replace("org_a", "org_b")  # tampered after signing
        body |= {"requestId": "req-producer", "orgId": "org_claimed"}  # what a real producer stamps
        handler = _handler()
        await _deliver(_consumer(handler), ("1-0", {"value": json.dumps(body)}))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "envelope_rejected", 1, logging.ERROR)
        assert end["code"] == "bad_signature" and end["org_id"] == "org_claimed"
        assert "event_type" not in end and "error_class" not in end
        assert handler.calls == []

    async def test_message_that_is_not_a_stream_message_never_logs_its_payload(self, caplog) -> None:
        fields = _signed({"orgId": "org_a", "registeredName": SEEDED}, event_type="")
        with pytest.raises(ValidationError) as control:
            StreamMessage(payload={"registeredName": SEEDED})
        assert SEEDED in str(control.value)
        await _deliver(_consumer(_handler()), ("1-0", fields))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "unparseable", 1, logging.ERROR)
        assert end["error_type"] == "ValidationError"
        for record in caplog.records:
            assert SEEDED not in " ".join([record.getMessage(), str(log_record_fields(record)), record.exc_text or ""])

    async def test_handler_false_is_retried_then_dead_lettered(self, caplog) -> None:
        c = _consumer(_handler(False), retry_manager=_Tracker())
        for _ in range(3):
            c.running = True
            await _deliver(c, ("1-0", _signed()))
        assert [_row(e) for e in _ends(caplog)] == [
            ("failure", "retry", "handler_false", 1, logging.WARNING),
            ("failure", "retry", "handler_false", 2, logging.WARNING),
            ("failure", "dead_letter", "handler_false", 3, logging.ERROR),
        ]
        assert all(e["org_id"] == "org_a" for e in _ends(caplog))
        c.redis.xack.assert_awaited_once()

    async def test_transient_exception_is_retried_with_its_class(self, caplog) -> None:
        await _deliver(_consumer(_handler(raises=ConnectionError("down")), _Tracker()), ("1-0", _signed()))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "retry", "exception", 1, logging.WARNING)
        assert end["error_class"] == "unavailable" and end["org_id"] == "org_a"

    async def test_terminal_exception_is_dead_lettered(self, caplog) -> None:
        await _deliver(_consumer(_handler(raises=NonRetryableError("bad")), _Tracker()), ("1-0", _signed()))
        assert _row(_one_end(caplog)) == ("failure", "dead_letter", "exception", 1, logging.ERROR)

    async def test_no_retry_manager_leaves_it_for_the_pel(self, caplog) -> None:
        c = _consumer(_handler(False))
        await _deliver(c, ("1-0", _signed()))
        assert _row(_one_end(caplog)) == ("failure", "retry", "handler_false", 1, logging.WARNING)
        c.redis.xack.assert_not_awaited()

    async def test_duplicate_whose_ack_fails_is_a_retry(self, caplog) -> None:
        envelope.configure(envelope._keys, _Inbox(fresh=False))
        c = _consumer(_handler())
        c.redis.xack = AsyncMock(side_effect=ConnectionError("redis down"))
        await _deliver(c, ("1-0", _signed()))
        end = _one_end(caplog)
        assert (end["outcome"], end["disposition"], end["reason"]) == ("failure", "retry", "exception")

    async def test_ack_and_fallback_both_failing_still_end_once(self, caplog) -> None:
        c = _consumer(_handler(), retry_manager=_Tracker())
        c.redis.xack = AsyncMock(side_effect=ConnectionError("redis down"))
        c._increment_retry_and_check = AsyncMock(side_effect=ConnectionError("redis down"))
        with patch("app.services.messaging.redis_streams.consumer.asyncio.sleep", AsyncMock()):
            await _deliver(c, ("1-0", _signed()))
        end = _one_end(caplog)
        assert (end["disposition"], end["reason"], end["error_class"]) == ("retry", "exception", "unavailable")

    async def test_cancellation_during_the_ack_is_a_retry_not_an_ack(self, caplog) -> None:
        c = _consumer(_handler())
        c.redis.xack = AsyncMock(side_effect=asyncio.CancelledError())
        await _deliver(c, ("1-0", _signed()))
        assert _row(_one_end(caplog)) == ("failure", "retry", "cancelled", 1, logging.INFO)

    async def test_cancellation_inside_the_handler_ends_it_and_stops_the_loop(self, caplog) -> None:
        handler = _handler(raises=asyncio.CancelledError())
        await _deliver(_consumer(handler), ("1-0", _signed()), ("2-0", _signed()))
        end = _one_end(caplog)
        assert _row(end) == ("failure", "retry", "cancelled", 1, logging.INFO)
        assert end["error_class"] == "cancelled" and len(handler.calls) == 1


class TestPendingEntries:
    async def test_claimed_entry_out_of_attempts_is_dead_lettered_unprocessed(self, caplog) -> None:
        handler = _handler()
        c = _consumer(handler, retry_manager=_Tracker({"1-0": 3}))
        await _drain(c, claimed=[("1-0", _signed())])
        end = _one_end(caplog)
        assert _row(end) == ("failure", "dead_letter", "attempts_exhausted", 4, logging.ERROR)
        assert end["message_id"] == "1-0" and handler.calls == []
        c.redis.xack.assert_awaited_once()

    async def test_own_entry_attempt_is_redis_delivery_count_without_retry_manager(self, caplog) -> None:
        c = _consumer(_handler(False))
        c.redis.xpending_range = AsyncMock(return_value=[{"times_delivered": 2}])
        await _drain(c, own=[("1-0", _signed())])
        assert _row(_one_end(caplog)) == ("failure", "retry", "handler_false", 2, logging.WARNING)

    async def test_own_entry_at_the_delivery_limit_is_dead_lettered(self, caplog) -> None:
        handler = _handler()
        c = _consumer(handler)
        c.redis.xpending_range = AsyncMock(return_value=[{"times_delivered": 3}])
        await _drain(c, own=[("1-0", _signed())])
        assert _row(_one_end(caplog)) == ("failure", "dead_letter", "attempts_exhausted", 3, logging.ERROR)
        assert handler.calls == []

    async def test_retried_entry_counts_the_earlier_failures(self, caplog) -> None:
        c = _consumer(_handler(), retry_manager=_Tracker({"1-0": 1}))
        await _drain(c, claimed=[("1-0", _signed())])
        end = _one_end(caplog)
        assert _row(end) == ("success", "ack", None, 2, logging.INFO)
        assert end["org_id"] == "org_a"

    @pytest.mark.parametrize("phase", ["claimed", "own"])
    async def test_a_failing_ack_on_a_pending_entry_ends_it_once(self, caplog, phase) -> None:
        tracker = _Tracker()
        c = _consumer(_handler(), retry_manager=tracker)
        c.redis.xack = AsyncMock(side_effect=ConnectionError("redis down"))
        await _drain(c, **{phase: [("1-0", _signed())]})
        end = _one_end(caplog)
        assert (end["outcome"], end["disposition"], end["reason"], end["error_class"]) == (
            "failure", "retry", "exception", "unavailable",
        )
        assert end["org_id"] == "org_a"
        assert tracker.counts == {}  # a pending entry gets no fallback retry count, as on main

    async def test_a_failing_dead_letter_ack_ends_it_once(self, caplog) -> None:
        c = _consumer(_handler(), retry_manager=_Tracker({"1-0": 3}))
        c.redis.xack = AsyncMock(side_effect=ConnectionError("redis down"))
        await _drain(c, claimed=[("1-0", _signed())])
        end = _one_end(caplog)
        assert (end["disposition"], end["reason"]) == ("retry", "exception")
