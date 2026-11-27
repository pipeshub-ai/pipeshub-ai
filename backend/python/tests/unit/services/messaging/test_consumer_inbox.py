"""The durable consumer inbox on the simple consumers of every broker: a redelivered or replayed event runs once, a failed handler
records nothing, and an inbox that cannot answer never stops delivery. The PostgreSQL side is in
tests/integration/pg_runtime/test_consumer_inbox.py."""

import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import envelope, org_context
from app.services.messaging import inbox as inbox_mod
from app.services.messaging import message_log
from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
)
from app.services.messaging.inbox import PgInbox, inbox_message_id, shared_inbox
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.redis_streams.broadcast_consumer import (
    RedisStreamsBroadcastConsumer,
)
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

LOG = logging.getLogger("test.consumer_inbox")
TOPIC = "entity-events"
GROUP = "g"
KEY = envelope.derive_key("consumer-inbox-test")
CTX = org_context.OrgContext("org_a", None, "service", "jwt", "req-1")


class _Inbox:
    def __init__(self) -> None:
        self.ids: set[tuple[str, str, str]] = set()

    async def seen(self, consumer, ctx) -> bool:
        return (ctx.org_id, consumer, ctx.event_id) in self.ids

    async def record(self, consumer, ctx) -> None:
        self.ids.add((ctx.org_id, consumer, ctx.event_id))


@pytest.fixture(autouse=True)
def _keys() -> Iterator[None]:
    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys)
    yield
    envelope.configure(None)


def _wire() -> dict:
    return envelope.sign(KEY, TOPIC, {"orgId": "org_a", "i": 1}, CTX, event_type="orgUpdated")


def _handler(result: bool = True) -> Callable[..., Awaitable[bool]]:
    calls: list = []

    async def handle(message) -> bool:
        calls.append(message)
        return result

    handle.calls = calls  # type: ignore[attr-defined]
    return handle


def _kafka(handler, inbox) -> KafkaMessagingConsumer:
    cfg = KafkaConsumerConfig(topics=[TOPIC], client_id="c", group_id=GROUP, auto_offset_reset="earliest",
                              enable_auto_commit=False, bootstrap_servers=["localhost:9092"])
    c = KafkaMessagingConsumer(LOG, cfg, inbox=inbox)
    c.message_handler = handler
    return c


async def _kafka_deliver(c: KafkaMessagingConsumer, wire: dict, offset: int) -> bool:
    m = MagicMock()
    m.topic, m.partition, m.offset, m.value = TOPIC, 0, offset, json.dumps(wire).encode()
    with message_log.delivery(LOG, topic=TOPIC, message_id=str(offset)):
        ok, _ = await c._KafkaMessagingConsumer__process_message(m)
    return ok


def _redis(handler, inbox) -> RedisStreamsConsumer:
    c = RedisStreamsConsumer(LOG, RedisStreamsConfig(client_id="c", group_id=GROUP, topics=[TOPIC]), provider=MagicMock(), inbox=inbox)
    c.message_handler = handler
    return c


async def _redis_deliver(c: RedisStreamsConsumer, wire: dict, entry: str) -> bool:
    with message_log.delivery(LOG, topic=TOPIC, message_id=entry):
        ok, _ = await c._process_message_with_classification(TOPIC, entry, {"value": json.dumps(wire)})
    return ok


class _Queue:
    def __init__(self) -> None:
        self.acked: list[int] = []

    async def ack(self, group_id, d) -> bool:
        self.acked.append(d.message_id)
        return True

    async def admit(self, group_id, d, *stamp) -> bool:
        return True

    async def nack(self, group_id, d, *a, **k) -> bool:
        return True


def _pg(handler, inbox, queue) -> PgMessagingConsumer:
    c = PgMessagingConsumer(LOG, RedisStreamsConfig(client_id="c", group_id=GROUP, topics=[TOPIC]), queue=queue, inbox=inbox)
    c.message_handler = handler
    return c


