"""Abstractions for cluster-wide lease management and retry tracking.

``DistributedConcurrencyManager`` (leases) and ``RetryManager`` (retry
counts) are the Redis-backed implementations; the indexing consumers and
``MessagingFactory`` depend on these interfaces so a future non-Redis
implementation (or an EE one with different semantics) is a drop-in.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class IDistributedLeaseManager(ABC):
    """Cluster-wide expiring leases, used to cap concurrency and enforce
    mutual exclusion across replicas (see ``LeaseKind`` in
    ``distributed_concurrency.py`` for which pools need which)."""

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def cleanup(self) -> None: ...

    @abstractmethod
    async def try_acquire(
        self, pool: str, owner: str, limit: int, lease_seconds: float
    ) -> bool: ...

    @abstractmethod
    async def renew(self, pool: str, owner: str, lease_seconds: float) -> bool: ...

    @abstractmethod
    async def renew_many(
        self, leases: Sequence[tuple[str, str]], lease_seconds: float
    ) -> dict[tuple[str, str], bool]: ...

    @abstractmethod
    async def release(self, pool: str, owner: str) -> None: ...


class IRetryTracker(ABC):
    """Persistent, cross-restart retry-count tracking for message consumers."""

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def increment_and_check(
        self, message_id: str, max_attempts: int
    ) -> tuple[int, bool]: ...

    @abstractmethod
    async def get_count(self, message_id: str) -> int: ...

    @abstractmethod
    async def clear(self, message_id: str) -> None: ...

    @abstractmethod
    async def clear_batch(self, message_ids: list[str]) -> int: ...

    @abstractmethod
    async def has_pending_retries(self, message_ids: list[str]) -> bool: ...

    @abstractmethod
    async def cleanup(self) -> None: ...

    async def record_delivery(self, message_id: str) -> int:
        """Count one hand-back of *message_id* without an attempt and return the total so far.

        A message re-queued because it never got a parse slot fails nothing,
        so the failure counter cannot stop it coming back for ever; this
        counter does. Trackers that cannot count deliveries return 0, which
        leaves the backstop disarmed rather than tripping it.
        """
        return 0

    async def clear_deliveries(self, message_id: str) -> None:
        """Start the delivery count of *message_id* over, keeping its failure count."""

    async def record_handback(self, message_id: str) -> int:
        """Count one hand-back of *message_id* by stale-record recovery and return the total.

        Its own counter: consumers count deliveries under the same id before
        each run, and recovery counts a hand-back only once its event is
        published. Cleared with the others. Trackers that cannot count
        hand-backs return 0.
        """
        return 0

    async def get_handbacks(self, message_id: str) -> int:
        """Hand-backs counted for *message_id*; 0 for a tracker that does not count them."""
        return 0

    async def unfinished_attempts(self, work_key: str) -> int:
        """Attempts at *work_key* that started and have not finished.

        Keyed by the unit of work (a record), not the message: a record that
        crashes the process comes back on several messages (the claimed
        original, stale recovery's re-publish). The failure counter cannot
        see these, since a crash skips the code that writes it. Trackers that
        cannot count attempts return 0.
        """
        return 0

    async def start_attempt(self, work_key: str) -> int:
        """Mark an attempt at *work_key* as started; returns ``unfinished_attempts`` before it."""
        return 0

    async def finish_attempt(self, work_key: str, *, settled: bool) -> None:
        """Mark one attempt at *work_key* finished. ``settled``: nothing will run it
        again (indexed, or given up on), so every earlier unfinished attempt is
        forgotten with it."""
