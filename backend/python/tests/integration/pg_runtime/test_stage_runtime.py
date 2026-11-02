"""What the staged-indexing pipeline and the code graph need from the broker and the lease store when there is no Redis:
stage consumers on the PostgreSQL queue (their own permits and lease pool, hand-back without an attempt, a record lease), and the
edge-build lease of a repository (compare-and-expire on the owner token)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from logging import getLogger

import pytest

from app.config.constants.arangodb import EventTypes
from app.modules.code_graph import edge_build_trigger as trigger
from app.modules.pipeline.leases import RecordBusy, RecordLeases
from app.services.messaging.config import (
    IndexingEvent,
    MessageBrokerType,
    PipelineEvent,
    RedisStreamsConfig,
    StreamMessage,
    Topic,
)
from app.services.messaging.consumer_concurrency import (
    RequeueWithoutAttempt,
    StageAdmission,
)
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.admin import PgMessageAdmin
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.services.pg_runtime import RuntimeDb
from app.services.pg_state import PgLeaseManager, PgState

LOG = getLogger("stage-runtime")


async def until(cond, timeout: float = 15.0) -> None:
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


def config(topic: str, group: str, client: str = "c1") -> RedisStreamsConfig:
    return RedisStreamsConfig(
        topics=[topic], group_id=group, client_id=client, batch_size=5, block_ms=100, claim_min_idle_ms=0
    )


@pytest.fixture
async def stage(runtime, monkeypatch) -> AsyncIterator[Callable[..., Awaitable[PgMessagingConsumer]]]:
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    monkeypatch.setenv("INDEXING_RECORD_LEASE_WAIT_SECONDS", "0.3")
    uid = uuid.uuid4().hex[:8]
    topic = f"pipeline-classify-{uid}"
    q = PgQueue(runtime)
    started: list[PgMessagingConsumer] = []

    class Ctx:
        pass

    c = Ctx()
    c.uid, c.topic, c.q, c.runtime, c.started = uid, topic, q, runtime, started
    c.state = PgState(runtime)
    c.leases = PgLeaseManager(c.state)
    c.producer = PgMessagingProducer(LOG, queue=q)

    async def consumer(handler, group="stage_g", admission=None, leases=True) -> PgMessagingConsumer:
        cons = PgMessagingConsumer(
            LOG,
            config(topic, group, f"c-{uid}"),
            indexing=True,
            queue=q,
            concurrency_manager=c.leases if leases else None,
            stage_admission=admission,
        )
        started.append(cons)
        await cons.start(handler)
        return cons

    c.consumer = consumer
    yield c
    for cons in started:
        await cons.stop()


async def test_the_pg_broker_has_an_admin_and_a_stage_consumer_needs_no_redis(runtime) -> None:
    """``create_admin`` for the PostgreSQL broker must not build a Redis admin (the stage start waited for it for ever)."""
    admin = MessagingFactory.create_admin(LOG, RedisStreamsConfig(topics=[]), MessageBrokerType.POSTGRES)
    assert isinstance(admin, PgMessageAdmin)
    await admin.ensure_topics_exist([Topic.CODE_GRAPH_EVENTS.value, "pipeline-classify"])
    assert isinstance(await admin.list_topics(), list)


async def test_a_stage_job_runs_on_its_own_lease_pool_and_is_acked(stage) -> None:
    seen: list[dict] = []
    pools: list[list[str]] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        seen.append(m.payload)
        async with (await stage.runtime.db()).runtime_uow(write=False) as tx:
            pools.append([r["key"] for r in await tx.fetch("/* pipeshub:test.leases */ SELECT key FROM platform.kv_entries WHERE key LIKE 'state:lease:%'")])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await stage.consumer(handler, admission=StageAdmission(stage=f"classify{stage.uid}", limit=2, workers=3))
    await stage.producer.send_event(stage.topic, "stage", {"jobId": f"j-{stage.uid}", "recordIds": ["r1"]})
    await until(lambda: len(seen) == 1)
    mine = [k for k in pools[0] if f"c-{stage.uid}:" in k]         # only this test's holders: other runs leave rows of their own
    assert any(k.startswith(f"state:lease:stage:classify{stage.uid}:") for k in mine)      # the stage's pool, not the indexing one
    assert any(k.startswith(f"state:lease:record:job:j-{stage.uid}:") for k in mine)       # the job's lease, which the stage sweeper reads
    assert not any(k.startswith("state:lease:pg-queue-indexing:") for k in mine)
    for _ in range(100):
        if not await stage.q.backlog("stage_g", [stage.topic]):
            break
        await asyncio.sleep(0.05)
    assert await stage.q.backlog("stage_g", [stage.topic]) == {}


async def test_a_stage_limit_bounds_the_jobs_running_at_once(stage) -> None:
    running = peak = done = 0

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        nonlocal running, peak, done
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.2)
        running -= 1
        done += 1
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await stage.consumer(handler, admission=StageAdmission(stage=f"classify{stage.uid}", limit=2))
    for i in range(6):
        await stage.producer.send_event(stage.topic, "stage", {"jobId": f"j{i}-{stage.uid}"})
    await until(lambda: done == 6, timeout=20.0)
    assert peak == 2


async def test_a_paused_stage_is_handed_back_without_costing_an_attempt(stage, monkeypatch) -> None:
    """The stage handler raises ``StagePaused`` (a ``RequeueWithoutAttempt``) while its dependency is down: it must never reach the
    attempt limit (3 here), however long the outage."""
    monkeypatch.setenv("PG_QUEUE_HANDBACK_BASE_S", "0.01")
    calls = 0

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        nonlocal calls
        calls += 1
        if calls <= 5:      # more hand-backs than MAX_DELIVERY_ATTEMPTS
            raise RequeueWithoutAttempt("dependency paused", delay_s=0.05)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await stage.consumer(handler, admission=StageAdmission(stage=f"classify{stage.uid}", limit=1))
    await stage.producer.send_event(stage.topic, "stage", {"jobId": f"jp-{stage.uid}"})
    await until(lambda: calls >= 6, timeout=20.0)
    for _ in range(100):
        if not await stage.q.backlog("stage_g", [stage.topic]):
            break
        await asyncio.sleep(0.05)
    assert await stage.q.backlog("stage_g", [stage.topic]) == {}      # acked, never dead


async def test_a_record_event_waits_for_a_stage_holding_the_record_and_is_not_charged(stage) -> None:
    """a stage that rewrites the stored record holds ``record:<id>`` (RecordLeases); the record consumer takes the same lease, so a
    re-index never writes beside it. The delivery that finds it held is handed back with its attempt returned, and runs once released."""
    ran: list[str] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        ran.append(m.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    leases = RecordLeases()
    leases.bind(stage.leases)

    async def current() -> bool:
        return True

    async with leases.hold(f"rec-{stage.uid}", owner="stage", wait_s=1.0, still_current=current):
        await stage.consumer(handler)          # an indexing consumer, no stage admission
        await stage.producer.send_event(stage.topic, "record", {"recordId": f"rec-{stage.uid}"})
        await asyncio.sleep(1.2)               # several hand-backs (wait 0.3 s each)
        assert ran == []
    await until(lambda: ran == [f"rec-{stage.uid}"], timeout=15.0)
    for _ in range(100):
        if not await stage.q.backlog("stage_g", [stage.topic]):
            break
        await asyncio.sleep(0.05)
    assert await stage.q.backlog("stage_g", [stage.topic]) == {}


async def test_the_record_lease_is_exclusive_between_a_stage_and_a_record_consumer_in_two_processes(runtime, monkeypatch) -> None:
    other = PgLeaseManager(PgState(RuntimeDb()))
    mine = PgLeaseManager(PgState(runtime))
    a, b = RecordLeases(), RecordLeases()
    a.bind(mine)
    b.bind(other)

    async def current() -> bool:
        return True

    rid = f"rec-x-{uuid.uuid4().hex[:8]}"
    async with a.hold(rid, owner="a", wait_s=1.0, still_current=current):
        with pytest.raises(RecordBusy):
            async with b.hold(rid, owner="b", wait_s=0.2, still_current=current):
                pass
    async with b.hold(rid, owner="b", wait_s=1.0, still_current=current):
        pass


async def test_a_record_waiting_for_a_stage_does_not_hold_an_indexing_slot(stage, monkeypatch) -> None:
    """with 2 cluster slots and a stage holding 2 records, the events of those records are handed back at once (lease first,
    tried once, before any slot) and unrelated records start right away."""
    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "2")
    monkeypatch.setenv("PG_QUEUE_HANDBACK_BASE_S", "0.2")
    ran: list[str] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        ran.append(m.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    held = [f"held{i}-{stage.uid}" for i in range(2)]
    leases = RecordLeases()
    leases.bind(stage.leases)

    async def current() -> bool:
        return True

    async with leases.hold(held[0], owner="s", wait_s=1.0, still_current=current), leases.hold(held[1], owner="s", wait_s=1.0, still_current=current):
        await stage.consumer(handler)
        for r in held:
            await stage.producer.send_event(stage.topic, "record", {"recordId": r})
        free = [f"free{i}-{stage.uid}" for i in range(6)]
        for r in free:
            await stage.producer.send_event(stage.topic, "record", {"recordId": r})
        await until(lambda: len([r for r in ran if r in free]) == 6, timeout=3.0)      # not after a 10 s lease wait
        assert not [r for r in ran if r in held]
    await until(lambda: set(held) <= set(ran), timeout=15.0)


async def test_a_paused_stage_stops_claiming_until_the_pause_ends(stage, monkeypatch) -> None:
    """a stage that hands a job back with a delay is not claimed from until then (no claim-and-hand-back churn), and then resumes."""
    monkeypatch.setenv("PG_QUEUE_HANDBACK_BASE_S", "0.01")
    calls: list[str] = []
    paused = True

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        calls.append(m.payload["jobId"])
        if paused:
            raise RequeueWithoutAttempt("dependency paused", delay_s=1.5)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await stage.consumer(handler, admission=StageAdmission(stage=f"classify{stage.uid}", limit=2))
    for i in range(20):
        await stage.producer.send_event(stage.topic, "stage", {"jobId": f"j{i}-{stage.uid}"})
    await asyncio.sleep(1.2)
    first = len(calls)
    assert 1 <= first <= 10         # one batch found the stage paused; the other 15+ were never claimed
    await asyncio.sleep(0.2)
    assert len(calls) == first      # nothing is claimed while the pause lasts
    paused = False
    await until(lambda: set(calls) >= {f"j{i}-{stage.uid}" for i in range(20)}, timeout=20.0)
    for _ in range(200):
        if not await stage.q.backlog("stage_g", [stage.topic]):
            break
        await asyncio.sleep(0.05)
    assert await stage.q.backlog("stage_g", [stage.topic]) == {}


async def test_hand_backs_back_off_per_message_and_a_backstopped_one_is_charged(stage, monkeypatch) -> None:
    """the delay of a message handed back again and again grows; one that counts toward the backstop is charged after the limit,
    so the delivery limit ends it."""
    monkeypatch.setenv("PG_QUEUE_HANDBACK_BASE_S", "0.05")
    monkeypatch.setenv("PG_QUEUE_HANDBACK_LIMIT", "3")
    stamps: list[float] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        stamps.append(asyncio.get_running_loop().time())
        raise RequeueWithoutAttempt("held elsewhere", delay_s=0.0)
        yield  # pragma: no cover

    await stage.consumer(handler)
    await stage.producer.send_event(stage.topic, "record", {"recordId": f"bs-{stage.uid}"})
    for _ in range(400):
        if (await stage.q.backlog("stage_g", [stage.topic])).get("dead"):
            break
        await asyncio.sleep(0.05)
    assert (await stage.q.backlog("stage_g", [stage.topic])).get("dead") == 1       # charged after 3 hand-backs, then 3 attempts
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps[3] > gaps[0]                                                         # 0.05, 0.1, 0.2 ...: it grows


# -- the code graph ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def pg_mode(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_MODE", "postgres")
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")


async def test_the_edge_build_lease_is_taken_once_fenced_renewed_and_released_by_its_owner(runtime, pg_mode, monkeypatch) -> None:
    org, group = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    first = await trigger.acquire_build_lock(None, org, group)
    assert first is not None and first[0] == f"{trigger.BUILD_LOCK_PREFIX}{org}:{group}"
    assert await trigger.acquire_build_lock(None, org, group) is None        # one build per repository at a time
    await trigger.assert_lease_held(None, *first)                              # the fence passes for the owner
    with pytest.raises(trigger.LeaseLostError):
        await trigger.assert_lease_held(None, first[0], "someone-else")
    # compare-and-expire: renewing with another token changes nothing, the owner's renewal extends the term
    state = PgState(runtime)
    assert not await state.lock_renew(first[0], "someone-else", 300)
    assert await state.lock_renew(first[0], first[1], 300)
    assert not await trigger.release_build_lock(None, first[0], "someone-else")     # not the owner: the lease stays
    assert await trigger.acquire_build_lock(None, org, group) is None
    assert await trigger.release_build_lock(None, *first)
    again = await trigger.acquire_build_lock(None, org, group)
    assert again is not None and again[1] != first[1]
    await trigger.release_build_lock(None, *again)


async def test_an_expired_edge_build_lease_is_taken_over_and_the_old_owner_is_fenced(runtime, pg_mode, monkeypatch) -> None:
    monkeypatch.setattr(trigger, "BUILD_LOCK_TTL_SECONDS", 1)
    org, group = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    old = await trigger.acquire_build_lock(None, org, group)
    assert old is not None
    await asyncio.sleep(1.4)
    new = await trigger.acquire_build_lock(None, org, group)
    assert new is not None
    with pytest.raises(trigger.LeaseLostError):
        await trigger.assert_lease_held(None, *old)
    assert not await trigger.release_build_lock(None, *old)       # the old owner cannot drop the new one's lease
    await trigger.assert_lease_held(None, *new)
    await trigger.release_build_lock(None, *new)


async def test_the_renewal_keeps_the_lease_and_stops_when_it_is_lost(runtime, pg_mode, monkeypatch) -> None:
    monkeypatch.setattr(trigger, "BUILD_LOCK_TTL_SECONDS", 2)
    monkeypatch.setattr(trigger, "BUILD_LOCK_RENEW_INTERVAL_SECONDS", 0.5)
    org, group = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    lease = await trigger.acquire_build_lock(None, org, group)
    assert lease is not None
    task = asyncio.create_task(trigger.renew_build_lock_until_cancelled(None, lease[0], lease[1], LOG))
    await asyncio.sleep(3.0)                                   # longer than the term: only the renewal kept it
    await trigger.assert_lease_held(None, *lease)
    assert await trigger.acquire_build_lock(None, org, group) is None
    await PgState(runtime).lock_release(lease[0], lease[1])      # lost: the renewal ends by itself
    await asyncio.wait_for(task, 3.0)


async def test_a_publish_is_claimed_once_per_window(runtime, pg_mode) -> None:
    org, group = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    assert await trigger.claim_publish(None, org, group)
    assert not await trigger.claim_publish(None, org, group)
    assert await trigger.claim_publish(None, org, group + "x")


async def test_the_code_graph_events_topic_reaches_its_consumer_group_through_the_pg_broker(runtime, monkeypatch) -> None:
    org = f"o-{uuid.uuid4().hex[:8]}"          # the topic is global and keeps what earlier runs published
    got: list[dict] = []

    async def handler(m: StreamMessage) -> bool:
        if m.payload.get("orgId") == org:
            got.append({"type": m.eventType, **m.payload})
        return True

    q = PgQueue(runtime)
    producer = PgMessagingProducer(LOG, queue=q)
    # published before the consumer group exists: the group still receives it
    await producer.send_event(
        Topic.CODE_GRAPH_EVENTS.value, EventTypes.BUILD_CODE_EDGES.value,
        {"orgId": org, "connectorId": "c", "recordGroupId": "g"}, key="g",
    )
    c = MessagingFactory.create_consumer(
        logger=LOG,
        config=config(Topic.CODE_GRAPH_EVENTS.value, f"code_graph_consumer_group-{uuid.uuid4().hex[:6]}"),
        broker_type=MessageBrokerType.POSTGRES,
    )
    c.queue = q
    await c.start(handler)
    try:
        await until(lambda: len(got) == 1)
        assert got[0] == {"type": EventTypes.BUILD_CODE_EDGES.value, "orgId": org, "connectorId": "c", "recordGroupId": "g"}
    finally:
        await c.stop()
