"""Idempotent acquisition, lease fencing and keep-warm bookkeeping every broker adapter shares."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from harness_core.sandbox.errors import LeaseFenced, SandboxNotFound

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.sandbox.types import LeaseRef


def key_digest(idempotency_key: str) -> str:
    """Stable, label-safe form of a caller's key (keys may be long or contain anything)."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]


class EntryState(StrEnum):
    HELD = "held"
    WARM = "warm"


@dataclass
class LedgerEntry:
    sandbox_id: str
    key: str
    holder: str
    fence: int
    state: EntryState = EntryState.HELD
    warm_until: float | None = None


class AcquireLedger:
    """Which sandbox a key names, who holds it, and until when a released one stays warm.

    Process-local: an adapter that must survive its own restart re-seeds an
    entry from the sandbox's labels (``adopt``).
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._by_key: dict[str, LedgerEntry] = {}
        self._by_id: dict[str, LedgerEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def by_key(self, key: str) -> LedgerEntry | None:
        return self._by_key.get(key)

    def by_id(self, sandbox_id: str) -> LedgerEntry | None:
        return self._by_id.get(sandbox_id)

    def held(self, sandbox_id: str) -> LedgerEntry:
        entry = self._by_id.get(sandbox_id)
        if entry is None or entry.state is not EntryState.HELD:
            raise SandboxNotFound(f"sandbox {sandbox_id} is not held")
        return entry

    def admit(self, entry: LedgerEntry, lease: LeaseRef) -> None:
        """Hand ``entry`` to ``lease``: same or higher fence adopts, lower is refused."""
        if lease.fence < entry.fence:
            raise LeaseFenced(
                f"sandbox {entry.sandbox_id} is held at fence {entry.fence}; fence {lease.fence} is stale",
                reason="stale_fence",
            )
        entry.holder = lease.holder
        entry.fence = lease.fence
        entry.state = EntryState.HELD
        entry.warm_until = None

    def record(self, key: str, sandbox_id: str, lease: LeaseRef) -> LedgerEntry:
        entry = LedgerEntry(sandbox_id=sandbox_id, key=key, holder=lease.holder, fence=lease.fence)
        self._by_key[key] = entry
        self._by_id[sandbox_id] = entry
        return entry

    def keep_warm(self, sandbox_id: str, keep_warm_s: int) -> None:
        entry = self._by_id.get(sandbox_id)
        if entry is not None:
            entry.state = EntryState.WARM
            entry.warm_until = self._clock.monotonic() + keep_warm_s

    def expired_warm(self) -> list[LedgerEntry]:
        now = self._clock.monotonic()
        return [
            entry
            for entry in self._by_id.values()
            if entry.state is EntryState.WARM and entry.warm_until is not None and entry.warm_until <= now
        ]

    def forget(self, sandbox_id: str) -> LedgerEntry | None:
        entry = self._by_id.pop(sandbox_id, None)
        if entry is not None and self._by_key.get(entry.key) is entry:
            del self._by_key[entry.key]
            lock = self._locks.get(entry.key)
            if lock is not None and not lock.locked():
                del self._locks[entry.key]
        return entry

    def active(self) -> int:
        return len(self._by_id)


__all__ = ["AcquireLedger", "EntryState", "LedgerEntry", "key_digest"]
