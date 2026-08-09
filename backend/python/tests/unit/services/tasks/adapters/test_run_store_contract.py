"""Parameterized contract suite for `ITaskRunStore`. Currently one backend
under test: `RedisRunStore` over `fakeredis`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.domain.models import RunStatus, TaskRun, compute_idempotency_key

if TYPE_CHECKING:
    from app.services.tasks.interface.run_store import ITaskRunStore


def _make_run(**overrides) -> TaskRun:
    now_iso = datetime.now(timezone.utc).isoformat()
    defaults = {
        "task_id": "task-1",
        "org_id": "org-1",
        "idempotency_key": compute_idempotency_key("task-1", now_iso),
        "status": RunStatus.PENDING,
        "created_at": now_iso,
    }
    defaults.update(overrides)
    return TaskRun(**defaults)


@pytest.fixture(params=["redis"])
async def run_store(request: pytest.FixtureRequest) -> "ITaskRunStore":
    if request.param == "redis":
        client = fake_aioredis.FakeRedis(decode_responses=True)
        store = RedisRunStore(client)
        yield store
        await client.aclose()
        return
    raise ValueError(request.param)


class TestCreateIfAbsent:
    async def test_creates_new_run(self, run_store: ITaskRunStore) -> None:
        run = _make_run()
        created = await run_store.create_if_absent(run)
        assert created is not None
        assert created.run_id == run.run_id

    async def test_duplicate_idempotency_key_returns_none(self, run_store: ITaskRunStore) -> None:
        run = _make_run()
        await run_store.create_if_absent(run)
        duplicate = run.model_copy(update={"run_id": "a-different-run-id"})
        result = await run_store.create_if_absent(duplicate)
        assert result is None
        # The original run must still be the one on record.
        fetched = await run_store.get_by_idempotency_key(run.idempotency_key)
        assert fetched.run_id == run.run_id

    async def test_get_by_idempotency_key_missing(self, run_store: ITaskRunStore) -> None:
        assert await run_store.get_by_idempotency_key("no-such-key") is None


class TestGetAndUpdate:
    async def test_get_roundtrip(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        fetched = await run_store.get(run.run_id)
        assert fetched is not None
        assert fetched.status == RunStatus.PENDING

    async def test_get_missing_returns_none(self, run_store: ITaskRunStore) -> None:
        assert await run_store.get("no-such-id") is None

    async def test_update_persists_nested_fields(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        updated = run.model_copy(update={
            "status": RunStatus.SUCCEEDED,
            "completed_steps": ["s1", "s2"],
            "usage": {"tokens": 1234},
        })
        await run_store.update(updated)
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED
        assert fetched.completed_steps == ["s1", "s2"]
        assert fetched.usage == {"tokens": 1234}


class TestClaimForExecution:
    async def test_claims_pending_run(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        claimed = await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30)
        assert claimed is not None
        assert claimed.status == RunStatus.RUNNING
        assert claimed.lease_owner == "worker-1"
        assert claimed.started_at

    async def test_second_claim_of_same_run_fails(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        first = await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30)
        assert first is not None
        second = await run_store.claim_for_execution(run.run_id, owner="worker-2", lease_seconds=30)
        assert second is None

    async def test_cannot_claim_terminal_run(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        await run_store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))
        assert await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30) is None

    async def test_cannot_claim_abandoned_run(self, run_store: ITaskRunStore) -> None:
        """Reviving an ABANDONED run is the caller's explicit retry/DLQ
        decision (see `TaskExecutor`), never this primitive's."""
        run = await run_store.create_if_absent(_make_run())
        await run_store.update(run.model_copy(update={"status": RunStatus.ABANDONED}))
        assert await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30) is None

    async def test_claim_missing_run_returns_none(self, run_store: ITaskRunStore) -> None:
        assert await run_store.claim_for_execution("no-such-run", owner="worker-1", lease_seconds=30) is None

    async def test_claimed_run_removed_from_pending_scan(self, run_store: ITaskRunStore) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        run = await run_store.create_if_absent(_make_run(
            idempotency_key=compute_idempotency_key("task-1", old_ts), created_at=old_ts,
        ))
        await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30)
        stale = await run_store.list_pending(now=datetime.now(timezone.utc), older_than_seconds=60)
        assert stale == []

    async def test_concurrent_claims_only_one_wins(self, run_store: ITaskRunStore) -> None:
        run = await run_store.create_if_absent(_make_run())
        results = await asyncio.gather(*(
            run_store.claim_for_execution(run.run_id, owner=f"worker-{i}", lease_seconds=30)
            for i in range(10)
        ))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1


