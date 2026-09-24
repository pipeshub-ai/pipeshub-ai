"""The one thing that decides whether a sync may run, and holds it while it does.

Admission used to be spread across five places — the dispatcher's `is_running`,
the lease, a capacity check, `start_if_idle`, and the consumer's capacity gate —
of which only the lease was authoritative. The other four were approximations,
and the gaps between them produced real defects: a status written after the lease
was released, and an `ignore_local_task` flag whose only purpose was to stop a
finalizer declining the request it was handing back.

So there is one question, `begin()`, answered in one place. Capacity is decided
inside it, in the same critical section that grants the lease, which is what
removes the acquire-check-release window entirely.

The task handle lives on the lease. An `asyncio.Task` cannot cross a process
boundary, so something local must hold it — but it is a *cancellation handle*,
never an input to the admission decision. Tying its lifetime to the lease's is
what keeps that distinction from eroding again.

`LocalSyncCoordinator` is the implementation here: one sync worker, so nothing
outside this process needs excluding and the in-process registry is already
exact. It is installed through `app.edition_services.create_coordinator`, so a
build with a different admission rule supplies its own without touching this
module. Nothing here imports redis.
"""

import asyncio
import logging
import os
import socket
import time
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from app.connectors.core.sync.task_manager import SyncTaskManager

_LEASE_PREFIX = "pipeshub:sync:lease:"
_STOP_PREFIX = "pipeshub:sync:stop:"


def executor_identity() -> str:
    """A consumer name that is stable across restarts.

    The Redis Streams pending-entries list is keyed by consumer name, so a
    PID-based id orphans this consumer's in-flight entries on every restart and
    `XAUTOCLAIM` will not reclaim them: it skips same-name entries.
    """
    # The index is appended even to an explicit id. Setting
    # CONNECTOR_SYNC_EXECUTOR_ID used to short-circuit before it, which gave
    # every process in one container the same name, and a shared name means
    # XAUTOCLAIM can never reclaim their pending work.
    index = os.getenv("CONNECTOR_SYNC_EXECUTOR_INDEX", "0")
    explicit = os.getenv("CONNECTOR_SYNC_EXECUTOR_ID")
    if explicit:
        return f"{explicit}-{index}"
    host = os.getenv("HOSTNAME") or socket.gethostname()
    return f"{host}-sync-exec-{index}"


def process_identity() -> str:
    """Unique per process, for anything that claims ownership.

    `executor_identity` is deliberately *stable*, which makes it identical for
    every process in one container: they share a HOSTNAME and index. An
    ownership check against it would therefore have each of them conclude it
    owns the thing. Ownership has to distinguish processes, so this appends the
    pid; the stable name stays available for the Redis Streams consumer, whose
    pending-entries list must survive a restart.
    """
    return f"{executor_identity()}-p{os.getpid()}"


def max_concurrent_syncs() -> int:
    """How many syncs one process may run at once.

    Per process, so the deployment-wide ceiling is workers x this. Parsed on
    every call rather than at import so a test can move it, but a malformed
    value must not silently remove the limit — see `_safe_limit`.
    """
    return max(1, int(os.getenv("CONNECTOR_SYNC_MAX_CONCURRENT", "8")))


def _safe_limit(logger: logging.Logger) -> int:
    """The limit, or the default if the environment says something absurd.

    A bad value used to travel a long way: `at_capacity` caught it and answered
    "not at capacity", silently removing the limit altogether. Falling back to
    the default and saying so is the only behaviour that is wrong in neither
    direction.
    """
    try:
        return max_concurrent_syncs()
    except (TypeError, ValueError):
        logger.error(
            "CONNECTOR_SYNC_MAX_CONCURRENT=%r is not a number; using 8",
            os.getenv("CONNECTOR_SYNC_MAX_CONCURRENT"),
        )
        return 8


def _now_ms() -> int:
    return int(time.time() * 1000)


class Admission(Enum):
    """Why a sync may or may not start. Four answers, not a bool.

    The caller does genuinely different things with each: GRANTED runs it,
    AT_CAPACITY queues it, HELD_ELSEWHERE records intent for the current owner to
    hand back, and REFUSED_BY_STOP drops it because the user asked for exactly
    that. Collapsing any two of them loses a decision the caller has to make.
    """

    GRANTED = "granted"
    HELD_ELSEWHERE = "held_elsewhere"
    REFUSED_BY_STOP = "refused_by_stop"
    AT_CAPACITY = "at_capacity"


@dataclass
class SyncLease:
    """One connector's claim on being synced, for the lifetime of that sync.

    `lost` and `stop_requested` may be set from another thread via
    `call_soon_threadsafe`, so they must be created on the loop that awaits them.

    `task` is the running sync, once one has been spawned. It is here so the
    claim and the handle share a lifetime; nothing may consult it to decide
    whether a sync is allowed to start.
    """

    connector_id: str
    token: str
    acquired_at_ms: int
    org_id: str | None = None
    task: asyncio.Task | None = None
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def lease_key(self) -> str:
        return f"{_LEASE_PREFIX}{self.connector_id}"

    @property
    def stop_key(self) -> str:
        return f"{_STOP_PREFIX}{self.connector_id}"

    async def wait_aborted(self) -> None:
        """Resolve as soon as the sync must stop, for either reason."""
        waiters = [
            asyncio.ensure_future(self.lost.wait()),
            asyncio.ensure_future(self.stop_requested.wait()),
        ]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()


