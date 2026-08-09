"""`SchedulerLoop`: Phase 3's tick / claim / dispatch / reschedule engine.

Runs as an asyncio background task inside the Query service process.
Safe to run N instances concurrently across replicas -- `ITriggerStore
.claim_due`'s lease IS the distributed lock (see that method's docstring),
so no separate leader-election is needed for the scheduler itself.

Each `tick()`:

  1. **Reap.** `ITriggerStore.reap_expired_leases` reclaims triggers whose
     claiming scheduler crashed mid-tick (held a lease, never called
     `complete_claim`); `_republish_stale_pending` reclaims runs whose
     scheduler crashed between creating the run row and publishing its
     dispatch event. Both are cheap bounded scans, run FIRST on every
     tick so a lease that just expired becomes reclaimable in this SAME
     tick's claim step rather than waiting a full extra tick interval.
  2. **Claim.** `ITriggerStore.claim_due()` atomically claims up to
     `claim_batch_size` due triggers, tagging each with this loop's
     `owner` id as lease holder.
  3. **Fairness.** At most `per_org_claim_cap` triggers from any one org
     are processed per tick (`_apply_fairness`); any excess is released
     back immediately via `complete_claim` with the trigger's OWN
     (unchanged) `next_run_at`, so it is immediately reclaimable on the
     very next tick rather than starved for a full lease period -- a
     noisy org can never crowd out the others for more than one tick.
  4. **Dispatch.** Each surviving trigger is expanded into one or more
     fire times via `ScheduleCalculator.apply_misfire_policy` (SKIP -> a
     single "now"; RUN_ONCE -> the single missed boundary; RUN_ALL ->
     every missed boundary, capped). Each fire time becomes exactly one
     `TaskRun`, created idempotently (`ITaskRunStore.create_if_absent`,
     keyed by `compute_idempotency_key(task_id, fire_time)`) and
     published to `Topic.TASK_EVENTS`. Outbox-lite: the run row is
     written BEFORE the publish, so a crash between the two just leaves a
     PENDING run that `_republish_stale_pending` (step 1, on a later
     tick) picks up rather than silently losing the fire.
  5. **Reschedule.** `run_count`/`last_fire_at` are persisted
     (`ITriggerStore.upsert`) and the lease is released with the newly
     computed `next_run_at` (`ITriggerStore.complete_claim`) -- the two
     calls are ordered so `complete_claim`'s write of `next_run_at` is
     never clobbered by the `upsert`.

A trigger whose per-tick processing raises is left claimed (its lease
simply expires and `reap_expired_leases` reclaims it on a later tick) --
never `complete_claim`d with a possibly-wrong `next_run_at` computed from
a partially-failed dispatch. Every write this loop performs
(`create_if_absent`, `upsert`, `complete_claim`) is itself idempotent or
lease-gated, so retrying a trigger's processing from scratch after a
partial failure is always safe.

This loop never decides HOW a run executes -- that is `runtime/executor.py`
(Phase 4), which consumes `Topic.TASK_EVENTS` and takes over the run's
lease from the moment it is dispatched. This loop's only write to a
`TaskRun` is its creation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.services.messaging.config import Topic
from app.services.tasks.domain.models import TaskRun, compute_idempotency_key
from app.services.tasks.domain.schedule_calculator import ScheduleCalculator
from app.services.tasks.interface.clock import SystemClock

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.tasks.domain.models import TaskTrigger
    from app.services.tasks.interface.clock import IClock
    from app.services.tasks.interface.run_store import ITaskRunStore
    from app.services.tasks.interface.trigger_store import ITriggerStore

__all__ = ["SchedulerLoop", "SchedulerTickStats"]

_TASK_RUN_DISPATCH_EVENT = "task_run_dispatch"


@dataclass(frozen=True)
class SchedulerTickStats:
    """Return value of one `tick()` call -- deliberately returned rather
    than only logged, so tests and callers (e.g. a `/health` endpoint
    reporting scheduler liveness) can assert on it directly."""

    claimed: int = 0
    deferred_for_fairness: int = 0
    dispatched_runs: int = 0
    duplicate_runs_skipped: int = 0
    reaped_leases: int = 0
    republished_pending: int = 0
    errors: int = 0


class SchedulerLoop:
    """One instance per Query service process. Carries no per-tick state
    between calls other than its own identity (`owner`) -- every other
    piece of state (`next_run_at`, lease ownership, run rows) lives in the
    injected stores, which is what makes running many instances safe."""

    def __init__(
        self,
        *,
        trigger_store: "ITriggerStore",
        run_store: "ITaskRunStore",
        producer: "IMessagingProducer",
        clock: "IClock | None" = None,
        calculator: ScheduleCalculator | None = None,
        owner: str | None = None,
        tick_interval_seconds: float = 5.0,
        claim_batch_size: int = 50,
        lease_seconds: float = 30.0,
        per_org_claim_cap: int | None = 10,
        stale_pending_after_seconds: float = 60.0,
        stale_pending_batch_size: int = 100,
        logger: "Logger | None" = None,
    ) -> None:
        self._trigger_store = trigger_store
        self._run_store = run_store
        self._producer = producer
        self._clock: IClock = clock or SystemClock()
        self._calculator = calculator or ScheduleCalculator()
        self._owner = owner or f"scheduler-{uuid.uuid4()}"
        self._tick_interval_seconds = tick_interval_seconds
        self._claim_batch_size = claim_batch_size
        self._lease_seconds = lease_seconds
        self._per_org_claim_cap = per_org_claim_cap
        self._stale_pending_after_seconds = stale_pending_after_seconds
        self._stale_pending_batch_size = stale_pending_batch_size
        self._logger = logger or logging.getLogger(__name__)
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def owner(self) -> str:
        return self._owner

    def start(self) -> None:
        """Idempotent -- a second call while already running is a no-op,
        matching `sandbox/artifact_cleanup.py`'s `start_cleanup_task`
        convention for background loops in this codebase."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("Unhandled error in scheduler tick (owner=%s)", self._owner)
            try:
                await asyncio.sleep(self._tick_interval_seconds)
            except asyncio.CancelledError:
                break

    async def tick(self) -> SchedulerTickStats:
        now = self._clock.now()
        reaped_leases = await self._trigger_store.reap_expired_leases(now=now)
        republished = await self._republish_stale_pending(now=now)

        claimed = await self._trigger_store.claim_due(
            now=now, owner=self._owner, limit=self._claim_batch_size, lease_seconds=self._lease_seconds,
        )
        active, deferred = self._apply_fairness(claimed)
        for trigger in deferred:
            await self._trigger_store.complete_claim(
                trigger_id=trigger.trigger_id, owner=self._owner, next_run_at=trigger.next_run_at,
            )

        dispatched = 0
        duplicates = 0
        errors = 0
        for trigger in active:
            try:
                created, skipped = await self._process_claimed_trigger(trigger, claimed_at=now)
            except Exception:
                errors += 1
                self._logger.exception(
                    "Failed to process claimed trigger %s (task %s); leaving lease to expire for reap",
                    trigger.trigger_id, trigger.task_id,
                )
            else:
                dispatched += created
                duplicates += skipped

        return SchedulerTickStats(
            claimed=len(claimed),
            deferred_for_fairness=len(deferred),
            dispatched_runs=dispatched,
            duplicate_runs_skipped=duplicates,
            reaped_leases=reaped_leases,
            republished_pending=republished,
            errors=errors,
        )

    def _apply_fairness(self, claimed: list["TaskTrigger"]) -> tuple[list["TaskTrigger"], list["TaskTrigger"]]:
        if self._per_org_claim_cap is None:
            return claimed, []
        counts: dict[str, int] = {}
        active: list[TaskTrigger] = []
        deferred: list[TaskTrigger] = []
        for trigger in claimed:
            count = counts.get(trigger.org_id, 0)
            if count < self._per_org_claim_cap:
                counts[trigger.org_id] = count + 1
                active.append(trigger)
            else:
                deferred.append(trigger)
        return active, deferred

    async def _process_claimed_trigger(self, trigger: "TaskTrigger", *, claimed_at: datetime) -> tuple[int, int]:
        fire_times = self._calculator.apply_misfire_policy(trigger, claimed_at=claimed_at)
        created_count = 0
        duplicate_count = 0
        for fire_time in fire_times:
            if await self._dispatch_run(trigger, fire_time):
                created_count += 1
            else:
                duplicate_count += 1

        # `run_count`/`last_fire_at` are persisted via `upsert` BEFORE
        # `complete_claim` -- `complete_claim` is the sole authority on the
        # `next_run_at` hash field going forward (it also releases the
        # lease atomically), so ordering it last means this upsert can
        # never clobber a fresher reschedule.
        updated_trigger = trigger.model_copy(update={
            "run_count": trigger.run_count + len(fire_times),
            "last_fire_at": fire_times[-1] if fire_times else trigger.last_fire_at,
        })
        await self._trigger_store.upsert(updated_trigger)
        next_run_at = self._calculator.next_run_at(updated_trigger, after=claimed_at)
        await self._trigger_store.complete_claim(
            trigger_id=trigger.trigger_id, owner=self._owner, next_run_at=next_run_at,
        )
        return created_count, duplicate_count

    async def _dispatch_run(self, trigger: "TaskTrigger", fire_time: str) -> bool:
        run = TaskRun(
            task_id=trigger.task_id,
            trigger_id=trigger.trigger_id,
            org_id=trigger.org_id,
            idempotency_key=compute_idempotency_key(trigger.task_id, fire_time),
            scheduled_for=fire_time,
            created_at=self._clock.now().astimezone(timezone.utc).isoformat(),
        )
        created = await self._run_store.create_if_absent(run)
        if created is None:
            # Same (task_id, fire_time) already has a run -- either a
            # previous claim of this trigger dispatched it (this claim is
            # a re-processing after a crash/reap) or a concurrent
            # scheduler beat us to it. Either way there is exactly one
            # `TaskRun` for this fire time, which is the idempotency
            # guarantee this method exists to provide; if ITS dispatch
            # event never made it out, `_republish_stale_pending` is what
            # re-publishes it, not this path.
            return False
        published = await self._publish_dispatch(created)
        if not published:
            self._logger.warning(
                "Publish failed for run %s (task %s) after creation; will be republished once stale",
                created.run_id, created.task_id,
            )
        return True

    async def _publish_dispatch(self, run: "TaskRun") -> bool:
        return await self._producer.send_event(
            topic=Topic.TASK_EVENTS.value,
            event_type=_TASK_RUN_DISPATCH_EVENT,
            payload={
                "run_id": run.run_id,
                "task_id": run.task_id,
                "trigger_id": run.trigger_id,
                "org_id": run.org_id,
                "scheduled_for": run.scheduled_for,
                "idempotency_key": run.idempotency_key,
            },
            key=run.task_id,
        )

    async def _republish_stale_pending(self, *, now: datetime) -> int:
        stale = await self._run_store.list_pending(
            now=now, older_than_seconds=self._stale_pending_after_seconds, limit=self._stale_pending_batch_size,
        )
        count = 0
        for run in stale:
            try:
                if await self._publish_dispatch(run):
                    count += 1
            except Exception:
                self._logger.exception("Failed to republish stale pending run %s", run.run_id)
        return count
