"""The messaging contract (producer -> consumer group -> ack / retry / dead letter), run against Redis Streams (a real Redis, when
``REDIS_RUNTIME_TEST_URL`` is set) and the PostgreSQL queue. Both must give the same answers, so ``MESSAGE_BROKER`` can choose the backend.

The PostgreSQL-only cases at the end cover what Streams does through the pending list and XAUTOCLAIM: a claim that expires, a
crashed consumer, concurrent claimers, and the indexing consumer's event stream.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from logging import getLogger

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.redis.config import RedisConnectionConfig
from tests.support.require_service import missing

LOG = getLogger("queue-contract")


class Backend:
    def __init__(self, kind: str, runtime) -> None:
        self.kind, self.runtime = kind, runtime
        self.topic = f"t-{uuid.uuid4().hex[:8]}"
        if kind == "redis":
            # Streams need a real Redis: fakeredis hangs on XREADGROUP BLOCK with a running consumer loop
            url = os.environ.get("REDIS_RUNTIME_TEST_URL")
            if not url:
                missing("REDIS_RUNTIME_TEST_URL (host:port) is not set")
            from app.services.redis.standalone_provider import StandaloneRedisProvider

            host, port = url.rsplit(":", 1)
            self.provider = StandaloneRedisProvider(
                RedisConnectionConfig(host=host, port=int(port))
            )
        self.consumers: list = []
        self.queue = PgQueue(runtime) if kind == "postgres" else None

    def config(self, group: str, client: str = "c1", **kw) -> RedisStreamsConfig:
        base = {
            "topics": [self.topic],
            "group_id": group,
            "client_id": client,
            "batch_size": 5,
            "block_ms": 100,
            "claim_min_idle_ms": 0,
        }
        base.update(kw)
        return RedisStreamsConfig(**base)

    async def producer(self) -> IMessagingProducer:
        if self.kind == "postgres":
            return PgMessagingProducer(LOG, queue=self.queue)
        p = RedisStreamsProducer(LOG, self.config("-"), provider=self.provider)
        await p.initialize()
        return p

    async def consume(self, group: str, handler, client: str = "c1", **kw) -> IMessagingConsumer:
        cfg = self.config(group, client, **kw)
        if self.kind == "postgres":
            c = PgMessagingConsumer(LOG, cfg, queue=self.queue)
        else:
            c = RedisStreamsConsumer(LOG, cfg, provider=self.provider)
        self.consumers.append(c)
        await c.start(handler)
        return c

    async def close(self) -> None:
        for c in self.consumers:
            await c.stop()


@pytest.fixture(params=["redis", "postgres"])
async def backend(request, runtime, monkeypatch) -> AsyncIterator[Backend]:
    monkeypatch.setenv("PG_QUEUE_RETRY_BASE_S", "0.1")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    b = Backend(request.param, runtime)
    yield b
    await b.close()


async def until(cond, timeout: float = 15.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


def collector() -> tuple[list, Callable[[StreamMessage], Awaitable[bool]]]:
    seen: list[int] = []

    async def handler(m: StreamMessage) -> bool:
        seen.append(m.payload["i"])
        return True

    return seen, handler


async def send(b: Backend, n: int, start: int = 0) -> None:
    p = await b.producer()
    for i in range(start, start + n):
        await p.send_event(b.topic, "test", {"i": i})


async def test_every_message_reaches_one_consumer_of_a_group_once(backend) -> None:
    s1, h1 = collector()
    s2, h2 = collector()
    await backend.consume("g", h1, client="c1")
    await backend.consume("g", h2, client="c2")
    await send(backend, 40)
    await until(lambda: len(s1) + len(s2) == 40)
    await asyncio.sleep(0.5)
    assert sorted(s1 + s2) == list(range(40))  # none lost, none twice


async def test_each_group_gets_every_message(backend) -> None:
    a, ha = collector()
    b, hb = collector()
    await backend.consume("ga", ha)
    await backend.consume("gb", hb)
    await send(backend, 10)
    await until(lambda: len(a) == 10 and len(b) == 10)
    assert sorted(a) == sorted(b) == list(range(10))


async def test_a_single_consumer_sees_messages_in_publish_order(backend) -> None:
    seen, h = collector()
    await backend.consume("g", h, batch_size=1)
    await send(backend, 15)
    await until(lambda: len(seen) == 15)
    assert seen == list(range(15))


async def test_a_group_created_after_publishing_replays_what_is_retained(
    backend,
) -> None:
    await send(backend, 5)
    seen, h = collector()
    await backend.consume("late", h)
    await until(lambda: len(seen) == 5)
    assert sorted(seen) == list(range(5))


async def test_an_ephemeral_group_starts_at_the_tail(backend) -> None:
    seen, h = collector()
    await backend.consume("stay", collector()[1])  # keeps the topic's messages retained
    await send(backend, 3)
    await backend.consume("temp", h, ephemeral_group=True)
    await send(backend, 2, start=3)
    await until(lambda: len(seen) == 2)
    assert sorted(seen) == [3, 4]


async def test_a_failed_handler_is_called_again(backend) -> None:
    calls: list[int] = []

    async def flaky(m: StreamMessage) -> bool:
        calls.append(m.payload["i"])
        return len(calls) > 1

    await backend.consume("g", flaky)
    await send(backend, 1)
    await until(lambda: len(calls) >= 2)
    await asyncio.sleep(0.5)
    assert calls == [0, 0]


async def test_a_message_that_keeps_failing_is_given_up_after_max_attempts(
    backend,
) -> None:
    calls: list[int] = []

    async def always_fails(m: StreamMessage) -> bool:
        calls.append(m.payload["i"])
        return False

    await backend.consume("g", always_fails)
    await send(backend, 1)
    await until(lambda: len(calls) >= 2)
    await asyncio.sleep(1.5)
    # MAX_DELIVERY_ATTEMPTS = 3. The PostgreSQL queue runs the handler three times. Streams without a RetryManager dead-letter on the
    # pending list's delivery count, one run earlier; both stop, and neither redelivers again.
    assert len(calls) in ((3,) if backend.kind == "postgres" else (2, 3))
    assert set(calls) == {0}


async def test_a_payload_round_trips(backend) -> None:
    got: list[StreamMessage] = []

    async def h(m: StreamMessage) -> bool:
        got.append(m)
        return True

    await backend.consume("g", h)
    p = await backend.producer()
    payload = {"s": 'ü\n"q"', "n": [1, 2.5, None], "o": {"k": True}}
    await p.send_event(backend.topic, "evt", payload, key="k1")
    await until(lambda: got)
    assert got[0].eventType == "evt" and got[0].payload == payload


# ---- PostgreSQL only: what the pending list does on Streams -------------------------------------------------------------------


@pytest.fixture
async def pg(runtime, monkeypatch) -> AsyncIterator[Backend]:
    monkeypatch.setenv("PG_QUEUE_RETRY_BASE_S", "0.1")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    b = Backend("postgres", runtime)
    yield b
    await b.close()


async def test_pg_a_crashed_consumers_claim_expires_and_another_consumer_takes_it(
    pg,
) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=True)
    await send(pg, 1)
    first = await pg.queue.claim("g", [pg.topic], "crashed", 10, 1.0)
    assert len(first) == 1 and first[0].attempts == 1
    assert (
        await pg.queue.claim("g", [pg.topic], "other", 10, 1.0) == []
    )  # hidden while the claim lasts
    await asyncio.sleep(1.2)
    again = await pg.queue.claim("g", [pg.topic], "other", 10, 1.0)
    assert [d.message_id for d in again] == [first[0].message_id] and again[
        0
    ].attempts == 2
    assert not await pg.queue.ack(
        "g", first[0]
    )  # the old claim can no longer ack: the attempt number fences it
    assert await pg.queue.ack("g", again[0])


async def test_pg_concurrent_claimers_never_take_the_same_message(pg) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=True)
    await send(pg, 120)
    taken: list[list[int]] = [[] for _ in range(6)]

    async def claimer(i: int) -> None:
        while True:
            got = await pg.queue.claim("g", [pg.topic], f"c{i}", 7, 30.0)
            if not got:
                return
            for d in got:
                taken[i].append(d.message_id)
                await pg.queue.ack("g", d)

    await asyncio.gather(*(claimer(i) for i in range(6)))
    flat = [m for t in taken for m in t]
    assert len(flat) == 120 and len(set(flat)) == 120
    assert sum(1 for t in taken if t) > 1  # the work really was shared


async def test_pg_acked_messages_are_swept_and_dead_ones_are_kept(pg) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=True)
    await send(pg, 2)
    one, two = await pg.queue.claim("g", [pg.topic], "c", 10, 30.0)
    await pg.queue.ack("g", one)
    await pg.queue.dead("g", two, "boom")
    await pg.queue.sweep()
    assert await pg.queue.backlog("g", [pg.topic]) == {"dead": 1}
    async with (await pg.runtime.db()).runtime_uow() as tx:
        assert (
            await tx.fetchval(
                "/* pipeshub:test.count */ SELECT count(*) FROM platform.queue_messages WHERE topic = $1",
                pg.topic,
            )
            == 1
        )


async def test_pg_a_stale_ephemeral_group_is_dropped_by_the_sweep(pg) -> None:
    await pg.queue.ensure_group(pg.topic, "gone", from_start=False, ephemeral=True)
    await pg.queue.sweep(ephemeral_stale_s=0.0)
    assert (
        await pg.queue.ensure_group(pg.topic, "gone", from_start=False, ephemeral=True)
        is True
    )  # created again: it was dropped


async def test_pg_indexing_consumer_runs_the_event_stream_and_retries_a_stream_that_never_completes(
    pg,
) -> None:
    attempts: list[int] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        attempts.append(m.payload["i"])
        yield PipelineEvent(event=IndexingEvent.START_PARSING)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        if len(attempts) > 1:
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    cfg = pg.config("idx")
    c = PgMessagingConsumer(LOG, cfg, indexing=True)
    c.queue = pg.queue
    pg.consumers.append(c)
    # the record handler's retry backoff is long on the indexing path: only the first attempt is needed to see the retry
    await c.start(handler)
    await send(pg, 1)
    await until(lambda: len(attempts) >= 1)
    await asyncio.sleep(0.3)
    assert attempts[0] == 0
    assert await pg.queue.backlog("idx", [pg.topic]) in (
        {"ready": 1},
        {"claimed": 1},
    )  # not acked: no INDEXING_COMPLETE


async def test_pg_the_dead_letter_tells_the_disposition_sink(pg) -> None:
    told: list[tuple[str, int]] = []

    class Sink:
        async def on_message_abandoned(self, message, *, reason, attempts) -> None:
            told.append((reason, attempts))

    async def always_fails(m: StreamMessage) -> bool:
        return False

    c = PgMessagingConsumer(
        LOG, pg.config("g"), disposition_sink=Sink(), queue=pg.queue
    )
    pg.consumers.append(c)
    await c.start(always_fails)
    await send(pg, 1)
    await until(lambda: told)
    assert told[0][1] == 3


@pytest.mark.chaos
async def test_pg_a_message_whose_consumer_keeps_dying_is_dead_lettered_with_its_message_and_siblings_proceed(pg) -> None:
    """The poison pill: each claim is counted when it is made, so a consumer that dies mid-message (any cause) cannot be handed the message
    for ever. After the limit the message is dead-lettered and the sink is given the message itself, to mark its record."""
    told: list[int] = []
    handled: list[int] = []

    class Sink:
        async def on_message_abandoned(self, message, *, reason, attempts) -> None:
            told.append(message.payload["i"] if message is not None else -1)

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        handled.append(m.payload["i"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await pg.queue.ensure_group(pg.topic, "idx", from_start=True)
    await send(pg, 1, start=0)                                      # the poison message
    for n in (1, 2, 3):                                             # three consumers claim it and die before settling
        got = await pg.queue.claim("idx", [pg.topic], f"dies-{n}", 10, 0.3)
        assert [d.attempts for d in got] == [n]
        await asyncio.sleep(0.4)
    await send(pg, 1, start=1)                                      # a sibling behind it
    c = PgMessagingConsumer(LOG, pg.config("idx"), indexing=True, disposition_sink=Sink())
    c.queue = pg.queue
    pg.consumers.append(c)
    await c.start(handler)
    await until(lambda: told and 1 in handled)
    assert told == [0] and handled == [1]                           # the poison one was never handed to the handler again
    assert (await pg.queue.backlog("idx", [pg.topic])).get("ready", 0) == 0


async def test_pg_per_key_order_holds_a_key_back_until_its_earlier_message_is_done(
    pg,
) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=True)
    prod = PgMessagingProducer(LOG, queue=pg.queue)
    for key, i in (("A", 1), ("A", 2), ("B", 1), ("A", 3)):
        await prod.send_event(pg.topic, "e", {"i": i}, key=key)
    first = await pg.queue.claim("g", [pg.topic], "c", 10, 30.0, ordered=True)
    assert sorted((d.key, json.loads(d.value)["payload"]["i"]) for d in first) == [
        ("A", 1),
        ("B", 1),
    ]
    assert (
        await pg.queue.claim("g", [pg.topic], "c2", 10, 30.0, ordered=True) == []
    )  # A2 waits for A1; A3 for A2
    a1 = next(d for d in first if d.key == "A")
    await pg.queue.ack("g", a1)
    second = await pg.queue.claim("g", [pg.topic], "c2", 10, 30.0, ordered=True)
    assert [(d.key, json.loads(d.value)["payload"]["i"]) for d in second] == [("A", 2)]


async def test_pg_next_due_is_the_end_of_a_retry_backoff(pg) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=True)
    assert await pg.queue.next_due("g", [pg.topic]) is None
    await send(pg, 1)
    (d,) = await pg.queue.claim("g", [pg.topic], "c", 1, 30.0)
    assert 25 < await pg.queue.next_due("g", [pg.topic]) <= 30  # the claim's expiry
    await pg.queue.nack("g", d, 2.0)
    assert 0 < await pg.queue.next_due("g", [pg.topic]) <= 2.0


async def test_pg_an_idle_consumer_waits_for_a_notification_not_a_timer(pg) -> None:
    got: list[int] = []

    async def handler(m: StreamMessage) -> bool:
        got.append(m.payload["i"])
        return True

    c = PgMessagingConsumer(LOG, pg.config("g", block_ms=60000), queue=pg.queue)
    pg.consumers.append(c)
    await c.start(handler)
    await asyncio.sleep(1.0)  # idle: parked on the wake-up event, 60 s from any timer
    await send(pg, 1)
    await until(lambda: got == [0], timeout=3.0)  # delivered by NOTIFY


async def test_pg_a_retry_is_picked_up_when_its_backoff_ends_with_no_polling(
    pg,
) -> None:
    calls: list[int] = []

    async def flaky(m: StreamMessage) -> bool:
        calls.append(1)
        return len(calls) > 1

    c = PgMessagingConsumer(LOG, pg.config("g", block_ms=60000), queue=pg.queue)
    pg.consumers.append(c)
    await c.start(flaky)
    await send(pg, 1)
    await until(
        lambda: len(calls) >= 2, timeout=4.0
    )  # backoff 0.1 s; the consumer slept until then, not 60 s


async def test_pg_record_events_are_ordered_by_key_by_default(pg) -> None:
    done: list[tuple[str, int]] = []
    gate = asyncio.Event()

    async def handler(m: StreamMessage) -> bool:
        if m.payload["i"] == 1:
            await gate.wait()  # the first message of record "r" is still running
        done.append(("r", m.payload["i"]))
        return True

    topic = "record-events"
    q = pg.queue
    c1 = PgMessagingConsumer(LOG, pg.config("g", "c1", topics=[topic]), queue=q)
    c2 = PgMessagingConsumer(LOG, pg.config("g", "c2", topics=[topic]), queue=q)
    pg.consumers += [c1, c2]
    await c1.start(handler)
    await c2.start(handler)
    prod = PgMessagingProducer(LOG, queue=q)
    await prod.send_event(topic, "e", {"i": 1}, key="r")
    await prod.send_event(topic, "e", {"i": 2}, key="r")
    await asyncio.sleep(1.0)
    assert (
        done == []
    )  # message 2 is not started on the other consumer while message 1 runs
    gate.set()
    await until(lambda: done == [("r", 1), ("r", 2)])


async def test_pg_two_indexing_workers_share_one_cluster_wide_cap(
    pg, monkeypatch
) -> None:
    from app.services.pg_runtime import RuntimeDb
    from app.services.pg_state import PgLeaseManager, PgState

    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
    running = peak = done = 0

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        nonlocal running, peak, done
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.3)
        running -= 1
        done += 1
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    workers = []
    for i in range(2):  # two "processes": each its own pool and lease manager
        q = PgQueue(RuntimeDb())
        c = PgMessagingConsumer(
            LOG,
            pg.config("idx", f"w{i}"),
            indexing=True,
            queue=q,
            concurrency_manager=PgLeaseManager(PgState(q.rt)),
        )
        workers.append(c)
        pg.consumers.append(c)
        await c.start(handler)
    await send(pg, 8)
    await until(lambda: done == 8, timeout=20.0)
    assert (
        peak == 2
    )  # four handlers could have run (2 workers x 2 slots): the cap held across both


@pytest.mark.perf
@pytest.mark.timeout(300)       # 100k rows published and a slow host: the claim itself is asserted, not the set-up
async def test_pg_ordered_claim_costs_the_batch_not_the_backlog(pg) -> None:
    """100k queued messages with distinct keys (an initial sync): a claim of a few records takes milliseconds, not minutes."""
    import statistics
    import time

    await pg.queue.ensure_group(pg.topic, "g", from_start=False)
    for start in range(0, 100_000, 5000):
        await pg.queue.publish(
            pg.topic, [(f"rec{start + i}", "{}") for i in range(5000)]
        )
    async with (await pg.runtime.db()).runtime_uow(write=True) as tx:
        await tx.execute(
            "/* pipeshub:test.analyze */ ANALYZE platform.queue_deliveries"
        )
    times, held = [], []
    for _ in range(15):
        t0 = time.perf_counter()
        got = await pg.queue.claim("g", [pg.topic], "c", 7, 600.0, ordered=True)
        times.append(1000 * (time.perf_counter() - t0))
        assert len(got) == 7
        held += got
    assert statistics.median(times) < 50, (
        times
    )  # was 3.7 s at 1k messages and minutes at 100k
    assert len({d.key for d in held}) == len(held)  # and each record once
    await pg.queue.drop_group(pg.topic, "g")
    await pg.queue.sweep()


async def test_pg_a_key_is_exclusive_even_when_its_ids_do_not_follow_commit_order(
    pg,
) -> None:
    """Two producers publish the same record's events; the earlier id commits last. While one is being handled the other is not handed out."""
    import asyncpg

    from app.services.messaging.postgres import queue as qmod

    await pg.queue.ensure_group(pg.topic, "g", from_start=False)

    async def raw() -> asyncpg.Connection:
        c = await asyncpg.connect(pg.runtime.cfg.dsn)
        await c.execute("SET ROLE pipeshub_connectors")
        return c

    t1, t2 = await raw(), await raw()
    tx1 = t1.transaction()
    await tx1.start()
    await t1.execute(
        qmod._INSERT, pg.topic, ["K"], ['{"n":1}'], ["g"]
    )  # m1: uncommitted, the lower id
    await t2.execute(qmod._INSERT, pg.topic, ["K"], ['{"n":2}'], ["g"])  # m2: committed
    a = await pg.queue.claim("g", [pg.topic], "A", 10, 60.0, ordered=True)
    assert [json.loads(d.value)["n"] for d in a] == [2]
    await tx1.commit()
    assert (
        await pg.queue.claim("g", [pg.topic], "B", 10, 60.0, ordered=True) == []
    )  # m1 waits while m2 is in flight
    await pg.queue.ack("g", a[0])
    b = await pg.queue.claim("g", [pg.topic], "B", 10, 60.0, ordered=True)
    assert [json.loads(d.value)["n"] for d in b] == [1]  # and runs once m2 is done
    await t1.close()
    await t2.close()


