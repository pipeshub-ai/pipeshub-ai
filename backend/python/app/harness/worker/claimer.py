"""Run slots, with the Mongo claim as the queue (REPORT 6.2.1, D20; P1.16).

A worker holds up to ``slots`` runs at once. It claims the next due run from
Node whenever a slot is free and polls while idle, so runs flow even with the
broker down (O-5). A broker message is only a wake hint: it is acknowledged on
receipt, and a hinted run is claimed by id when a slot is free. Nothing waits
for a run to finish before taking the next message, so a one-partition topic
does not serialize runs (O-4).

The inline claimer in the query service is the same class with role
``inline``; it is woken by Node's hint right after admission. Either role
hears ``run-ready`` (LAT1): Node announces every run that became claimable
after its commit, so an idle claimer polls at once; its own poll backs off
further while it hears them, and stays the backstop for a lost one.

Priority (G-45): Node's queue serves the highest class first. A full worker
holding a background or batch run asks Node for a run of a strictly higher
class, takes it at once in one of ``overcommit`` slots past ``slots``, and asks
its lowest-class run to give its slot back: that run yields ``preempted`` at its
next step boundary and is claimed again later, from its journal. A run is asked
only after ``min_slice_s`` on this worker, so a slot is not passed back and
forth; each claim moves it on by at least a step.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core import org_context
from app.core.org_context import spawn_detached, spawn_in_request
from harness_core.journal.types import (
    PRIORITY_PREEMPTION,
    JournalError,
    JournalUnavailable,
    RunNotClaimable,
)
from harness_core.logs import HarnessLog

# Not app.harness.assembly: the claimer is imported by small processes (the egress proxy's image) that must not
# load the whole run assembly.
from harness_core.models.transports.opik_tracing import (
    maybe_start_conversation_trace,
)
from harness_core.models.transports.opik_tracing import (
    opik_tracing_settings as opik_tracing,
)
from harness_core.priority import (
    PreemptRequests,
    PriorityClass,
    preemptible,
    priority_rank,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from app.services.messaging.config import StreamMessage
    from harness_core.clock import Clock
    from harness_core.driver import RunDriver
    from harness_core.journal.ready import RunReadySignal
    from harness_core.journal.types import AttemptToken, Claim

DISPATCH_EVENT = "agent_run_dispatch_requested"
# The org context a claimed attempt runs in until its assembler narrows it to the person (ADR-SEC-02).
RUN_JOB_SOURCE = "job:harness-run"
MIB = 1024 * 1024
# The warmed idle worker's budget (SLO #16, restated 2026-11-11: it imports what its first run imports) and one
# run's working set, until the P1 load test measures it; slots are a constant formula of the cgroup.
BASE_MIB = 650
PER_RUN_MIB = 64
SLOTS_PER_CPU = 16
MAX_SLOTS = 64
IDLE_POLL_S = 1.0
MAX_IDLE_POLL_S = 5.0
# While run-ready notices arrive, the poll only backstops a lost one (NFR-48).
READY_MAX_IDLE_POLL_S = 15.0
READY_LISTEN_S = 30.0
# SIGTERM to exit (RU-1). A drain finishes the boundary in progress: a model
# call to its commit, a tool batch to its outcomes. Long enough for a streamed
# answer, well inside the app's stop grace; an attempt still short of a
# boundary after it is cancelled and released, so the next worker takes it at
# once instead of after the lease.
DRAIN_S = 30.0
RELEASE_S = 5.0
# G-45: slots past ``slots`` a higher class may take at once while the run it preempts reaches its boundary; 0 turns
# preemption off. Two keep a burst of turns from waiting on one boundary, at two runs' working set (128 MiB).
OVERCOMMIT_ENV = "AGENT_HARNESS_PREEMPT_OVERCOMMIT"
DEFAULT_OVERCOMMIT = 2
# How long a run holds its slot before it may be preempted: long enough for a step or two of a scheduled run.
MIN_SLICE_ENV = "AGENT_HARNESS_PREEMPT_MIN_SLICE_S"
DEFAULT_MIN_SLICE_S = 5.0


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cgroup_memory_bytes() -> int:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        value = _read(path)
        if value and value.isdigit() and int(value) < 1 << 60:
            return int(value)
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def cgroup_cpus() -> float:
    value = _read("/sys/fs/cgroup/cpu.max")
    if value:
        quota, _, period = value.partition(" ")
        if quota.isdigit() and period.isdigit() and int(period) > 0:
            return int(quota) / int(period)
    return float(os.cpu_count() or 1)


def run_slots(memory_bytes: int | None = None, cpus: float | None = None) -> int:
    memory = cgroup_memory_bytes() if memory_bytes is None else memory_bytes
    cpu = cgroup_cpus() if cpus is None else cpus
    by_memory = (memory // MIB - BASE_MIB) // PER_RUN_MIB
    return max(1, min(MAX_SLOTS, by_memory, int(cpu * SLOTS_PER_CPU)))


def preempt_overcommit() -> int:
    """``AGENT_HARNESS_PREEMPT_OVERCOMMIT``: a whole number up to ``MAX_SLOTS``; anything else is the default."""
    raw = os.getenv(OVERCOMMIT_ENV, "").strip()
    return int(raw) if raw.isdigit() and int(raw) <= MAX_SLOTS else DEFAULT_OVERCOMMIT


def preempt_min_slice_s() -> float:
    """``AGENT_HARNESS_PREEMPT_MIN_SLICE_S``: seconds, 0 to 3600; anything else is the default."""
    raw = os.getenv(MIN_SLICE_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MIN_SLICE_S
    return value if 0 <= value <= 3600 else DEFAULT_MIN_SLICE_S


class ClaimJournal(Protocol):
    async def claim(
        self,
        run_id: str | None = None,
        *,
        worker_id: str,
        org_id: str | None = None,
        above: PriorityClass | None = None,
    ) -> Claim | None:
        """``above`` (G-45) is passed only by a preempting claimer, and only to a journal whose claims named
        ``priority_preemption``; a journal without preemption never sees it."""
        ...

    async def release(self, token: AttemptToken, *, reason: str) -> None: ...

    def forget(self, token: AttemptToken) -> None:
        """Drops what the journal keeps for an attempt that left this worker for good."""
        ...


class RunClaimer:
    def __init__(
        self,
        *,
        journal: ClaimJournal,
        driver: RunDriver,
        worker_id: str,
        slots: int,
        clock: Clock,
        role: str = "dispatched",
        idle_poll_s: float = IDLE_POLL_S,
        max_idle_poll_s: float = MAX_IDLE_POLL_S,
        drain_s: float = DRAIN_S,
        release_s: float = RELEASE_S,
        ready: RunReadySignal | None = None,
        ready_max_idle_poll_s: float = READY_MAX_IDLE_POLL_S,
        sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
        preempt: PreemptRequests | None = None,
        overcommit: int = DEFAULT_OVERCOMMIT,
        min_slice_s: float = DEFAULT_MIN_SLICE_S,
    ) -> None:
        """``preempt``: shared with the driver's kernels, which yield the runs asked in it (G-45); unset, no run is
        preempted and a full worker only waits for a slot."""
        if slots < 1:
            raise ValueError("a claimer needs at least one slot")
        self._journal = journal
        self._driver = driver
        self._worker_id = worker_id
        self._slots = slots
        self._clock = clock
        # The idle poll and the drain deadline wait on this, so a test drives them with its clock.
        self._sleep = sleep
        self._log = HarnessLog.named("app.harness.worker", workerId=worker_id, role=role)
        self._idle_poll_s = idle_poll_s
        self._max_idle_poll_s = max_idle_poll_s
        self._ready = ready
        self._ready_max_idle_poll_s = ready_max_idle_poll_s
        self._listen_task: asyncio.Task[None] | None = None
        self._listening = ready is not None
        self._busy = 0
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()
        self._stopping = False
        self._loop_task: asyncio.Task[None] | None = None
        self._drain_s = drain_s
        self._release_s = release_s
        self._drain_task: asyncio.Task[None] | None = None
        # Claims in flight: a drain waits for them, so no run starts after it counted.
        self._claiming = 0
        self._claims_settled = asyncio.Event()
        self._claims_settled.set()
        # Clear while the poll's claim is at Node: it may be claiming the run a hint names (PERFC-F4).
        self._poll_settled = asyncio.Event()
        self._poll_settled.set()
        # Once draining: how each attempt ended, by run.
        self._outcomes: dict[str, str] | None = None
        self._preempt = preempt
        self._overcommit = overcommit if preempt is not None else 0
        self._min_slice_s = min_slice_s
        # What each running attempt's claim named (G-45): its class, when it started here, and whether an engine runs
        # it (an engine yields only before a turn after one it drove, and comes back through a new sandbox).
        self._held: dict[str, tuple[PriorityClass | None, float, bool]] = {}
        # The Node of the latest claim takes ``above`` and ``preempted`` (its capabilities named them).
        self._node_preempts = False

    @property
    def free(self) -> int:
        return self._slots - self._busy

    @property
    def running(self) -> tuple[str, ...]:
        return tuple(self._runs)

    def start(self) -> None:
        if self._loop_task is None:
            self._log.info("slots.start", slots=self._slots)
            self._loop_task = spawn_detached(self._loop(), name=f"claimer-{self._worker_id}")
            if self._ready is not None:
                self._listen_task = spawn_detached(
                    self._listen(self._ready), name=f"ready-{self._worker_id}"
                )

    def wake(self) -> None:
        self._wake.set()

    async def claim_run(self, org_id: str, run_id: str) -> bool:
        """A hinted run, claimed by id if a slot is free; otherwise the queue has it.

        The poll's claim in flight is answered first: it is usually for this very run (Node's notice woke the
        poll before the broker delivered the hint), and a second claim of it would only be refused."""
        _ = await self._poll_settled.wait()
        if run_id in self._runs:
            self._log.debug("slots.hint_deferred", orgId=org_id, runId=run_id, reason="running", free=self.free)
            return False
        if self._stopping or self.free <= 0:
            self._log.debug(
                "slots.hint_deferred",
                orgId=org_id,
                runId=run_id,
                reason="stopping" if self._stopping else "full",
                free=self.free,
            )
            self.wake()
            return False
        return await self._claim(run_id, org_id)

    def begin_drain(self) -> asyncio.Task[None]:
        """Starts the drain (idempotent): no new claims, running attempts yield at their next boundary."""
        if self._drain_task is None:
            self._stopping = True
            self._driver.request_drain()
            if self._listen_task is not None:
                _ = self._listen_task.cancel()
            self.wake()
            self._drain_task = spawn_detached(self._drain(), name=f"drain-{self._worker_id}")
        return self._drain_task

    async def stop(self) -> None:
        await self.begin_drain()

    async def _drain(self) -> None:
        started = self._clock.monotonic()
        deadline = spawn_in_request(self._sleep(self._drain_s))
        self._outcomes = {}
        seen = set(self._runs)
        self._log.info("slots.drain", running=len(seen), drainS=self._drain_s)
        try:
            # The poll loop exits at its next check; a claim it has in flight is
            # answered and released unassembled by the draining driver.
            if self._loop_task is not None:
                await self._settle(self._loop_task, deadline)
            settled = spawn_in_request(self._claims_settled.wait())
            try:
                _ = await self._until({settled}, deadline)
            finally:
                _ = settled.cancel()
            seen.update(self._runs)
            pending = await self._until(set(self._runs.values()), deadline)
        finally:
            _ = deadline.cancel()
        for task in pending:
            _ = task.cancel()
        if pending:
            _ = await asyncio.wait(pending, timeout=self._release_s + 1)
        seen.update(self._outcomes)
        outcomes = collections.Counter(self._outcomes.values())
        self._log.info(
            "worker.drain",
            running=len(seen),
            ms=round((self._clock.monotonic() - started) * 1000),
            yielded=outcomes["yielded"],
            released=outcomes["released"],
            ended=sum(n for status, n in outcomes.items() if status not in ("yielded", "released")),
            timedOut=len(pending),
        )

    @staticmethod
    async def _until(waits: set[asyncio.Future[Any]], deadline: asyncio.Future[None]) -> set[asyncio.Future[Any]]:
        """Waits for all of ``waits`` or for ``deadline``, whichever is first; returns those still pending."""
        pending = set(waits)
        while pending and not deadline.done():
            done, _ = await asyncio.wait(pending | {deadline}, return_when=asyncio.FIRST_COMPLETED)
            pending -= done
        return pending

    async def _settle(self, task: asyncio.Task[None], deadline: asyncio.Future[None]) -> None:
        if await self._until({task}, deadline):
            _ = task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _longest_idle_poll_s(self) -> float:
        if self._listening and self._ready is not None and self._ready.healthy:
            return self._ready_max_idle_poll_s
        return self._max_idle_poll_s

    async def _listen(self, ready: RunReadySignal) -> None:
        while not self._stopping:
            try:
                if await ready.wait(READY_LISTEN_S):
                    self.wake()
            except Exception as exc:  # a broken listener must leave the poll in charge, not end it
                self._listening = False
                self._log.error("slots.ready_failed", exc=exc, errorType=type(exc).__name__)
                return

    async def _loop(self) -> None:
        delay = self._idle_poll_s
        while not self._stopping:
            try:
                victim = self._victim() if self.free <= 0 else None
                if (self.free > 0 or victim is not None) and await self._claim(None, None, victim):
                    delay = self._idle_poll_s
                    continue
            except Exception as exc:  # this task is the worker's only poller: one failed poll must not end it
                self._log.error("slots.poll_failed", exc=exc, errorType=type(exc).__name__)
            if await self._woken_within(delay):
                delay = self._idle_poll_s
            else:
                delay = min(delay * 2, self._longest_idle_poll_s())
            self._wake.clear()

    async def _woken_within(self, delay: float) -> bool:
        wake = spawn_in_request(self._wake.wait())
        timer = spawn_in_request(self._sleep(delay))
        try:
            _ = await asyncio.wait({wake, timer}, return_when=asyncio.FIRST_COMPLETED)
            return wake.done()
        finally:
            _ = wake.cancel()
            _ = timer.cancel()

    def _victim(self) -> tuple[str, PriorityClass] | None:
        """The run a higher class would take the slot of: the lowest class first, a native-loop run before an engine
        run of that class, then the longest held. ``None``
        when no overcommit slot is left, Node cannot take ``above``, or no preemptible run has held its slice."""
        if (
            self._preempt is None
            or not self._node_preempts
            or self._stopping
            or self._busy >= self._slots + self._overcommit
        ):
            return None
        now = self._clock.monotonic()
        held: list[tuple[str, PriorityClass, float, bool]] = [
            (run_id, cls, since, engine)
            for run_id, (cls, since, engine) in self._held.items()
            if cls is not None
            and preemptible(cls)
            and not self._preempt.requested(run_id)
            and now - since >= self._min_slice_s
        ]
        if not held:
            return None
        run_id, cls, _, _ = max(held, key=lambda h: (priority_rank(h[1]), not h[3], -h[2]))
        return run_id, cls

    async def _claim(
        self, run_id: str | None, org_id: str | None, victim: tuple[str, PriorityClass] | None = None
    ) -> bool:
        self._claiming += 1
        self._claims_settled.clear()
        try:
            return await self._claim_once(run_id, org_id, victim)
        finally:
            self._claiming -= 1
            if self._claiming == 0:
                self._claims_settled.set()

    async def _claim_once(
        self, run_id: str | None, org_id: str | None, victim: tuple[str, PriorityClass] | None = None
    ) -> bool:
        via = "poll" if run_id is None else "hint"
        sent_at = self._clock.suspend_aware()
        self._busy += 1
        claim: Claim | None = None
        started = False
        if run_id is None:
            self._poll_settled.clear()
        try:
            claim = await self._request(run_id, org_id, via, above=victim[1] if victim is not None else None)
            if claim is not None:
                self._node_preempts = claim.journal_accepts(PRIORITY_PREEMPTION)
                engine = getattr(claim.spec.executor, "engine", None) is not None
                self._held[claim.run_id] = (claim.priority_class, self._clock.monotonic(), engine)
                # Detached: the run binds the org its claim names, never the poll's or the hint consumer's.
                self._runs[claim.run_id] = spawn_detached(self._run(claim, sent_at), name=f"run-{claim.run_id}")
                started = True
                if victim is not None and self._preempt is not None:
                    self._preempt.request(victim[0])
        finally:
            if run_id is None:
                self._poll_settled.set()
            if not started:
                self._busy -= 1
        if claim is None:
            if run_id is None:
                self._log.debug("slots.poll_empty", free=self.free, above=victim[1] if victim else None)
            return False
        self._log.info(
            "slots.claimed",
            orgId=claim.org_id,
            runId=claim.run_id,
            attemptId=claim.attempt_id,
            fence=claim.fence,
            via=via,
            free=self.free,
            slots=self._slots,
            priorityClass=claim.priority_class,
        )
        if victim is not None:
            self._log.info(
                "slots.preempt",
                runId=victim[0],
                priorityClass=victim[1],
                forRunId=claim.run_id,
                forClass=claim.priority_class,
            )
        return True

    async def _request(
        self, run_id: str | None, org_id: str | None, via: str, *, above: PriorityClass | None = None
    ) -> Claim | None:
        """The journal's answer; a refusal or an answer this worker cannot use is no claim."""
        try:
            if above is None:
                claim = await self._journal.claim(run_id, worker_id=self._worker_id, org_id=org_id)
            else:
                claim = await self._journal.claim(
                    run_id, worker_id=self._worker_id, org_id=org_id, above=above
                )
        except RunNotClaimable:
            self._log.debug("slots.not_claimable", orgId=org_id, runId=run_id, via=via)
            return None
        except JournalUnavailable as exc:
            self._log.warning("slots.claim_unavailable", runId=run_id, via=via, errorType=type(exc).__name__)
            return None
        except JournalError as exc:
            self._log.warning("slots.claim_refused", runId=run_id, via=via, errorType=type(exc).__name__)
            return None
        except Exception as exc:  # an adapter bug must cost one claim, never the slot or the poller
            self._log.error("slots.claim_failed", exc=exc, runId=run_id, via=via, errorType=type(exc).__name__)
            return None
        return claim

    async def _run(self, claim: Claim, sent_at: float) -> None:
        log = self._log.bind(
            orgId=claim.org_id,
            runId=claim.run_id,
            attemptId=claim.attempt_id,
            fence=claim.fence,
        )
        status = "crashed"
        traced, project = opik_tracing()
        # Every org-scoped read of the attempt acts for the run's org (ADR-SEC-02); the assembler narrows it to the person.
        bound = org_context.push(org_context.OrgContext(claim.org_id, None, "job", RUN_JOB_SOURCE, claim.run_id))
        try:
            with maybe_start_conversation_trace(
                enabled=traced, org_id=claim.org_id, conversation_id=claim.session_id, run_id=claim.run_id,
                project_name=project,
            ):
                disposition = await self._driver.run_claimed(claim, claim_sent_at=sent_at)
            status = disposition.status
            log.info("slots.run_done", status=disposition.status, reason=disposition.reason)
        except asyncio.CancelledError:
            status = await self._release_cancelled(claim, log)
            raise
        except Exception as exc:  # the driver settles runs; this guards the slot
            log.error("slots.run_crashed", exc=exc, errorType=type(exc).__name__)
        finally:
            if self._outcomes is not None:
                self._outcomes[claim.run_id] = status
            self._journal.forget(claim.token)
            self._runs.pop(claim.run_id, None)
            self._held.pop(claim.run_id, None)
            if self._preempt is not None:
                self._preempt.clear(claim.run_id)
            self._busy -= 1
            org_context.pop(bound)
            self.wake()

    async def _release_cancelled(self, claim: Claim, log: HarnessLog) -> str:
        """The drain ran out before this attempt reached a boundary: hand the run back now."""
        try:
            await asyncio.wait_for(
                self._journal.release(claim.token, reason="drain_timeout"), self._release_s
            )
        except (JournalError, TimeoutError) as exc:
            log.warning("run.release_failed", reason="drain_timeout", errorType=type(exc).__name__)
            return "lost"
        log.info("run.release", reason="drain_timeout")
        return "released"


class DispatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    command_id: str = Field(alias="commandId", min_length=1, max_length=128)
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    org_id: str = Field(alias="orgId", min_length=1, max_length=128)
    kind: Literal["run_ready", "run_resume", "run_cancel"]
    created_at: int = Field(alias="createdAt", ge=0)


class DispatchHints:
    """Broker handler: every message is acknowledged on receipt (it is only a hint)."""

    def __init__(self, claimer: RunClaimer) -> None:
        self._claimer = claimer

    async def handle(self, message: StreamMessage) -> bool:
        if message.eventType != DISPATCH_EVENT:
            return True
        try:
            hint = DispatchPayload.model_validate(message.payload)
        except ValidationError as exc:
            HarnessLog.named("app.harness.worker").warning(
                "hint.invalid", errors=len(exc.errors()), requestId=message.requestId
            )
            return True
        HarnessLog.named(
            "app.harness.worker", orgId=hint.org_id, runId=hint.run_id, requestId=message.requestId
        ).debug("hint.received", kind=hint.kind, commandId=hint.command_id)
        if hint.kind == "run_cancel":
            # The attempt holding the run sees run.cancel_requested at its next boundary.
            return True
        await self._claimer.claim_run(hint.org_id, hint.run_id)
        return True


__all__ = [
    "DEFAULT_MIN_SLICE_S",
    "DEFAULT_OVERCOMMIT",
    "DISPATCH_EVENT",
    "MIN_SLICE_ENV",
    "OVERCOMMIT_ENV",
    "DispatchHints",
    "DispatchPayload",
    "RunClaimer",
    "cgroup_cpus",
    "cgroup_memory_bytes",
    "preempt_min_slice_s",
    "preempt_overcommit",
    "run_slots",
]