class TestResumeWithAnswer:
    async def test_transitions_awaiting_input_to_pending_with_answer(self, run_store: ITaskRunStore) -> None:
        run = _make_run(status=RunStatus.AWAITING_INPUT, hil_question_id="q-1")
        await run_store.create_if_absent(run)

        resumed = await run_store.resume_with_answer(run.run_id, answer="Sprint 42")
        assert resumed is not None
        assert resumed.status == RunStatus.PENDING
        assert resumed.pending_answer == "Sprint 42"

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.PENDING
        assert fetched.pending_answer == "Sprint 42"

    async def test_resumed_run_is_claimable_and_reappears_in_pending_scan(self, run_store: ITaskRunStore) -> None:
        """The whole point of returning to PENDING: the ordinary dispatch/
        claim path (`TaskExecutor.handle_dispatch` -> `claim_for_execution`)
        picks it back up with no bespoke resume-claim primitive needed."""
        run = _make_run(status=RunStatus.AWAITING_INPUT)
        await run_store.create_if_absent(run)
        await run_store.resume_with_answer(run.run_id, answer="Sprint 42")

        claimed = await run_store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=30)
        assert claimed is not None
        assert claimed.pending_answer == "Sprint 42"

    async def test_rejects_a_run_that_is_not_awaiting_input(self, run_store: ITaskRunStore) -> None:
        """Stale-answer guard: a run already answered (now PENDING/RUNNING),
        completed, or cancelled must never accept a second/late answer."""
        run = await run_store.create_if_absent(_make_run(status=RunStatus.SUCCEEDED))
        assert await run_store.resume_with_answer(run.run_id, answer="too late") is None
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED
        assert fetched.pending_answer is None

    async def test_rejects_missing_run(self, run_store: ITaskRunStore) -> None:
        assert await run_store.resume_with_answer("no-such-run", answer="x") is None

    async def test_concurrent_answers_exactly_one_winner(self, run_store: ITaskRunStore) -> None:
        """Mirrors `test_concurrent_claims_only_one_wins` -- two users (or
        one user double-clicking) answering the same question at once must
        not both succeed."""
        run = await run_store.create_if_absent(_make_run(status=RunStatus.AWAITING_INPUT))
        results = await asyncio.gather(*(
            run_store.resume_with_answer(run.run_id, answer=f"answer-{i}")
            for i in range(10)
        ))
        winners = [r for r in results if r is not None]
        assert len(winners) == 1


