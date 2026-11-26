"""Ports the durable kernel uses to reach the journal, checkpoints, blobs and the live bus.

Adapters: ``journal.memory`` (reference, in-process) and, from P1a S5, a client
of Node's ``/internal/harness/*`` routes. Both must pass
``harness_core.testing.contracts.journal``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from harness_core.journal.children import ChildAdmitted, ChildRequest
    from harness_core.journal.types import (
        AttemptToken,
        Checkpoint,
        Claim,
        CommitReceipt,
        CommitRequest,
        ContentRef,
        JournalEvent,
        LeaseGrant,
        LiveFrame,
    )
    from harness_core.priority import PriorityClass


class JournalPort(Protocol):
    async def claim(
        self,
        run_id: str | None = None,
        *,
        worker_id: str,
        org_id: str | None = None,
        above: PriorityClass | None = None,
    ) -> Claim | None:
        """Claim ``run_id`` (or the next claimable run): attempts+1, fence+1, DB-time lease.

        ``org_id`` names the tenant of ``run_id`` where the adapter needs it
        (the Node journal looks a run up by org). Returns ``None`` when nothing
        is claimable. The returned ``last_seq`` is the stream head at claim
        time; a fold that reads less is stale. The next run is the highest
        priority class's (G-45); ``above``: only a run of a strictly higher
        class than it, for a full worker that will preempt one of that class.
        """
        ...

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        """Append ``request.events`` (and the transition's ``run.*`` event) atomically.

        Fenced by ``request.token`` and the lease; a retried ``command_id``
        returns the stored receipt without writing again.
        """
        ...

    async def read(
        self, stream_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[JournalEvent]: ...

    async def last_seq(self, stream_id: str) -> int: ...

    async def renew(self, token: AttemptToken) -> LeaseGrant:
        """Extend the lease. Never takes the commit lock; raises ``OwnershipLost`` on 404/409."""
        ...

    async def release(self, token: AttemptToken, *, reason: str) -> None:
        """Give the run back to the queue without a transition (e.g. an incompatible fold)."""
        ...


class ChildRunPort(Protocol):
    """Node's child admission (P6.1), asked with the parent's attempt.

    Idempotent on the parent run and ``request.invocation_id``: asking again
    answers the same child (``created`` false) and writes nothing. Node writes
    ``child.spawned`` on the parent's stream in the admission's transaction.
    Raises ``ChildRefused`` (nothing written) or ``OwnershipLost``.
    """

    async def spawn(self, token: AttemptToken, request: ChildRequest) -> ChildAdmitted: ...


class ContentStorePort(Protocol):
    async def put(self, data: bytes) -> ContentRef: ...

    async def get(self, ref: ContentRef) -> bytes:
        """Raise ``ContentMissing`` when the blob is gone or fails its hash/size check."""
        ...


@runtime_checkable
class SealingContentStore(Protocol):
    """A content store that can keep a payload out of its ref on request: what a receiver refused inline is
    stored again this way and committed again (CHILDREF)."""

    async def put_sealed(self, data: bytes) -> ContentRef: ...


class CheckpointPort(Protocol):
    async def save(self, checkpoint: Checkpoint) -> str: ...

    async def load(self, ref: str) -> Checkpoint | None: ...


class LiveSinkPort(Protocol):
    async def publish(self, frame: LiveFrame) -> None:
        """Best effort; failures never block execution."""
        ...


class LiveBusPort(LiveSinkPort, Protocol):
    """The LiveBus (REPORT 6.2.4): a bounded, expiring, per-run frame buffer.

    Adapters: ``journal.memory.InMemoryLiveBus`` (single process, tests) and
    ``app.harness.livebus`` (Redis Streams). Both pass
    ``harness_core.testing.contracts.livebus``. Consumers (Node's projector)
    read the same buffer and drop frames from a lower fence.
    """

    async def read(self, run_id: str) -> list[LiveFrame]:
        """The run's retained frames, oldest first."""
        ...


__all__ = [
    "CheckpointPort",
    "ChildRunPort",
    "ContentStorePort",
    "JournalPort",
    "LiveBusPort",
    "LiveSinkPort",
    "SealingContentStore",
]