async def test_pg_the_sweep_clears_a_burst_in_one_call_and_counts_a_dead_letter_from_its_death(
    pg,
) -> None:
    await pg.queue.ensure_group(pg.topic, "g", from_start=False)
    for _start in range(0, 12_000, 4000):
        await pg.queue.publish(pg.topic, [(None, "{}") for _ in range(4000)])
    db = await pg.runtime.db()
    async with db.runtime_uow(write=True) as tx:  # all of it acked
        await tx.execute(
            "/* pipeshub:test.ack */ DELETE FROM platform.queue_deliveries WHERE topic = $1",
            pg.topic,
        )
    await pg.queue.sweep()
    async with db.runtime_uow() as tx:
        left = await tx.fetchval(
            "/* pipeshub:test.left */ SELECT count(*) FROM platform.queue_messages WHERE topic = $1",
            pg.topic,
        )
    assert left == 0  # 12,000 > one batch of 5,000: the sweep looped

    await send(pg, 1)
    (d,) = await pg.queue.claim("g", [pg.topic], "c", 1, 30.0)
    assert await pg.queue.nack("g", d, 3600.0)  # backoff an hour ahead...
    await asyncio.sleep(0.01)
    async with db.runtime_uow(write=True) as tx:
        await tx.execute(
            "/* pipeshub:test.vis */ UPDATE platform.queue_deliveries SET visible_at = now() + interval '1 hour', status = 'claimed', locked_until = now() + interval '1 minute' WHERE topic = $1",
            pg.topic,
        )
    assert await pg.queue.dead("g", d, "boom") is True
    async with db.runtime_uow() as tx:
        age = await tx.fetchval(
            "/* pipeshub:test.age */ SELECT extract(epoch FROM now() - visible_at) FROM platform.queue_deliveries WHERE topic = $1 AND status = 'dead'",
            pg.topic,
        )
    assert age is not None and age < 5  # stamped when it died, not when it was queued


