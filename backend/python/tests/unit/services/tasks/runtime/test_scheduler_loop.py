"""Unit tests for `SchedulerLoop` -- tick/claim/dispatch/reschedule, misfire
policy, fairness caps, and both reapers (expired trigger leases + stale
PENDING runs). Runs the real `RedisTriggerStore`/`RedisRunStore` adapters
over `fakeredis` (same pattern as `test_trigger_store_contract.py` /
`test_run_store_contract.py`) rather than hand-rolled fakes, so these tests
also exercise the actual Lua-scripted claim/lease semantics the loop
depends on -- a fake store could hide a mismatch between what the loop
assumes and what the real adapter guarantees.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.messaging.config import Topic
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.adapters.redis.trigger_store import RedisTriggerStore
from app.services.tasks.domain.models import RunStatus, TaskTrigger, TriggerKind
from app.services.tasks.domain.policies import MisfirePolicy
from app.services.tasks.interface.clock import FixedClock
from app.services.tasks.runtime.scheduler_loop import SchedulerLoop


class FakeMessagingProducer:
    """Minimal `IMessagingProducer` test double -- records every event
    published so tests can assert on topic/payload without a real broker.
    `fail_next` lets a test simulate a transient publish failure to prove
    the outbox-lite run row survives it.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail_next = False

    async def initialize(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        return await self.send_event(topic, "message", message, key=key)

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        self.sent.append({"topic": topic, "event_type": event_type, "payload": payload, "key": key})
        return True


def _make_cron_trigger(**overrides) -> TaskTrigger:
    defaults = {
        "task_id": "task-1",
        "org_id": "org-1",
        "kind": TriggerKind.CRON,
        "cron_expression": "* * * * *",
        "timezone": "UTC",
    }
    defaults.update(overrides)
    return TaskTrigger(**defaults)


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def trigger_store(redis_client) -> RedisTriggerStore:
    return RedisTriggerStore(redis_client)


@pytest.fixture
def run_store(redis_client) -> RedisRunStore:
    return RedisRunStore(redis_client)


@pytest.fixture
def producer() -> FakeMessagingProducer:
    return FakeMessagingProducer()


def _make_loop(trigger_store, run_store, producer, clock, **overrides) -> SchedulerLoop:
    kwargs = {
        "trigger_store": trigger_store,
        "run_store": run_store,
        "producer": producer,
        "clock": clock,
        "owner": "scheduler-test",
        "claim_batch_size": 50,
        "lease_seconds": 30.0,
    }
    kwargs.update(overrides)
    return SchedulerLoop(**kwargs)


class TestBasicDispatch:
    async def test_due_trigger_creates_and_publishes_run(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        trigger = await trigger_store.upsert(_make_cron_trigger(next_run_at=now.isoformat()))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        stats = await loop.tick()

        assert stats.claimed == 1
        assert stats.dispatched_runs == 1
        assert stats.errors == 0
        assert len(producer.sent) == 1
        assert producer.sent[0]["topic"] == Topic.TASK_EVENTS.value
        assert producer.sent[0]["payload"]["task_id"] == "task-1"

        refetched = await trigger_store.get(trigger.trigger_id)
        assert refetched is not None
        assert refetched.lease_owner is None
        assert refetched.run_count == 1
        # CRON "* * * * *" from 09:00:00 -> next boundary 09:01:00.
        assert refetched.next_run_at == "2024-01-01T09:01:00+00:00"

    async def test_not_due_trigger_is_not_claimed(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        future = now + timedelta(hours=1)
        clock = FixedClock(now)
        await trigger_store.upsert(_make_cron_trigger(next_run_at=future.isoformat()))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        stats = await loop.tick()

        assert stats.claimed == 0
        assert stats.dispatched_runs == 0
        assert producer.sent == []

    async def test_disabled_trigger_never_reschedules(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        trigger = await trigger_store.upsert(_make_cron_trigger(
            kind=TriggerKind.ONE_TIME, fire_at=now.isoformat(), next_run_at=now.isoformat(),
            cron_expression=None,
        ))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        await loop.tick()

        refetched = await trigger_store.get(trigger.trigger_id)
        assert refetched is not None
        assert refetched.next_run_at is None
        assert refetched.run_count == 1


class TestIdempotency:
    async def test_reprocessing_same_fire_time_does_not_duplicate_run(
        self, trigger_store, run_store, producer,
    ) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        trigger = await trigger_store.upsert(_make_cron_trigger(next_run_at=now.isoformat()))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        # Simulate a crash mid-processing: dispatch the run directly at the
        # loop's own fire time, without ever calling complete_claim, then
        # let a fresh tick's claim_due pick the (still-eligible-because-
        # never-completed) trigger back up -- exactly what happens after a
        # lease expires and is reaped.
        created_first = await loop._dispatch_run(trigger, now.isoformat())
        assert created_first is True
        assert len(producer.sent) == 1

        created_second = await loop._dispatch_run(trigger, now.isoformat())
        assert created_second is False
        assert len(producer.sent) == 1  # no second publish from this path

        runs = await run_store.list_for_task("task-1")
        assert runs.total == 1

    async def test_two_schedulers_claiming_same_trigger_only_one_dispatches(
        self, trigger_store, run_store, producer,
    ) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        await trigger_store.upsert(_make_cron_trigger(next_run_at=now.isoformat()))

        loop_a = _make_loop(trigger_store, run_store, producer, clock, owner="scheduler-a")
        loop_b = _make_loop(trigger_store, run_store, producer, clock, owner="scheduler-b")

        stats_a = await loop_a.tick()
        stats_b = await loop_b.tick()

        # Exactly one of the two claimed and dispatched the trigger --
        # `claim_due`'s lease is the distributed lock.
        assert stats_a.claimed + stats_b.claimed == 1
        assert stats_a.dispatched_runs + stats_b.dispatched_runs == 1
        assert len(producer.sent) == 1


class TestMisfirePolicy:
    async def test_skip_policy_fires_exactly_once_at_claim_time(self, trigger_store, run_store, producer) -> None:
        due_at = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        claimed_at = due_at + timedelta(hours=3)  # scheduler was down for 3 hours
        clock = FixedClock(claimed_at)
        await trigger_store.upsert(_make_cron_trigger(
            next_run_at=due_at.isoformat(), misfire_policy=MisfirePolicy.SKIP,
        ))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        stats = await loop.tick()

        assert stats.dispatched_runs == 1

    async def test_run_all_policy_replays_every_missed_minute(self, trigger_store, run_store, producer) -> None:
        due_at = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        claimed_at = due_at + timedelta(minutes=4)
        clock = FixedClock(claimed_at)
        await trigger_store.upsert(_make_cron_trigger(
            next_run_at=due_at.isoformat(), misfire_policy=MisfirePolicy.RUN_ALL,
        ))

        loop = _make_loop(trigger_store, run_store, producer, clock)
        stats = await loop.tick()

        # 09:00, 09:01, 09:02, 09:03, 09:04 -- five missed boundaries.
        assert stats.dispatched_runs == 5
        assert len(producer.sent) == 5


class TestReapers:
    async def test_expired_trigger_lease_is_reclaimed_on_a_later_tick(
        self, trigger_store, run_store, producer,
    ) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        await trigger_store.upsert(_make_cron_trigger(next_run_at=now.isoformat()))

        # Claim it directly (bypassing SchedulerLoop) to simulate a
        # scheduler that crashed immediately after claiming, before ever
        # calling complete_claim.
        claimed = await trigger_store.claim_due(now=now, owner="dead-scheduler", limit=10, lease_seconds=10.0)
        assert len(claimed) == 1

        clock.advance(11)  # past the 10s lease
        loop = _make_loop(trigger_store, run_store, producer, clock, lease_seconds=30.0)
        stats = await loop.tick()

        assert stats.reaped_leases == 1
        assert stats.claimed == 1
        assert stats.dispatched_runs == 1

    async def test_stale_pending_run_is_republished(self, trigger_store, run_store, producer) -> None:
        past = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        now = past + timedelta(minutes=5)
        clock = FixedClock(now)

        from app.services.tasks.domain.models import TaskRun, compute_idempotency_key
        stale_run = TaskRun(
            task_id="task-1", org_id="org-1",
            idempotency_key=compute_idempotency_key("task-1", past.isoformat()),
            scheduled_for=past.isoformat(), created_at=past.isoformat(),
        )
        await run_store.create_if_absent(stale_run)

        loop = _make_loop(trigger_store, run_store, producer, clock, stale_pending_after_seconds=60.0)
        stats = await loop.tick()

        assert stats.republished_pending == 1
        assert len(producer.sent) == 1
        assert producer.sent[0]["payload"]["run_id"] == stale_run.run_id

    async def test_recently_pending_run_is_not_yet_republished(self, trigger_store, run_store, producer) -> None:
        recent = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(recent + timedelta(seconds=5))

        from app.services.tasks.domain.models import TaskRun, compute_idempotency_key
        run = TaskRun(
            task_id="task-1", org_id="org-1",
            idempotency_key=compute_idempotency_key("task-1", recent.isoformat()),
            scheduled_for=recent.isoformat(), created_at=recent.isoformat(),
        )
        await run_store.create_if_absent(run)

        loop = _make_loop(trigger_store, run_store, producer, clock, stale_pending_after_seconds=60.0)
        stats = await loop.tick()

        assert stats.republished_pending == 0
        assert producer.sent == []


class TestFairness:
    async def test_per_org_cap_defers_excess_triggers_to_next_tick(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        for i in range(3):
            await trigger_store.upsert(_make_cron_trigger(
                task_id=f"task-noisy-{i}", org_id="org-noisy", next_run_at=now.isoformat(),
            ))
        await trigger_store.upsert(_make_cron_trigger(
            task_id="task-quiet", org_id="org-quiet", next_run_at=now.isoformat(),
        ))

        loop = _make_loop(trigger_store, run_store, producer, clock, per_org_claim_cap=1)
        stats = await loop.tick()

        assert stats.claimed == 4
        assert stats.deferred_for_fairness == 2
        assert stats.dispatched_runs == 2
        dispatched_orgs = [entry["payload"]["org_id"] for entry in producer.sent]
        assert dispatched_orgs.count("org-noisy") == 1
        assert dispatched_orgs.count("org-quiet") == 1

    async def test_deferred_trigger_is_immediately_reclaimable_next_tick(
        self, trigger_store, run_store, producer,
    ) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        for i in range(2):
            await trigger_store.upsert(_make_cron_trigger(
                task_id=f"task-{i}", org_id="org-1", next_run_at=now.isoformat(),
            ))

        loop = _make_loop(trigger_store, run_store, producer, clock, per_org_claim_cap=1)
        first = await loop.tick()
        assert first.dispatched_runs == 1
        assert first.deferred_for_fairness == 1

        second = await loop.tick()
        assert second.dispatched_runs == 1
        assert len(producer.sent) == 2

    async def test_no_cap_processes_everything_in_one_tick(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        for i in range(5):
            await trigger_store.upsert(_make_cron_trigger(
                task_id=f"task-{i}", org_id="org-1", next_run_at=now.isoformat(),
            ))

        loop = _make_loop(trigger_store, run_store, producer, clock, per_org_claim_cap=None)
        stats = await loop.tick()

        assert stats.dispatched_runs == 5
        assert stats.deferred_for_fairness == 0


class TestPublishFailureSurvivesViaOutbox:
    async def test_run_row_persists_even_if_publish_fails(self, trigger_store, run_store, producer) -> None:
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock = FixedClock(now)
        await trigger_store.upsert(_make_cron_trigger(next_run_at=now.isoformat()))

        producer.fail_next = True
        loop = _make_loop(trigger_store, run_store, producer, clock)
        stats = await loop.tick()

        # create_if_absent still succeeded (returns True from _dispatch_run
        # regardless of publish outcome) -- the run row exists even though
        # nothing was actually published this tick.
        assert stats.dispatched_runs == 1
        assert producer.sent == []

        runs = await run_store.list_for_task("task-1")
        assert runs.total == 1
        assert runs.items[0].status == RunStatus.PENDING
