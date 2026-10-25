"""The exclusivity lease a record's processing holds, taken by stages that write what it writes.

The record consumer holds ``record:<recordId>`` for the whole of a record's indexing, the stored
record's write included. A stage that read-modify-writes the stored record takes the same lease,
so a re-index of new content cannot land between its read and its write.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from app.services.distributed.interface import IDistributedLeaseManager
from app.services.messaging.config import messaging_env

_POLL_S = 1.0
# Shortest renewal interval for a held lease (a third of its term otherwise).
_MIN_RENEW_S = 1.0


class RevisionSuperseded(Exception):
    """The record moved to a newer revision: this job's output must not be written."""


class RecordBusy(Exception):
    """The record's lease stayed held for longer than the job could wait."""


class LeaseLost(RecordBusy):
    """The held lease could not be renewed: a re-index may own the record now, so the holder
    stopped before its next write. A ``RecordBusy``, so the job is retried."""


class RecordLeases:
    """Bound when the consumers start, which is when the lease manager exists. Unbound (no
    distributed concurrency), holding only re-checks the revision."""

    def __init__(self) -> None:
        super().__init__()
        self._manager: IDistributedLeaseManager | None = None

    @property
    def bound(self) -> bool:
        return self._manager is not None

    def bind(self, manager: IDistributedLeaseManager | None) -> None:
        self._manager = manager

    async def claim(self, record_id: str) -> str | None:
        """Take ``record_id``'s lease if nobody holds it, for a short check-and-write.

        Returns the holder to give to ``release``, or ``None`` when a live holder has it or there is no lease
        manager to ask. The lease is not renewed: do not keep it for long.
        """
        manager = self._manager
        if manager is None:
            return None
        holder = f"claim:{uuid4().hex[:8]}"
        if await manager.try_acquire(f"record:{record_id}", holder, 1, messaging_env.concurrency_lease_seconds):
            return holder
        return None

    async def release(self, record_id: str, holder: str) -> None:
        manager = self._manager
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.release(f"record:{record_id}", holder)

    @contextlib.asynccontextmanager
    async def hold(
        self,
        record_id: str,
        *,
        owner: str,
        wait_s: float,
        still_current: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[None]:
        """Hold ``record_id``'s lease, with the record still on the job's revision.

        Raises ``RevisionSuperseded`` as soon as it is not (a re-index started while this waited),
        and ``RecordBusy`` when the lease stays held for ``wait_s``. When the held lease is lost
        (``LeaseLost``), the task that entered the hold is cancelled at its next await, so the
        body writes nothing more.
        """
        manager = self._manager
        if manager is None:
            if not await still_current():
                raise RevisionSuperseded(record_id)
            yield
            return
        pool, holder = f"record:{record_id}", f"{owner}:{uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, wait_s)
        while not await manager.try_acquire(pool, holder, 1, messaging_env.concurrency_lease_seconds):
            if not await still_current():
                raise RevisionSuperseded(record_id)
            if loop.time() >= deadline:
                raise RecordBusy(f"record {record_id} stayed busy for {wait_s:.0f}s")
            await asyncio.sleep(_POLL_S)
        # Held across a record's enrichment writes (graph, vectors), which can outlast one lease
        # term: renew it so a re-index cannot take the record while those writes are landing.
        lease_s = messaging_env.concurrency_lease_seconds
        task = asyncio.current_task()
        lost: list[str] = []
        holding = True
        cancel_sent = False

        def on_lost(reason: str) -> None:
            nonlocal cancel_sent
            # Only while the body may still write: past it, the cleanup below must not be cut short.
            if holding and not lost and task is not None:
                lost.append(reason)
                cancel_sent = True
                _ = task.cancel()

        def absorb_own_cancel() -> bool:
            # Called only where a CancelledError was just delivered: it was ours when on_lost sent
            # one and no other cancellation is still counted (uncancel leaves those in place).
            nonlocal cancel_sent
            if not cancel_sent or task is None:
                return False
            cancel_sent = False
            return task.uncancel() == 0

        renewer = asyncio.create_task(_keep_renewed(manager, pool, holder, lease_s, on_lost))
        try:
            if not await still_current():
                raise RevisionSuperseded(record_id)
            yield
        except asyncio.CancelledError:
            if not absorb_own_cancel():
                raise
        finally:
            holding = False
            _ = renewer.cancel()
            try:
                # A loss noticed as the body returned may not have been delivered yet: take it here,
                # at a suspension point that is certain, rather than in the release below.
                _ = await asyncio.wait({renewer})
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                if not absorb_own_cancel():
                    raise
            with contextlib.suppress(Exception):
                await manager.release(pool, holder)
        # Writes may have landed after the lease lapsed, however the body ended: retry the job.
        if lost:
            if cancel_sent and task is not None:
                # The body swallowed the cancellation and ran on: take back the request it consumed.
                _ = task.uncancel()
            raise LeaseLost(f"record {record_id}: {lost[0]}")


async def _keep_renewed(
    manager: IDistributedLeaseManager, pool: str, holder: str, lease_s: float, on_lost: Callable[[str], None]
) -> None:
    """Renew every third of the term; lost when the manager says this holder no longer has the
    lease, or when renewals keep failing until one interval before the term ends (the same rule
    as ``LeaseRenewer``)."""
    interval = max(_MIN_RENEW_S, lease_s / 3)
    deadline = max(interval, lease_s - interval)
    loop = asyncio.get_running_loop()
    last_success = loop.time()
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = await manager.renew(pool, holder, lease_s)
        except Exception as exc:
            if loop.time() - last_success >= deadline:
                on_lost(f"lease renewals kept failing ({exc!r})")
                return
            continue
        if not renewed:
            on_lost("the lease is no longer held")
            return
        last_success = loop.time()