class LocalSyncCoordinator:
    """One process, so a dict is an exact answer.

    `begin` declines only on capacity. Everything that would need to see other
    processes is deliberately inert: `peek_many` answers "nothing is live",
    which is not the same as knowing nothing is, so `reports_liveness` is False
    and callers that would act destructively on that answer must check it.
    """

    reports_liveness = False

    _TOKEN = "local"

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.instance_id = process_identity()
        self.ttl_sec = 0
        self.heartbeat_sec = 0
        self._held: dict[str, SyncLease] = {}
        self._tasks = SyncTaskManager(label="Sync")

    async def begin(
        self,
        connector_id: str,
        *,
        org_id: str | None = None,
        message_ts_ms: int | None = None,
    ) -> tuple[Admission, SyncLease | None]:
        if connector_id in self._held:
            return Admission.HELD_ELSEWHERE, None
        if len(self._held) >= _safe_limit(self.logger):
            return Admission.AT_CAPACITY, None
        lease = SyncLease(
            connector_id=connector_id,
            token=self._TOKEN,
            acquired_at_ms=_now_ms(),
            org_id=org_id,
        )
        self._held[connector_id] = lease
        return Admission.GRANTED, lease

    async def end(self, lease: SyncLease) -> bool:
        if self._held.get(lease.connector_id) is lease:
            del self._held[lease.connector_id]
        self._tasks.deregister(lease.connector_id, lease.task)
        return True

    async def cancel_and_wait(self, connector_id: str) -> None:
        await self._tasks.cancel_sync(connector_id)

    async def spawn(
        self, lease: SyncLease, coro: Coroutine
    ) -> asyncio.Task | None:
        task = await self._tasks.start_if_idle(lease.connector_id, coro)
        lease.task = task
        return task

    def is_running_here(self, connector_id: str) -> bool:
        # `_held` as well as `_tasks`: begin() takes the lease, but spawn() only
        # happens after _ensure_connector has done OAuth and HTTP. Asking only
        # about tasks answers "nothing is running" for seconds after admission,
        # which silently dropped a Stop issued in that window.
        return connector_id in self._held or self._tasks.is_running(connector_id)

    async def is_running(self, connector_id: str) -> bool:
        return self.is_running_here(connector_id)

    async def peek(self, connector_id: str) -> str | None:
        """One process, so "running elsewhere" is always None."""
        return None

    def running_count(self) -> int:
        return len(self._held)

    async def request_stop(self, connector_id: str) -> bool:
        if self._tasks.request_stop(connector_id):
            return True
        # Admitted but not yet spawned: there is no task to cancel, so signal the
        # lease instead. run_sync_task waits on this and aborts before doing any
        # work, which is what the user asked for.
        lease = self._held.get(connector_id)
        if lease is not None:
            lease.stop_requested.set()
            return True
        return False

    async def peek_many(self, connector_ids: Iterable[str]) -> set[str]:
        return set()

    async def try_claim_once(self, name: str, ttl_ms: int) -> bool:
        """Always granted: one process, so there is no one to race."""
        return True

    def active_keys(self) -> list[str]:
        return self._tasks.active_keys()

    async def cancel_all(self) -> None:
        await self._tasks.cancel_all()

    async def stop(self) -> None:
        return None


class SyncCoordinator(Protocol):
    """What the start path needs, whichever edition supplies it.

    A Protocol rather than a union: an alternative implementation may not be
    importable in this build, so a union naming it would not resolve.
    """

    #: False when the coordinator cannot tell a live sync from a stale status,
    #: which anything acting destructively on `peek_many` has to check first.
    reports_liveness: bool

    async def begin(
        self,
        connector_id: str,
        *,
        org_id: str | None = None,
        message_ts_ms: int | None = None,
    ) -> tuple[Admission, "SyncLease | None"]: ...
    async def end(self, lease: "SyncLease") -> bool: ...
    async def spawn(
        self, lease: "SyncLease", coro: Coroutine
    ) -> "asyncio.Task | None": ...
    def is_running_here(self, connector_id: str) -> bool: ...
    async def is_running(self, connector_id: str) -> bool: ...
    def running_count(self) -> int: ...
    async def request_stop(self, connector_id: str) -> bool: ...
    async def cancel_and_wait(self, connector_id: str) -> None: ...
    async def peek_many(self, connector_ids: Iterable[str]) -> set[str]: ...
    async def try_claim_once(self, name: str, ttl_ms: int) -> bool: ...
    def active_keys(self) -> list[str]: ...
    async def cancel_all(self) -> None: ...
    async def stop(self) -> None: ...


class _Registry:
    """Process-wide coordinator, set once at startup by whichever entrypoint runs."""

    coordinator: SyncCoordinator | None = None


def set_coordinator(coordinator: SyncCoordinator) -> None:
    _Registry.coordinator = coordinator


def get_coordinator() -> SyncCoordinator | None:
    """The process's coordinator, or None before startup has wired one."""
    return _Registry.coordinator