class TestHeartbeat:
    async def test_heartbeat_extends_lease_for_owner(self, run_store: ITaskRunStore) -> None:
        run = _make_run(status=RunStatus.RUNNING, lease_owner="w1", lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        await run_store.create_if_absent(run)
        assert await run_store.heartbeat(run.run_id, "w1", 30) is True

    async def test_heartbeat_fails_for_wrong_owner(self, run_store: ITaskRunStore) -> None:
        run = _make_run(status=RunStatus.RUNNING, lease_owner="w1", lease_expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
        await run_store.create_if_absent(run)
        assert await run_store.heartbeat(run.run_id, "w2", 30) is False

    async def test_heartbeat_fails_for_missing_run(self, run_store: ITaskRunStore) -> None:
        assert await run_store.heartbeat("no-such-run", "w1", 30) is False


class TestReapAbandoned:
    async def test_reaps_running_run_with_expired_lease(self, run_store: ITaskRunStore) -> None:
        run = _make_run(
            status=RunStatus.RUNNING, lease_owner="w1",
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(milliseconds=10)).isoformat(),
        )
        await run_store.create_if_absent(run)
        await asyncio.sleep(0.05)
        reaped = await run_store.reap_abandoned(now=datetime.now(timezone.utc))
        assert len(reaped) == 1
        assert reaped[0].status == RunStatus.ABANDONED

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.ABANDONED
        assert fetched.lease_owner is None

    async def test_does_not_reap_active_lease(self, run_store: ITaskRunStore) -> None:
        run = _make_run(
            status=RunStatus.RUNNING, lease_owner="w1",
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        await run_store.create_if_absent(run)
        reaped = await run_store.reap_abandoned(now=datetime.now(timezone.utc))
        assert reaped == []


class TestListing:
    async def test_list_for_task_pagination(self, run_store: ITaskRunStore) -> None:
        base = datetime.now(timezone.utc)
        for i in range(5):
            ts = (base + timedelta(seconds=i)).isoformat()
            await run_store.create_if_absent(_make_run(
                idempotency_key=compute_idempotency_key("task-1", ts), created_at=ts,
            ))
        page = await run_store.list_for_task("task-1", limit=2, offset=0)
        assert page.total == 5
        assert len(page.items) == 2
        # Most recent first.
        assert page.items[0].created_at >= page.items[1].created_at

    async def test_list_pending_finds_stale_runs(self, run_store: ITaskRunStore) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        await run_store.create_if_absent(_make_run(
            idempotency_key=compute_idempotency_key("task-1", old_ts), created_at=old_ts, status=RunStatus.PENDING,
        ))
        recent_ts = datetime.now(timezone.utc).isoformat()
        await run_store.create_if_absent(_make_run(
            idempotency_key=compute_idempotency_key("task-1", recent_ts), created_at=recent_ts, status=RunStatus.PENDING,
        ))
        stale = await run_store.list_pending(now=datetime.now(timezone.utc), older_than_seconds=60)
        assert len(stale) == 1

    async def test_non_pending_run_excluded_from_pending_list(self, run_store: ITaskRunStore) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        run = await run_store.create_if_absent(_make_run(
            idempotency_key=compute_idempotency_key("task-1", old_ts), created_at=old_ts, status=RunStatus.PENDING,
        ))
        await run_store.update(run.model_copy(update={"status": RunStatus.RUNNING}))
        stale = await run_store.list_pending(now=datetime.now(timezone.utc), older_than_seconds=60)
        assert stale == []

    async def test_list_pending_uses_caller_supplied_now_not_wall_clock(self, run_store: ITaskRunStore) -> None:
        """Regression guard: staleness must be computed against the `now`
        the caller passes in (e.g. a scheduler's injected `IClock`), never
        against real wall-clock time -- otherwise a run created under a
        `FixedClock` set to any time far from the real present would be
        misjudged as stale (or fresh) regardless of the caller's own
        notion of "now"."""
        far_past_ts = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        await run_store.create_if_absent(_make_run(
            idempotency_key=compute_idempotency_key("task-1", far_past_ts), created_at=far_past_ts,
            status=RunStatus.PENDING,
        ))
        # Under a `now` consistent with the run's own creation time, it is
        # NOT yet stale -- even though real wall-clock time is years later.
        consistent_now = datetime(2024, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        assert await run_store.list_pending(now=consistent_now, older_than_seconds=60) == []


class TestListAwaitingEvent:
    """The lookup `fire_event` uses to resume a `ctx.wait_for_event` run.

    Without this index a parked run is unreachable: nothing could correlate an
    incoming event back to the run waiting for it.
    """

    @staticmethod
    async def _park(run_store: "ITaskRunStore", **overrides) -> TaskRun:
        run = await run_store.create_if_absent(_make_run(**overrides))
        parked = run.model_copy(update={
            "status": RunStatus.AWAITING_INPUT,
            "suspension_kind": "wait_for_event",
            "awaiting_event_type": "slack.message.posted",
        })
        await run_store.update(parked)
        return parked

    async def test_finds_a_parked_run(self, run_store: ITaskRunStore) -> None:
        parked = await self._park(run_store)
        found = await run_store.list_awaiting_event("org-1", "slack.message.posted")
        assert [r.run_id for r in found] == [parked.run_id]

    async def test_another_org_cannot_see_it(self, run_store: ITaskRunStore) -> None:
        await self._park(run_store)
        assert await run_store.list_awaiting_event("org-2", "slack.message.posted") == []

    async def test_another_event_type_does_not_match(self, run_store: ITaskRunStore) -> None:
        await self._park(run_store)
        assert await run_store.list_awaiting_event("org-1", "slack.reaction.added") == []

    async def test_a_resumed_run_leaves_the_index(self, run_store: ITaskRunStore) -> None:
        """Otherwise the next event resumes a run that is already running."""
        parked = await self._park(run_store)
        await run_store.resume_with_answer(parked.run_id, answer="{}")
        assert await run_store.list_awaiting_event("org-1", "slack.message.posted") == []


class TestListExpiredSuspensions:
    """Runs parked longer than their journal is kept.

    Resuming one of these replays the workflow against a journal that no
    longer records what already ran, so every completed step happens twice.
    The index exists so they can be failed on purpose instead.
    """

    @staticmethod
    async def _park(run_store: "ITaskRunStore", *, deadline: datetime) -> TaskRun:
        run = await run_store.create_if_absent(_make_run())
        parked = run.model_copy(update={
            "status": RunStatus.AWAITING_INPUT,
            "suspension_kind": "approval",
            "resume_deadline_at": deadline.isoformat(),
        })
        await run_store.update(parked)
        return parked

    async def test_finds_a_run_past_its_deadline(self, run_store: ITaskRunStore) -> None:
        now = datetime.now(timezone.utc)
        parked = await self._park(run_store, deadline=now - timedelta(minutes=1))
        found = await run_store.list_expired_suspensions(now=now)
        assert [r.run_id for r in found] == [parked.run_id]

    async def test_ignores_a_run_still_within_its_deadline(self, run_store: ITaskRunStore) -> None:
        now = datetime.now(timezone.utc)
        await self._park(run_store, deadline=now + timedelta(days=29))
        assert await run_store.list_expired_suspensions(now=now) == []

    async def test_a_run_without_a_deadline_is_never_expired(self, run_store: ITaskRunStore) -> None:
        """Agent-path suspensions have no journal to outlive."""
        run = await run_store.create_if_absent(_make_run())
        await run_store.update(run.model_copy(update={"status": RunStatus.AWAITING_INPUT}))
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        assert await run_store.list_expired_suspensions(now=far_future) == []

    async def test_an_answered_run_leaves_the_index(self, run_store: ITaskRunStore) -> None:
        now = datetime.now(timezone.utc)
        parked = await self._park(run_store, deadline=now - timedelta(minutes=1))
        await run_store.resume_with_answer(parked.run_id, answer="yes")
        assert await run_store.list_expired_suspensions(now=now) == []
