"""One background renewer for every distributed lease this process holds.

Each in-flight record used to start its own renewal task, so N concurrent
records meant N tasks each issuing their own Redis renew on every interval.
That put a standing load on Redis proportional to how busy the node was —
exactly when it could least afford it — and, because those tasks ran on the
consumer's *main* loop while the records ran on its worker loop, every renew
also paid a cross-thread hop that could be cancelled by a stalled loop.

``LeaseRenewer`` replaces all of that with a single task that renews the whole
registry in one pipelined round trip. A record registers its leases, gets a
handle, and awaits ``handle.lost`` only if it wants to abort early; losing a
lease sets that handle's event rather than raising into an unrelated task.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )


class LeaseHandle:
    """One record's view of the leases it holds.

    ``lost`` is set when the renewer could not prove this owner still holds
    its leases — either Redis said the lease is gone (another replica has it,
    or it expired), or renewals failed for longer than the safety deadline.
    Either way the caller must stop: the rest of the fleet may have already
    reassigned the work.
    """

    __slots__ = ("owner", "pools", "lost", "reason")

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.pools: set[str] = set()
        self.lost = asyncio.Event()
        self.reason: str | None = None

    def mark_lost(self, reason: str) -> None:
        if not self.lost.is_set():
            self.reason = reason
            self.lost.set()


class LeaseRenewer:
    """Renews every registered lease on one shared interval.

    Single-loop: the renewer runs on the loop that started it, which is the
    same worker loop the records run on, so no cross-thread hop is involved.
    """

    def __init__(
        self,
        logger: "Logger",
        manager: "DistributedConcurrencyManager",
        *,
        lease_seconds: float,
        interval_seconds: float,
        clock: "Callable[[], float]" = time.monotonic,
    ) -> None:
        self._logger = logger
        self._manager = manager
        self._lease_seconds = lease_seconds
        # Never longer than a third of the lease, so a lease survives two
        # consecutive failed rounds before its safety deadline is at risk.
        self._interval = max(0.1, min(interval_seconds, lease_seconds / 3))
        # How long renewals may keep failing before held leases are declared
        # lost. One interval short of the lease itself: past this point Redis
        # would have expired them anyway, so continuing to process would mean
        # working without a lease the rest of the fleet can see.
        self._deadline = max(0.1, lease_seconds - self._interval)
        self._clock = clock
        self._handles: dict[str, LeaseHandle] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_success = clock()

    def register(self, owner: str) -> LeaseHandle:
        handle = self._handles.get(owner)
        if handle is None:
            handle = LeaseHandle(owner)
            self._handles[owner] = handle
        return handle

    def add(self, owner: str, pool: str) -> None:
        self.register(owner).pools.add(pool)

    def discard(self, owner: str, pool: str) -> None:
        handle = self._handles.get(owner)
        if handle is not None:
            handle.pools.discard(pool)

    def unregister(self, owner: str) -> None:
        self._handles.pop(owner, None)

    @property
    def seconds_since_success(self) -> float:
        return self._clock() - self._last_success

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._renew_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A renew round failing is expected during a Redis blip; only
                # the safety deadline below turns it into lost leases.
                self._note_failure(exc)

    async def _renew_once(self) -> None:
        leases = [
            (pool, handle.owner)
            for handle in list(self._handles.values())
            for pool in tuple(handle.pools)
        ]
        if not leases:
            # Nothing held means nothing to prove; keep the clock fresh so an
            # idle stretch doesn't instantly expire the next lease taken.
            self._last_success = self._clock()
            return

        results = await self._manager.renew_many(leases, self._lease_seconds)
        self._last_success = self._clock()

        for (pool, owner), renewed in results.items():
            if renewed:
                continue
            handle = self._handles.get(owner)
            # Only if the handle still claims the pool: a record that released
            # it between building the batch and reading the reply has not lost
            # anything.
            if handle is not None and pool in handle.pools:
                handle.mark_lost(f"Lost distributed {pool} concurrency lease")

    def _note_failure(self, exc: Exception) -> None:
        overdue = self.seconds_since_success
        if overdue < self._deadline:
            self._logger.debug(
                "Lease renewal round failed (%.1fs since last success): %s",
                overdue, exc,
            )
            return
        self._logger.warning(
            "Distributed concurrency leases could not be renewed for %.1fs "
            "(deadline %.1fs); releasing %d holder(s): %s",
            overdue, self._deadline, len(self._handles), exc,
        )
        # Every handle, not just the ones holding an exclusivity pool.
        # Reaching here means the whole round raised, and one round renews
        # every pool of every owner in a single pipeline — so an owner's
        # `record:<id>` lease is exactly as unrenewed as its capacity pools.
        # LeaseKind's fail-open is about *acquiring* a capacity lease under a
        # node-local gate, not about continuing to process a record whose
        # exclusivity lease the fleet can no longer see.
        for handle in list(self._handles.values()):
            handle.mark_lost(
                "Distributed concurrency lease could not be renewed "
                "before its safety deadline"
            )
