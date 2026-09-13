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


class RevisionSuperseded(Exception):
    """The record moved to a newer revision: this job's output must not be written."""


class RecordBusy(Exception):
    """The record's lease stayed held for longer than the job could wait."""


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
        and ``RecordBusy`` when the lease stays held for ``wait_s``.
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
        try:
            if not await still_current():
                raise RevisionSuperseded(record_id)
            yield
        finally:
            with contextlib.suppress(Exception):
                await manager.release(pool, holder)