async def _pg_deliver(c: PgMessagingConsumer, wire: dict, row: int) -> bool:
    await c._decide(Delivery(topic=TOPIC, message_id=row, attempts=1, value=json.dumps(wire), key=None), {})
    return True


@pytest.mark.parametrize("broker", ["kafka", "redis", "postgres"])
async def test_the_same_envelope_delivered_twice_is_processed_once(broker) -> None:
    inbox, handler, wire = _Inbox(), _handler(), _wire()
    if broker == "kafka":
        c = _kafka(handler, inbox)
        assert await _kafka_deliver(c, wire, 1) and await _kafka_deliver(c, wire, 2)   # a producer retry: a second offset
    elif broker == "redis":
        c = _redis(handler, inbox)
        assert await _redis_deliver(c, wire, "1-0") and await _redis_deliver(c, wire, "2-0")
    else:
        queue = _Queue()
        c = _pg(handler, inbox, queue)
        await _pg_deliver(c, wire, 1)
        await _pg_deliver(c, wire, 2)
        assert queue.acked == [1, 2]   # the duplicate is acknowledged, not left to be redelivered
    assert len(handler.calls) == 1
    assert inbox.ids == {("org_a", GROUP, wire["ctx"]["eventId"])}


@pytest.mark.parametrize("broker", ["kafka", "redis", "postgres"])
async def test_a_handler_that_fails_leaves_the_event_unrecorded_so_it_is_retried(broker) -> None:
    inbox, wire = _Inbox(), _wire()
    failing, passing = _handler(False), _handler(True)
    if broker == "kafka":
        assert not await _kafka_deliver(_kafka(failing, inbox), wire, 1)
        assert await _kafka_deliver(_kafka(passing, inbox), wire, 1)
    elif broker == "redis":
        assert not await _redis_deliver(_redis(failing, inbox), wire, "1-0")
        assert await _redis_deliver(_redis(passing, inbox), wire, "1-0")
    else:
        await _pg_deliver(_pg(failing, inbox, _Queue()), wire, 1)
        await _pg_deliver(_pg(passing, inbox, _Queue()), wire, 1)
    assert len(failing.calls) == 1 and len(passing.calls) == 1
    assert len(inbox.ids) == 1


async def test_on_the_postgres_broker_the_record_and_the_ack_are_one_call_and_a_failed_record_still_acks() -> None:
    pg_inbox = PgInbox(runtime=MagicMock())
    pg_inbox.seen = AsyncMock(return_value=False)  # type: ignore[method-assign]
    pg_inbox.record_and_ack = AsyncMock(return_value=True)  # type: ignore[method-assign]
    queue = _Queue()
    c = _pg(_handler(), pg_inbox, queue)
    await _pg_deliver(c, _wire(), 1)
    pg_inbox.record_and_ack.assert_awaited_once()
    assert queue.acked == []   # acked inside record_and_ack

    pg_inbox.record_and_ack = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    before = envelope.INBOX_ERRORS._metric.labels("record")._value.get()
    await _pg_deliver(c, _wire(), 2)
    assert queue.acked == [2]
    assert envelope.INBOX_ERRORS._metric.labels("record")._value.get() == before + 1


