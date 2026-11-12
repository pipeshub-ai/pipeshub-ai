"""F54: a ValueError that is not the message's fault is retried, never dead-lettered, on every consumer.

Only ``json.loads``'s own failures are converted to poison (``envelope.decode_json``). A bad envelope setting read while
a message is opened raises a plain ValueError; treating that as poison would acknowledge every message and tell the
sink each record was unparseable. The real config readers run here (no patched parse): ``ORG_CONTEXT_MODE=bogus`` read
by ``enforcing()`` for an unsigned message while signing is on, and ``ENVELOPE_MAX_AGE_SECONDS=abc`` read by
``max_age_seconds()`` after a valid signature check.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka.structs import ConsumerRecord

from app.core import envelope
from app.core.org_context import OrgContext
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from tests.support.undecodable_json import DEEP
from tests.unit.services.messaging.message_log_helpers import in_delivery
from tests.unit.services.messaging.test_kafka_indexing_poison_offset import (
    _consumer as _kafka_consumer,
)
from tests.unit.services.messaging.test_redis_read_phase_parse_failure import (
    LANES,
    LOGGER,
    Sink,
    _acked_ids,
    _consumer,
    _good,
    _read_phase,
    _wired,
)

KEY = envelope.derive_key("test-f54-misconfigured-secret")
MISCONFIGURED = {
    "bogus_mode": ({"ORG_CONTEXT_MODE": "bogus", "ENVELOPE_SIGN": "true"}, False),  # unsigned message
    "bogus_max_age": ({"ENVELOPE_MAX_AGE_SECONDS": "abc"}, True),  # validly signed message
}


@pytest.fixture(params=MISCONFIGURED.values(), ids=MISCONFIGURED.keys())
def signed(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """The setting broken as the param says, the envelope key loaded; whether the message is signed."""
    env, is_signed = request.param
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    monkeypatch.setattr(envelope, "_keys", keys)
    return is_signed


def _value(signed: bool, topic: str) -> str:
    if not signed:
        return _good("a")["value"]
    payload = {"recordId": "r", "orgId": "org-1", "connectorId": "c", "extension": "txt", "mimeType": "text/plain"}
    return json.dumps(envelope.sign(KEY, topic, payload, OrgContext("org-1", None, "service", "test", "-"),
                                    event_type="newRecord"))


class TestRedisIndexing:
    async def test_parse_raises_a_value_error_that_is_not_poison(self, signed: bool) -> None:
        with pytest.raises(ValueError) as e:
            await _consumer()._parse_message("1-0", {"value": _value(signed, LANES[0])}, LANES[0])
        assert not isinstance(e.value, json.JSONDecodeError)

    async def test_read_phase_leaves_it_pending(self, signed: bool, caplog: pytest.LogCaptureFixture) -> None:
        sink = Sink()
        consumer = _consumer(sink)
        redis = await _wired(consumer, [(LANES[0], [("1-0", {"value": _value(signed, LANES[0])})])])

        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            await _read_phase(consumer)

        assert _acked_ids(redis) == [] and sink.reasons == []
        [line] = [r for r in caplog.records if r.getMessage() == "message.parse_deferred"]
        assert (line.levelno, line.error_type) == (logging.ERROR, "ValueError")

    async def test_recovery_does_not_ack_it(self, signed: bool) -> None:
        sink = Sink()
        consumer = _consumer(sink)
        redis = await _wired(consumer, [])
        redis.xpending_range = AsyncMock(return_value=[{"message_id": "1-0", "times_delivered": 9}])

        handed_on = await consumer._IndexingRedisStreamsConsumer__recover_entry(
            LANES[0], "1-0", {"value": _value(signed, LANES[0])}, owned=True
        )

        assert handed_on is False and _acked_ids(redis) == [] and sink.reasons == []


async def test_kafka_indexing_parse_raises(signed: bool) -> None:
    raw = _value(signed, "record-events")
    record = ConsumerRecord(topic="record-events", partition=0, offset=0, timestamp=0, timestamp_type=0, key=None,
                            value=raw.encode(), checksum=None, serialized_key_size=0,
                            serialized_value_size=len(raw), headers=())
    with pytest.raises(ValueError) as e:
        await _kafka_consumer(fair=True)._IndexingKafkaConsumer__parse_message(record)
    assert not isinstance(e.value, json.JSONDecodeError)


async def test_redis_generic_is_transient(signed: bool) -> None:
    consumer = RedisStreamsConsumer(logging.getLogger(LOGGER), RedisStreamsConfig(topics=["s"], group_id="g"))
    consumer.message_handler = AsyncMock(return_value=True)
    result = await in_delivery(
        consumer._process_message_with_classification("s", "1-0", {"value": _value(signed, "s")})
    )
    assert result == (False, False)  # (success, terminal)


@pytest.mark.parametrize("indexing", [True, False], ids=["indexing", "generic"])
async def test_postgres_retries_it(signed: bool, indexing: bool) -> None:
    consumer = PgMessagingConsumer(logging.getLogger(LOGGER), RedisStreamsConfig(topics=["t"], group_id="g"),
                                   indexing=indexing, disposition_sink=AsyncMock())
    consumer.message_handler = AsyncMock(return_value=True)
    consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())
    consumer._drive_indexing = AsyncMock()

    await consumer._run_one(Delivery(topic="t", message_id=1, attempts=1, value=_value(signed, "t"), key=None))

    consumer.queue.dead.assert_not_awaited()
    consumer.queue.nack.assert_awaited_once()


def test_the_classifier_sees_an_undecodable_body_as_terminal() -> None:
    with pytest.raises(json.JSONDecodeError) as e:
        envelope.decode_json(DEEP)
    assert MessageErrorClassifier.classify_by_exception(e.value) == MessageErrorType.TERMINAL
