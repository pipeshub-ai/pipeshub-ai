"""Parameterized contract suite for `ITriggerStore` -- any future adapter
proves itself by passing these same tests. Currently one backend under
test: `RedisTriggerStore` over `fakeredis` (Lua-scripting via `lupa`, see
`fakeredis[lua]` dev dependency).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.tasks.adapters.redis.trigger_store import RedisTriggerStore
from app.services.tasks.domain.models import TaskTrigger, TriggerKind

if TYPE_CHECKING:
    from app.services.tasks.interface.trigger_store import ITriggerStore


def _make_trigger(**overrides) -> TaskTrigger:
    defaults = {
        "task_id": "task-1",
        "org_id": "org-1",
        "kind": TriggerKind.CRON,
        "cron_expression": "0 9 * * *",
        "timezone": "UTC",
    }
    defaults.update(overrides)
    return TaskTrigger(**defaults)


@pytest.fixture(params=["redis"])
async def trigger_store(request: pytest.FixtureRequest) -> "ITriggerStore":
    if request.param == "redis":
        client = fake_aioredis.FakeRedis(decode_responses=True)
        store = RedisTriggerStore(client)
        yield store
        await client.aclose()
        return
    raise ValueError(request.param)


class TestUpsertAndGet:
    async def test_roundtrip(self, trigger_store: ITriggerStore) -> None:
        trig = _make_trigger()
        await trigger_store.upsert(trig)
        fetched = await trigger_store.get(trig.trigger_id)
        assert fetched is not None
        assert fetched.cron_expression == "0 9 * * *"
        assert fetched.task_id == "task-1"

    async def test_get_missing_returns_none(self, trigger_store: ITriggerStore) -> None:
        assert await trigger_store.get("no-such-id") is None


class TestListForTasks:
    """The bulk read behind the workflow list page.

    A list of 50 workflows would otherwise be 50 sequential per-task reads,
    and the page needs triggers to say whether anything will run again.
    """

    async def test_groups_triggers_under_their_own_task(
        self, trigger_store: ITriggerStore,
    ) -> None:
        a1 = await trigger_store.upsert(_make_trigger(task_id="task-a"))
        a2 = await trigger_store.upsert(_make_trigger(task_id="task-a"))
        b1 = await trigger_store.upsert(_make_trigger(task_id="task-b"))

        found = await trigger_store.list_for_tasks(["task-a", "task-b"])

        assert {t.trigger_id for t in found["task-a"]} == {a1.trigger_id, a2.trigger_id}
        assert [t.trigger_id for t in found["task-b"]] == [b1.trigger_id]

    async def test_omits_a_task_with_no_triggers(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(task_id="task-a"))
        found = await trigger_store.list_for_tasks(["task-a", "task-empty"])
        assert "task-empty" not in found

    async def test_no_task_ids_reads_nothing(self, trigger_store: ITriggerStore) -> None:
        assert await trigger_store.list_for_tasks([]) == {}

    async def test_matches_list_for_task_field_for_field(
        self, trigger_store: ITriggerStore,
    ) -> None:
        """The bulk path decodes hashes itself, so it can drift from the
        single-task path it is replacing."""
        await trigger_store.upsert(_make_trigger(
            task_id="task-a", kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "slack.message.posted"},
        ))

        single = await trigger_store.list_for_task("task-a")
        bulk = (await trigger_store.list_for_tasks(["task-a"]))["task-a"]

        assert [t.model_dump() for t in bulk] == [t.model_dump() for t in single]

    async def test_list_for_task(self, trigger_store: ITriggerStore) -> None:
        t1 = await trigger_store.upsert(_make_trigger(task_id="task-a"))
        t2 = await trigger_store.upsert(_make_trigger(task_id="task-a"))
        await trigger_store.upsert(_make_trigger(task_id="task-b"))
        triggers = await trigger_store.list_for_task("task-a")
        assert {t.trigger_id for t in triggers} == {t1.trigger_id, t2.trigger_id}

    async def test_delete(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger())
        assert await trigger_store.delete(trig.trigger_id) is True
        assert await trigger_store.get(trig.trigger_id) is None
        assert await trigger_store.delete(trig.trigger_id) is False

    async def test_delete_for_task(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(task_id="task-a"))
        await trigger_store.upsert(_make_trigger(task_id="task-a"))
        count = await trigger_store.delete_for_task("task-a")
        assert count == 2
        assert await trigger_store.list_for_task("task-a") == []


class TestClaimDue:
    async def test_claims_due_trigger(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        trig = await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        claimed = await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)
        assert len(claimed) == 1
        assert claimed[0].trigger_id == trig.trigger_id
        assert claimed[0].lease_owner == "owner-1"

    async def test_does_not_claim_future_trigger(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        await trigger_store.upsert(_make_trigger(next_run_at=(now + timedelta(hours=1)).isoformat()))
        claimed = await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)
        assert claimed == []

    async def test_does_not_reclaim_leased_trigger(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        first = await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)
        assert len(first) == 1
        second = await trigger_store.claim_due(now=now, owner="owner-2", limit=10, lease_seconds=30)
        assert second == []

    async def test_respects_limit(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        for _ in range(5):
            await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        claimed = await trigger_store.claim_due(now=now, owner="owner-1", limit=3, lease_seconds=30)
        assert len(claimed) == 3

    async def test_concurrent_claims_exactly_one_winner(self, trigger_store: ITriggerStore) -> None:
        """N schedulers racing to claim the SAME due trigger -- exactly one
        must win. This is the load-bearing correctness property of the
        whole scheduling design (Part J of the plan)."""
        now = datetime.now(timezone.utc)
        trig = await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))

        results = await asyncio.gather(*[
            trigger_store.claim_due(now=now, owner=f"owner-{i}", limit=1, lease_seconds=30)
            for i in range(20)
        ])
        winners = [r for r in results if r]
        assert len(winners) == 1
        assert winners[0][0].trigger_id == trig.trigger_id

    async def test_complete_claim_reschedules(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        trig = await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)

        next_time = (now + timedelta(hours=1)).isoformat()
        await trigger_store.complete_claim(trigger_id=trig.trigger_id, owner="owner-1", next_run_at=next_time)

        fetched = await trigger_store.get(trig.trigger_id)
        assert fetched.lease_owner is None
        # Not claimable now (rescheduled to the future).
        assert await trigger_store.claim_due(now=now, owner="owner-2", limit=10, lease_seconds=30) == []
        # But claimable once due again.
        future_claim = await trigger_store.claim_due(
            now=now + timedelta(hours=2), owner="owner-2", limit=10, lease_seconds=30
        )
        assert len(future_claim) == 1

    async def test_complete_claim_with_none_next_run_at_stops_scheduling(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        trig = await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)
        await trigger_store.complete_claim(trigger_id=trig.trigger_id, owner="owner-1", next_run_at=None)

        future_claim = await trigger_store.claim_due(
            now=now + timedelta(days=365), owner="owner-2", limit=10, lease_seconds=30
        )
        assert future_claim == []

    async def test_complete_claim_is_noop_for_wrong_owner(self, trigger_store: ITriggerStore) -> None:
        """If a reaper already reclaimed the trigger (lease expired), the
        original worker's late `complete_claim` must not stomp state."""
        now = datetime.now(timezone.utc)
        trig = await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=0.001)
        await asyncio.sleep(0.05)
        await trigger_store.reap_expired_leases(now=datetime.now(timezone.utc))
        reclaimed = await trigger_store.claim_due(now=datetime.now(timezone.utc), owner="owner-2", limit=10, lease_seconds=30)
        assert len(reclaimed) == 1

        # owner-1's stale complete_claim arrives late -- must not affect owner-2's claim.
        await trigger_store.complete_claim(
            trigger_id=trig.trigger_id, owner="owner-1", next_run_at=(now + timedelta(hours=1)).isoformat()
        )
        fetched = await trigger_store.get(trig.trigger_id)
        assert fetched.lease_owner == "owner-2"