def test_the_factory_gives_the_inbox_to_simple_consumers_only(monkeypatch) -> None:
    marker = object()
    monkeypatch.setattr("app.services.messaging.messaging_factory.shared_inbox", lambda: marker)
    cfg = RedisStreamsConfig(client_id="c", group_id=GROUP, topics=[TOPIC])
    simple = MessagingFactory.create_consumer(LOG, cfg, broker_type=MessageBrokerType.POSTGRES)
    indexing = MessagingFactory.create_consumer(LOG, cfg, broker_type=MessageBrokerType.POSTGRES, consumer_type=ConsumerType.INDEXING)
    assert simple.inbox is marker  # type: ignore[attr-defined]
    assert indexing.inbox is None  # type: ignore[attr-defined]
    with patch("app.services.messaging.redis_streams.consumer.get_redis_provider", MagicMock()):
        redis = MessagingFactory.create_consumer(LOG, cfg, broker_type=MessageBrokerType.REDIS)
    assert redis.inbox is marker  # type: ignore[attr-defined]
    kafka_cfg = KafkaConsumerConfig(topics=[TOPIC], client_id="c", group_id=GROUP, auto_offset_reset="earliest",
                                    enable_auto_commit=False, bootstrap_servers=["localhost:9092"])
    assert MessagingFactory.create_consumer(LOG, kafka_cfg, broker_type=MessageBrokerType.KAFKA).inbox is marker  # type: ignore[attr-defined]


def test_a_redis_broadcast_subscriber_has_no_inbox(monkeypatch) -> None:
    """XREAD from a position kept in memory never redelivers, so an inbox would only write a row per process per event."""
    monkeypatch.setattr("app.services.messaging.messaging_factory.shared_inbox", lambda: object())
    cfg = RedisStreamsConfig(client_id="c", group_id=f"{GROUP}-proc", topics=[TOPIC], broadcast=True)
    with patch("app.services.messaging.redis_streams.consumer.get_redis_provider", MagicMock()):
        consumer = MessagingFactory.create_consumer(LOG, cfg, broker_type=MessageBrokerType.REDIS)
    assert isinstance(consumer, RedisStreamsBroadcastConsumer)
    assert consumer.inbox is None
    assert consumer.retry_manager is None


@pytest.mark.parametrize(
    ("env", "wanted"),
    [
        ({}, False),                                                                       # no PostgreSQL store
        ({"MESSAGE_BROKER": "postgres"}, False),                                          # no logins
        ({"MESSAGE_BROKER": "postgres", "PG_CORE_URL": "x"}, False),                      # nothing could prune it
        ({"MESSAGE_BROKER": "postgres", "PG_CORE_URL": "x", "PG_JOBS_URL": "y"}, True),
        ({"DATA_STORE": "postgres", "PG_CORE_URL": "x", "PG_JOBS_URL": "y"}, True),
        ({"VECTOR_DB_TYPE": "pgvector", "PG_CORE_URL": "x", "PG_JOBS_URL": "y"}, True),
        ({"DATA_STORE": "neo4j", "PG_CORE_URL": "x", "PG_JOBS_URL": "y"}, False),       # logins alone do not make it a store
    ],
)
def test_the_inbox_exists_only_where_postgresql_is_a_store_and_can_be_pruned(monkeypatch, env, wanted) -> None:
    for k in ("MESSAGE_BROKER", "DATA_STORE", "KV_STORE_TYPE", "VECTOR_DB_TYPE", "REDIS_MODE", "PG_CORE_URL", "PG_JOBS_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    shared_inbox.cache_clear()
    try:
        assert isinstance(shared_inbox(), PgInbox) is wanted
    finally:
        shared_inbox.cache_clear()


def test_the_row_id_cannot_be_spelled_by_another_group_and_event() -> None:
    assert inbox_message_id("a", "b:c") != inbox_message_id("a:b", "c")


async def test_records_start_a_sweep_at_most_once_a_minute(monkeypatch) -> None:
    spawned: list = []
    monkeypatch.setattr(inbox_mod.org_context, "spawn_detached", lambda coro, name=None: spawned.append(coro.close()))
    clock = [1000.0]
    monkeypatch.setattr(inbox_mod.time, "monotonic", lambda: clock[0])
    tx = MagicMock(execute=AsyncMock())
    i = PgInbox(runtime=MagicMock())
    for _ in range(3):
        await i._record_in(tx, GROUP, CTX)
    clock[0] += inbox_mod.SWEEP_EVERY_S
    await i._record_in(tx, GROUP, CTX)
    assert len(spawned) == 2
