"""The durable consumer inbox on a migrated database, behind consumers built by ``MessagingFactory`` on the PostgreSQL queue and on
Redis Streams (``REDIS_RUNTIME_TEST_URL``): an event delivered twice runs once, a replayed envelope is skipped and acknowledged, a
failed handler records nothing and is retried, rows are per org, and the sweep removes rows past the replay window only.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from logging import getLogger

import pytest

from app.core import envelope, org_context
from app.plugins.telemetry.modules import envelope_metrics
from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.inbox import PgInbox, inbox_message_id, shared_inbox
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.queue import PgQueue
from tests.support.require_service import missing

LOG = getLogger("consumer-inbox-it")
KEY = envelope.derive_key("consumer-inbox-it")
JOBS_URL = os.environ.get("PG_TEST_JOBS_URL", "")
ORG_A, ORG_B = "a0" * 12, "b0" * 12   # org ids are 24 hex digits (a check on the table)


@pytest.fixture(params=["postgres", "redis"])
async def broker(request, runtime, monkeypatch) -> AsyncIterator[tuple[str, list]]:
    if not JOBS_URL:
        missing("PG_TEST_JOBS_URL is not set")
    monkeypatch.setenv("PG_JOBS_URL", JOBS_URL)
    monkeypatch.setenv("PG_QUEUE_RETRY_BASE_S", "0.1")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "5")
    if request.param == "redis":
        url = os.environ.get("REDIS_RUNTIME_TEST_URL")
        if not url:
            missing("REDIS_RUNTIME_TEST_URL (host:port) is not set")
        monkeypatch.setenv("MESSAGE_BROKER", "redis")
        monkeypatch.setenv("DATA_STORE", "postgres")   # PostgreSQL is a store here, the broker is Redis
    else:
        monkeypatch.setenv("MESSAGE_BROKER", "postgres")

    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys, KEY)
    shared_inbox.cache_clear()
    consumers: list = []
    yield request.param, consumers
    for c in consumers:
        await c.stop()
    shared_inbox.cache_clear()
    envelope.configure(None)


def _config(kind: str, topic: str, group: str) -> RedisStreamsConfig:
    extra = {}
    if kind == "redis":
        host, port = os.environ["REDIS_RUNTIME_TEST_URL"].rsplit(":", 1)
        extra = {"host": host, "port": int(port)}
    return RedisStreamsConfig(topics=[topic], group_id=group, client_id="c1", batch_size=5, block_ms=100, claim_min_idle_ms=0, **extra)


async def _start(broker, topic: str, handler, group: str = "g") -> IMessagingConsumer:
    kind, consumers = broker
    cfg = _config(kind, topic, group)
    c = MessagingFactory.create_consumer(LOG, cfg, broker_type=MessageBrokerType(kind))
    consumers.append(c)
    await c.start(handler)
    return c


async def _send(broker, topic: str, wire: dict) -> None:
    kind, _ = broker
    p = MessagingFactory.create_producer(LOG, _config(kind, topic, "-"), broker_type=MessageBrokerType(kind))
    await p.initialize()
    try:
        await p.send_message(topic, wire)
    finally:
        await p.cleanup()


def _signed_for(topic: str, org: str = ORG_A, i: int = 0) -> dict:
    ctx = org_context.OrgContext(org, None, "service", "jwt", "req-1")
    return envelope.sign(KEY, topic, {"orgId": org, "i": i}, ctx, event_type="orgUpdated")


async def until(cond, timeout: float = 15.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


async def _settled(broker, topic: str, group: str = "g") -> None:
    """Wait until the group has read and acknowledged everything queued: nothing unread and nothing unacknowledged."""
    kind, _ = broker
    if kind == "postgres":
        q = PgQueue()

        async def empty() -> bool:
            backlog = await q.backlog(group, [topic])
            return not backlog.get("ready") and not backlog.get("claimed")
    else:
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.standalone_provider import StandaloneRedisProvider

        host, port = os.environ["REDIS_RUNTIME_TEST_URL"].rsplit(":", 1)
        redis = StandaloneRedisProvider(RedisConnectionConfig(host=host, port=int(port))).get_client()

        async def empty() -> bool:
            # XPENDING counts only delivered-but-unacknowledged entries, so an entry not yet read would pass it. XINFO GROUPS gives both:
            # "pending: ... messages that were delivered but are yet to be acknowledged" and "lag: the number of entries in the stream
            # that are still waiting to be delivered to the group's consumers, or a NULL when that number can't be determined"
            # (https://redis.io/docs/latest/commands/xinfo-groups/). A NULL lag is not settled.
            groups = await redis.xinfo_groups(topic)
            (g,) = [x for x in groups if (x["name"].decode() if isinstance(x["name"], bytes) else x["name"]) == group]
            return g["pending"] == 0 and g.get("lag") == 0
    end = asyncio.get_running_loop().time() + 15
    while not await empty():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("messages still open")
        await asyncio.sleep(0.1)


def _topic() -> str:
    return f"inbox-{uuid.uuid4().hex[:8]}"


async def test_the_same_envelope_delivered_twice_is_processed_once(broker) -> None:
    topic, calls = _topic(), []

    async def handler(m) -> bool:
        calls.append(m.payload["i"])
        return True

    await _start(broker, topic, handler)
    wire = _signed_for(topic, i=1)
    await _send(broker, topic, wire)
    await _send(broker, topic, wire)   # a producer retry or a broker redelivery: the same event again
    await _send(broker, topic, _signed_for(topic, i=2))
    await until(lambda: 2 in calls)
    await _settled(broker, topic)
    assert sorted(calls) == [1, 2]


async def test_a_replayed_envelope_is_rejected_after_the_original_was_acknowledged(broker) -> None:
    topic, calls = _topic(), []

    async def handler(m) -> bool:
        calls.append(m.payload["i"])
        return True

    await _start(broker, topic, handler)
    wire = _signed_for(topic, i=7)
    await _send(broker, topic, wire)
    await until(lambda: calls == [7])
    await _settled(broker, topic)
    await _send(broker, topic, json.loads(json.dumps(wire)))   # the captured message written to the broker again
    await _settled(broker, topic)
    await asyncio.sleep(0.3)
    assert calls == [7]


async def test_a_handler_that_fails_leaves_the_event_unrecorded_so_it_is_retried(broker) -> None:
    topic, attempts = _topic(), []
    wire = _signed_for(topic, i=3)
    ctx = org_context.OrgContext(ORG_A, None, "service", "envelope", "r", event_id=wire["ctx"]["eventId"])
    inbox = PgInbox()
    rows_while_failing: list[bool] = []

    async def handler(m) -> bool:
        attempts.append(m.payload["i"])
        if len(attempts) == 1:
            rows_while_failing.append(await inbox.seen("g", ctx))
            raise RuntimeError("transient")
        return True

    await _start(broker, topic, handler)
    await _send(broker, topic, wire)
    await until(lambda: len(attempts) >= 2, timeout=30)
    await _settled(broker, topic)
    assert rows_while_failing == [False]
    assert await inbox.seen("g", ctx)
    assert attempts == [3, 3]


async def test_another_group_and_another_org_keep_their_own_rows(runtime, monkeypatch) -> None:
    inbox = PgInbox()
    event = uuid.uuid4().hex
    a = org_context.OrgContext(ORG_A, None, "service", "envelope", "r", event_id=event)
    b = org_context.OrgContext(ORG_B, None, "service", "envelope", "r", event_id=event)
    await inbox.record("g1", a)
    await inbox.record("g1", a)   # idempotent
    assert await inbox.seen("g1", a)
    assert not await inbox.seen("g2", a)   # another group on the topic still gets it
    assert not await inbox.seen("g1", b)   # the same id in another org is another event, and org a's row is not visible to org b


async def test_the_sweep_removes_rows_past_the_replay_window_and_keeps_the_rest(runtime, monkeypatch) -> None:
    if not JOBS_URL:
        missing("PG_TEST_JOBS_URL is not set")
    monkeypatch.setenv("PG_JOBS_URL", JOBS_URL)
    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "60")
    monkeypatch.setenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", "60")
    window = envelope.replay_window_seconds()
    inbox = PgInbox()
    old, fresh = uuid.uuid4().hex, uuid.uuid4().hex
    for org in (ORG_A, ORG_B):
        with org_context.bind(org_context.OrgContext(org, None, "service", "envelope", "r")):
            async with (await inbox.rt.db()).uow(write=True) as tx:
                await tx.execute(
                    "INSERT INTO platform.consumer_inbox (org_id, message_id, received_at) VALUES ($1, $2, now() - make_interval(secs => $3))",
                    org, inbox_message_id("g", old), window + 5.0,
                )
                await tx.execute(
                    "INSERT INTO platform.consumer_inbox (org_id, message_id, received_at) VALUES ($1, $2, now() - make_interval(secs => $3))",
                    org, inbox_message_id("g", fresh), window - 30.0,
                )
    assert await inbox.sweep() >= 2   # other tests' old rows may go too; these two must
    for org in (ORG_A, ORG_B):
        def ctx(e: str, org: str = org) -> org_context.OrgContext:
            return org_context.OrgContext(org, None, "service", "envelope", "r", event_id=e)

        assert not await inbox.seen("g", ctx(old))
        assert await inbox.seen("g", ctx(fresh))


async def test_the_service_role_cannot_delete_rows_only_the_jobs_role_prunes(runtime) -> None:
    inbox = PgInbox()
    ctx = org_context.OrgContext(ORG_A, None, "service", "envelope", "r", event_id=uuid.uuid4().hex)
    await inbox.record("g", ctx)
    with org_context.bind(ctx), pytest.raises(Exception, match="permission denied"):
        async with (await inbox.rt.db()).uow(write=True) as tx:
            await tx.execute("DELETE FROM platform.consumer_inbox WHERE org_id = $1", ORG_A)


async def test_an_event_the_inbox_cannot_record_is_still_processed_and_acknowledged(broker) -> None:
    topic, calls = _topic(), []

    async def handler(m) -> bool:
        calls.append(m.payload["i"])
        return True

    await _start(broker, topic, handler)
    before = envelope_metrics.INBOX_ERRORS._metric.labels("record")._value.get()
    await _send(broker, topic, _signed_for(topic, org="not-an-org-id", i=9))   # refused by the table's org id check
    await until(lambda: calls == [9])
    await _settled(broker, topic)
    assert envelope_metrics.INBOX_ERRORS._metric.labels("record")._value.get() > before