class TestReapExpiredLeases:
    async def test_reaps_expired_lease(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=0.001)
        await asyncio.sleep(0.05)
        reaped = await trigger_store.reap_expired_leases(now=datetime.now(timezone.utc))
        assert reaped == 1

    async def test_does_not_reap_active_lease(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat()))
        await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=300)
        reaped = await trigger_store.reap_expired_leases(now=now)
        assert reaped == 0


class TestDisabledAndOneTimeExhaustion:
    async def test_disabled_trigger_never_claimable(self, trigger_store: ITriggerStore) -> None:
        now = datetime.now(timezone.utc)
        await trigger_store.upsert(_make_trigger(next_run_at=(now - timedelta(minutes=1)).isoformat(), enabled=False))
        claimed = await trigger_store.claim_due(now=now, owner="owner-1", limit=10, lease_seconds=30)
        assert claimed == []


class TestGetByWebhookId:
    async def test_finds_trigger_by_webhook_id(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.WEBHOOK, cron_expression=None, webhook_id="wh-1",
        ))
        found = await trigger_store.get_by_webhook_id("wh-1")
        assert found is not None
        assert found.trigger_id == trig.trigger_id

    async def test_unknown_webhook_id_returns_none(self, trigger_store: ITriggerStore) -> None:
        assert await trigger_store.get_by_webhook_id("no-such-webhook") is None

    async def test_deleting_trigger_removes_webhook_index(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.WEBHOOK, cron_expression=None, webhook_id="wh-2",
        ))
        await trigger_store.delete(trig.trigger_id)
        assert await trigger_store.get_by_webhook_id("wh-2") is None

    async def test_deleting_by_task_removes_webhook_index(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(
            task_id="task-wh", kind=TriggerKind.WEBHOOK, cron_expression=None, webhook_id="wh-3",
        ))
        await trigger_store.delete_for_task("task-wh")
        assert await trigger_store.get_by_webhook_id("wh-3") is None


