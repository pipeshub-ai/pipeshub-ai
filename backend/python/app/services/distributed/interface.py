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