@pytest.mark.timeout(300)
@pytest.mark.parametrize("analysed", [False, True], ids=["cold", "analysed"])
async def test_pg_ordered_claim_probes_the_key_index_on_a_cold_table(analysed) -> None:
    """The per-key probe must use the key index on a table nothing has analysed yet (a fresh install's first burst), not the primary key
    or the open index, which would walk the backlog: checked on a scratch copy of the table (the service roles cannot create tables, so
    this needs ``PG_ADMIN_URL``, a superuser DSN of the same database)."""
    import asyncpg

    from app.services.messaging.postgres import queue as qmod

    admin_url = os.environ.get("PG_ADMIN_URL")
    if not admin_url:
        missing("PG_ADMIN_URL is not set")
    con = await asyncpg.connect(admin_url)
    try:
        await con.execute("DROP SCHEMA IF EXISTS plan_test CASCADE; CREATE SCHEMA plan_test")
        await con.execute("CREATE TABLE plan_test.d (LIKE platform.queue_deliveries INCLUDING ALL) WITH (autovacuum_enabled = false)")
        await con.execute(
            "INSERT INTO plan_test.d (group_id, topic, message_id, msg_key) "
            "SELECT 'go', 't', i, 'r' || i FROM generate_series(1, 100000) AS i"
        )
        if analysed:
            await con.execute("ANALYZE plan_test.d")
        sql = qmod._CLAIM_ORDERED.replace("platform.queue_deliveries", "plan_test.d")
        tx = con.transaction()
        await tx.start()
        try:
            plan = [r[0] for r in await con.fetch("EXPLAIN (ANALYZE, COSTS OFF) " + sql, "go", ["t"], 7, 60.0, "x")]
        finally:
            await tx.rollback()
        text = "\n".join(plan)
        probes = [line for line in plan if "Index" in line and " on d e" in line]
        assert probes, text
        assert all("msg_key" in line for line in probes), text         # the key index, not d_pkey or the open index
        ms = float(next(line for line in plan if "Execution Time" in line).split(":")[1].split()[0])
        assert ms < 25, text                                            # milliseconds; ~1 ms when it holds, 75+ on a backlog walk
    finally:
        await con.execute("DROP SCHEMA IF EXISTS plan_test CASCADE")
        await con.close()