class TestListByEventType:
    async def test_finds_matching_enabled_triggers(self, trigger_store: ITriggerStore) -> None:
        t1 = await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "record.created"},
        ))
        t2 = await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "record.created", "connectorId": "conn-1"},
        ))
        found = await trigger_store.list_by_event_type("org-1", "record.created")
        assert {t.trigger_id for t in found} == {t1.trigger_id, t2.trigger_id}

    async def test_does_not_match_other_event_types(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "record.created"},
        ))
        found = await trigger_store.list_by_event_type("org-1", "record.deleted")
        assert found == []

    async def test_does_not_match_other_orgs(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(
            org_id="org-1", kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "record.created"},
        ))
        found = await trigger_store.list_by_event_type("org-2", "record.created")
        assert found == []

    async def test_disabled_trigger_excluded(self, trigger_store: ITriggerStore) -> None:
        await trigger_store.upsert(_make_trigger(
            kind=TriggerKind.EVENT, cron_expression=None,
            event_filter={"event_type": "record.created"}, enabled=False,
        ))
        found = await trigger_store.list_by_event_type("org-1", "record.created")
        assert found == []


class TestClaimFire:
    _FIRE_AT = "2026-01-01T00:00:00+00:00"

    async def test_unlimited_trigger_always_claims(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger(max_runs=None))
        counts = [
            await trigger_store.claim_fire(
                trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token=f"d{i}",
            )
            for i in range(3)
        ]
        assert counts == [1, 2, 3]

    async def test_claim_stops_at_max_runs(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger(max_runs=2))
        claims = [
            await trigger_store.claim_fire(
                trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token=f"d{i}",
            )
            for i in range(3)
        ]
        assert claims == [1, 2, None]

    async def test_claim_records_the_fire_time(self, trigger_store: ITriggerStore) -> None:
        trig = await trigger_store.upsert(_make_trigger(max_runs=1))
        await trigger_store.claim_fire(trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token="d")
        refetched = await trigger_store.get(trig.trigger_id)
        assert refetched is not None
        assert refetched.run_count == 1
        assert refetched.last_fire_at == self._FIRE_AT

    async def test_missing_trigger_cannot_be_claimed(self, trigger_store: ITriggerStore) -> None:
        assert await trigger_store.claim_fire(
            "no-such-id", fire_at=self._FIRE_AT, dedupe_token="d",
        ) is None

    async def test_a_redelivered_token_does_not_consume_a_second_slot(
        self, trigger_store: ITriggerStore,
    ) -> None:
        trig = await trigger_store.upsert(_make_trigger(max_runs=1))
        first = await trigger_store.claim_fire(
            trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token="delivery-1",
        )
        repeat = await trigger_store.claim_fire(
            trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token="delivery-1",
        )
        assert first == repeat == 1
        refetched = await trigger_store.get(trig.trigger_id)
        assert refetched is not None
        assert refetched.run_count == 1

    async def test_concurrent_claims_do_not_exceed_max_runs(
        self, trigger_store: ITriggerStore,
    ) -> None:
        """The reason `claim_fire` exists: read-check-then-increment would let
        every one of these observe `run_count == 0` and fire."""
        trig = await trigger_store.upsert(_make_trigger(max_runs=3))
        results = await asyncio.gather(*(
            trigger_store.claim_fire(
                trig.trigger_id, fire_at=self._FIRE_AT, dedupe_token=f"d{i}",
            )
            for i in range(10)
        ))
        granted = [count for count in results if count is not None]
        assert sorted(granted) == [1, 2, 3]
