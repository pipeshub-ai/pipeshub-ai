"""F54: an entry whose parse raises must not take the Redis read phase down with it.

The fair read phase parses a whole XREADGROUP batch before buffering any of it. A parse that raised used to abort the
batch after every entry was already in this consumer's pending list, and escaped to ``_consume_loop``, which logs
``Error in consume_messages loop`` and sleeps 1 s for every stream. Undecodable JSON and a lone surrogate in a signed
message raised there; they are now poison (acknowledged once, the sink told). Anything that still raises (a shut-down
executor, a key store) is the process, not the message: only that entry is left pending, for recovery.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.core import envelope
from app.core.org_context import OrgContext
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.undecodable_json import UNDECODABLE

LOGGER = "test.f54"
LANES = [f"record-events.{i}" for i in range(3)]
POISON = {name: {"value": UNDECODABLE[name]} for name in ("nesting_too_deep", "integer_too_long")}


def _good(record_id: str, connector_id: str = "conn-1") -> dict[str, str]:
    payload = {"recordId": record_id, "orgId": "org-1", "connectorId": connector_id,
               "extension": "txt", "mimeType": "text/plain"}
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


class Sink:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def on_message_abandoned(self, message, *, reason: str, attempts: int) -> None:
        self.reasons.append(reason)


def _consumer(sink: Sink | None = None) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger(LOGGER),
        RedisStreamsConfig(host="localhost", port=6379, batch_size=10, block_ms=100, client_id="f54",
                           group_id="g", topics=LANES),
        fair_scheduler_config=FairSchedulerConfig(enabled=True, key_fields=("orgId", "connectorId"),
                                                  max_buffered_messages=100, max_per_entity_messages=50),
        disposition_sink=sink,
    )
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    return consumer


async def _wired(consumer: IndexingRedisStreamsConsumer, batch: list) -> AsyncMock:
    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(return_value=batch)
    redis.xpending = AsyncMock(return_value={"pending": 0})
    consumer.redis = redis
    consumer.main_loop = asyncio.get_running_loop()
    consumer.running = True
    return redis


def _acked_ids(redis: AsyncMock) -> list[str]:
    return [c.args[2] for c in redis.xack.await_args_list]


async def _read_phase(consumer: IndexingRedisStreamsConsumer) -> None:
    await consumer._IndexingRedisStreamsConsumer__read_phase()


def _failing_parse(consumer: IndexingRedisStreamsConsumer, message_id: str, error: BaseException) -> None:
    real_parse = consumer._parse_message

    async def parse(mid: str, fields: dict[str, str], stream: str):  # noqa: ANN202
        if mid == message_id:
            raise error
        return await real_parse(mid, fields, stream)

    consumer._parse_message = parse  # type: ignore[method-assign]


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """The envelope key loaded, as in production; restored after the test."""
    key = envelope.derive_key("test-f54-scoped-secret")

    async def keys() -> tuple[bytes, ...]:
        return (key,)

    monkeypatch.setattr(envelope, "_keys", keys)
    return key


class TestParseLogsWithoutTheValue:
    async def test_logs_the_error_type_and_size_not_the_value(self, caplog: pytest.LogCaptureFixture) -> None:
        fields = POISON["integer_too_long"]
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            assert await _consumer()._parse_message("9-0", fields, LANES[0]) is None
        [line] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert line.error_type == "ValueError"  # the cause behind the JSONDecodeError: a number past the digit limit
        assert line.size_bytes == len(fields["value"])
        assert "1" * 50 not in caplog.text

    async def test_a_lone_surrogate_in_a_signed_message_is_poison(self, signing_key: bytes) -> None:
        message = envelope.sign(signing_key, LANES[0], {"recordId": "r", "orgId": "org-1"},
                                OrgContext("org-1", None, "service", "test", "-"), event_type="newRecord")
        message["payload"] = '{"recordId": "r", "orgId": "org-1", "n": "\ud800"}'
        fields = {"value": json.dumps(message)}  # json escapes it as \ud800, valid JSON on the wire

        assert await _consumer()._parse_message("9-0", fields, LANES[0]) is None


class TestReadPhaseSettlesThePoisonAndKeepsItsBatchMates:
    @pytest.mark.parametrize("fields", POISON.values(), ids=POISON.keys())
    async def test_poison_is_acked_once_and_reported(self, fields: dict[str, str]) -> None:
        sink = Sink()
        consumer = _consumer(sink)
        redis = await _wired(consumer, [(LANES[0], [("1-0", _good("a")), ("2-0", fields), ("3-0", _good("b"))])])

        await _read_phase(consumer)

        assert _acked_ids(redis) == ["2-0"]
        assert len(sink.reasons) == 1 and "unparseable" in sink.reasons[0]

    async def test_entries_of_every_stream_in_the_batch_are_buffered(self) -> None:
        consumer = _consumer(Sink())
        await _wired(consumer, [
            (LANES[0], [("1-0", _good("a0", "c0")), ("2-0", POISON["nesting_too_deep"]), ("3-0", _good("a1", "c0"))]),
            (LANES[1], [("1-1", _good("b0", "c1"))]),
            (LANES[2], [("1-2", _good("c0", "c2"))]),
        ])

        await _read_phase(consumer)

        assert consumer._scheduler.pending_count == 4
        held = consumer._held_entries
        assert {(LANES[0], "1-0"), (LANES[0], "3-0"), (LANES[1], "1-1"), (LANES[2], "1-2")} <= held
        assert (LANES[0], "2-0") not in held

    @pytest.mark.parametrize(("error", "level"), [
        (ConnectionError("key store unreachable"), logging.WARNING),
        (RuntimeError("cannot schedule new futures after shutdown"), logging.ERROR),
    ], ids=["transient", "process"])
    async def test_a_failure_that_is_not_the_message_leaves_only_that_entry_pending(
        self, caplog: pytest.LogCaptureFixture, error: Exception, level: int
    ) -> None:
        consumer = _consumer(Sink())
        redis = await _wired(consumer, [(LANES[0], [("1-0", _good("a")), ("2-0", _good("flaky")), ("3-0", _good("b"))])])
        _failing_parse(consumer, "2-0", error)

        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            await _read_phase(consumer)

        assert consumer._scheduler.pending_count == 2
        # Left in the pending list and unheld: recovery re-offers it (within ~1 s when this is the only consumer).
        assert (LANES[0], "2-0") not in consumer._held_entries
        assert redis.xack.await_count == 0
        [line] = [r for r in caplog.records if getattr(r, "message_id", None) == "2-0"]
        assert line.getMessage() == "message.parse_deferred"
        assert (line.levelno, line.topic, line.error_type) == (level, LANES[0], type(error).__name__)
        assert "2-0" not in line.getMessage()

    async def test_the_consume_loop_does_not_stall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The loop's catch-all sleeps 1 s for every stream; a failed parse must never reach it."""
        consumer = _consumer(Sink())
        await _wired(consumer, [(LANES[0], [("1-0", _good("flaky")), ("2-0", _good("a"))])])
        _failing_parse(consumer, "1-0", RuntimeError("cannot schedule new futures after shutdown"))
        caught: list[object] = []
        real_error = consumer.logger.error

        def error(msg: str, *args: object, **kwargs: object) -> None:
            if msg.startswith("Error in consume_messages loop"):
                caught.append(args[0] if args else msg)
                consumer.running = False
            real_error(msg, *args, **kwargs)

        monkeypatch.setattr(consumer.logger, "error", error)  # a process-global logger: restored by monkeypatch
        consumer._drain_pending = AsyncMock(return_value=False)
        reads = 0
        real_read = consumer._xreadgroup_grouped

        async def read_once(*a: object, **k: object) -> list:
            nonlocal reads
            reads += 1
            if reads > 1:
                consumer.running = False
                return []
            return await real_read(*a, **k)

        consumer._xreadgroup_grouped = read_once  # type: ignore[method-assign]
        await asyncio.wait_for(consumer._consume_loop(), timeout=5)

        assert caught == []


    async def test_the_guard_does_not_swallow_cancellation(self) -> None:
        consumer = _consumer(Sink())
        await _wired(consumer, [(LANES[0], [("1-0", _good("a"))])])
        _failing_parse(consumer, "1-0", asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await _read_phase(consumer)


class TestRecoveryDeadLettersThePoison:
    async def test_a_pending_poison_entry_is_acked_not_retried_forever(self) -> None:
        sink = Sink()
        consumer = _consumer(sink)
        redis = await _wired(consumer, [])
        redis.xpending_range = AsyncMock(return_value=[{"message_id": "2-0", "times_delivered": 1}])

        handed_on = await consumer._IndexingRedisStreamsConsumer__recover_entry(
            LANES[0], "2-0", POISON["integer_too_long"], owned=True
        )

        assert handed_on is True
        assert _acked_ids(redis) == ["2-0"]
        assert len(sink.reasons) == 1
