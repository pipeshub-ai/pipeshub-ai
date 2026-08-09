"""`ITaskRunStore` -- persistence port for `TaskRun`.

The reference adapter is Redis-backed (`adapters/redis/run_store.py`):
heartbeats are a write-heavy hot path, idempotency is a single atomic
create-if-absent, and reaping abandoned runs is a sorted-set scan by lease
expiry. Nothing in this module or its callers may assume a Redis backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from app.services.tasks.domain.models import Page, TaskRun


class ITaskRunStore(ABC):
    @abstractmethod
    async def create_if_absent(self, run: TaskRun) -> TaskRun | None:
        """Atomic create-if-absent keyed by `run.idempotency_key`. Returns
        the created run on success, or `None` if a run with that
        idempotency key already exists (the caller should fetch and use
        the existing run instead of treating this as an error) -- this is
        what makes dispatching the same (task_id, fire_time) pair twice
        produce exactly one run."""
        ...

    @abstractmethod
    async def get(self, run_id: str) -> TaskRun | None:
        ...

    @abstractmethod
    async def get_by_idempotency_key(self, idempotency_key: str) -> TaskRun | None:
        ...

    @abstractmethod
    async def update(self, run: TaskRun, *, expected_owner: str | None = None) -> TaskRun | None:
        """Full replace by `run_id`.

        Pass `expected_owner` from any caller that believes it holds the run's
        lease: the write then applies only if the stored `lease_owner` still
        matches, and returns `None` otherwise. Without it a worker whose lease
        was reaped mid-run would happily overwrite the state written by the
        worker that reclaimed the run, resurrecting a run the reaper already
        retried or dead-lettered. Callers that legitimately write outside a
        lease (creation, reaping) omit it."""
        ...

    @abstractmethod
    async def claim_for_execution(self, run_id: str, *, owner: str, lease_seconds: float) -> TaskRun | None:
        """Atomically transition a PENDING run to RUNNING under `owner`'s
        lease, setting `started_at` on first claim only. Returns the
        claimed run, or `None` if it was not claimable (already RUNNING
        under an active lease, or in any other -- including terminal --
        status). The one-time PENDING -> RUNNING transition is the
        exactly-once execution guarantee: a redelivered or outbox-republished
        dispatch event for a run some other worker already claimed (or
        already finished) is a safe no-op here, never a double-execution.
        Deliberately does NOT claim an ABANDONED run -- reviving one is an
        explicit decision (retry vs DLQ) made by the caller's own recovery
        logic (see `runtime/executor.py`), not something this generic
        primitive should do silently."""
        ...

    @abstractmethod
    async def resume_with_answer(self, run_id: str, *, answer: str) -> TaskRun | None:
        """Atomically transitions an AWAITING_INPUT run back to PENDING,
        stashing `answer` in `pending_answer` for `TaskExecutor` to inject
        via `Agent.resume(hil_responses=...)` on its next claim (keyed by
        the run's own `hil_question_id`). Returns `None` -- a safe "stale
        answer" signal, never raises -- if the run was not currently
        AWAITING_INPUT: already answered by a concurrent request,
        cancelled, or otherwise moved on since the question was asked.
        Does NOT itself publish a dispatch event; the caller (`TaskEngine
        .answer_run`) does that only after seeing a non-None result."""
        ...

    @abstractmethod
    async def heartbeat(self, run_id: str, owner: str, lease_seconds: float) -> bool:
        """Extend the lease on `run_id` if `owner` still holds it. Returns
        False if the lease was lost (expired and possibly reaped/reclaimed
        by another worker) -- the caller must treat False as "stop work
        immediately, another worker may already be retrying this run."""
        ...

    @abstractmethod
    async def reap_abandoned(self, *, now: datetime) -> list[TaskRun]:
        """Find every RUNNING run whose lease has expired without a
        heartbeat (its worker crashed), mark it ABANDONED, and return the
        list of runs reaped so the caller (the executor's reaper loop) can
        decide whether to retry each one."""
        ...

    @abstractmethod
    async def list_for_task(self, task_id: str, *, limit: int = 50, offset: int = 0) -> Page[TaskRun]:
        ...

    @abstractmethod
    async def list_pending(self, *, now: datetime, older_than_seconds: float, limit: int = 100) -> list[TaskRun]:
        """Runs stuck in PENDING for longer than `older_than_seconds` as of
        `now`, without ever being picked up by a worker (the outbox-lite
        re-publish path) -- distinct from `reap_abandoned`, which handles
        runs that started but whose worker died mid-execution. `now` is
        caller-supplied (mirroring `reap_abandoned`'s own `now` parameter)
        rather than read from the wall clock here, so a caller driven by an
        injected `IClock` (e.g. `SchedulerLoop`, or any test using
        `FixedClock`) gets a `now`-consistent staleness cutoff instead of
        one silently pinned to real time."""
        ...

    @abstractmethod
    async def list_expired_suspensions(self, *, now: datetime, limit: int = 100) -> list[TaskRun]:
        """AWAITING_INPUT runs whose `resume_deadline_at` has passed.

        These can no longer be resumed safely -- their execution journal has
        aged out, so replaying them would re-run steps the first attempt
        already completed. The reaper fails them so they stop presenting
        themselves as answerable."""
        ...

    @abstractmethod
    async def list_awaiting_event(self, org_id: str, event_type: str) -> list[TaskRun]:
        """Runs parked on `ctx.wait_for_event(event_type)` in `org_id`.

        The lookup `fire_event` needs to resume a suspended run. Scoped by org
        as well as type: an event delivered to one tenant must not resume
        another's run."""
        ...
