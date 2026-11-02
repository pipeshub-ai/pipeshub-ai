"""Process-wide resource governance for sandbox instances.

Prevents unbounded sandbox creation from OOMing the host: each
``SandboxManager`` (one per request) calls ``acquire()`` and ``release()``
against a single shared ``SandboxResourceGovernor`` instance. A metered
backend holds its slot from provisioning to destroy; any other backend
holds one only while a call runs (``SandboxManager.slot()``).

The instance has to be shared to mean anything. Per-request limits
(``SandboxLimits.max_concurrent``) bound one chat; only a process-wide
governor bounds the host when N chats run at once, so both
``build_coding_sandbox_manager()`` and ``ControlPlane.start()`` take theirs
from ``get_default_governor()`` rather than constructing one.

Phase 1: in-memory counters behind an ``asyncio.Lock``. A multi-instance
deployment gets per-process caps; a KV-backed governor via
``KeyValueStoreFactory`` is a Phase 4 option, not a hard dependency.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded

__all__ = [
    "GovernorLimits",
    "GovernorLease",
    "SandboxResourceGovernor",
    "SandboxLimitExceeded",
    "get_default_governor",
    "reset_default_governor",
]

logger = logging.getLogger(__name__)


class GovernorLimits(BaseModel):
    """``None`` on either cap means unlimited for that dimension."""

    max_total_sandboxes: int | None = 50
    max_per_org: int | None = 10


@dataclass
class GovernorLease:
    """Opaque token returned by ``acquire()``; pass to ``release()``."""

    governor: SandboxResourceGovernor
    org_id: str | None = None
    released: bool = field(default=False, repr=False)

    async def release(self) -> None:
        await self.governor.release(self)


class SandboxResourceGovernor:
    """In-memory, ``asyncio.Lock``-guarded counters. One instance per
    process, injected into every ``SandboxManager``.

    A caller that would exceed a cap waits up to ``wait_s`` for a slot to
    be released, then gets ``SandboxLimitExceeded``. ``wait_s=0`` (the
    default) fails at once. The agent surfaces the error to the model as a
    retryable denial.
    """

    def __init__(self, limits: GovernorLimits | None = None) -> None:
        self._limits = limits or GovernorLimits()
        self._lock = asyncio.Lock()
        self._released = asyncio.Condition(self._lock)
        self._total = 0
        self._per_org: dict[str, int] = {}

    @property
    def limits(self) -> GovernorLimits:
        return self._limits

    def _full_reason(self, org_id: str | None) -> str | None:
        """Why a new slot can't be taken now, or ``None`` if it can. Call under the lock."""
        total_cap = self._limits.max_total_sandboxes
        if total_cap is not None and self._total >= total_cap:
            return (
                f"process-wide sandbox limit reached ({self._total}/{total_cap}); "
                f"retry once another sandbox is released"
            )
        org_cap = self._limits.max_per_org
        if org_id and org_cap is not None:
            org_count = self._per_org.get(org_id, 0)
            if org_count >= org_cap:
                return (
                    f"per-org sandbox limit reached for org {org_id!r} "
                    f"({org_count}/{org_cap}); retry once another sandbox "
                    f"in this org is released"
                )
        return None

    async def acquire(
        self,
        *,
        org_id: str | None = None,
        wait_s: float = 0.0,
        on_wait: Callable[[], Awaitable[None]] | None = None,
    ) -> GovernorLease:
        """Reserve a slot. When the caps are full, wait up to ``wait_s`` for
        one to be released, then raise ``SandboxLimitExceeded``.

        ``on_wait`` runs once, outside the lock, when the caller starts
        waiting, so a chat can tell its user why it paused.
        """
        # shortcut: waiters are woken all at once and re-check, not served in
        # arrival order, and a call arriving just as a slot frees can take it
        # before them; add a FIFO queue if waits near slot_wait_s show up.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        told = on_wait is None
        while True:
            async with self._released:
                reason = self._full_reason(org_id)
                if reason is None:
                    return self._take(org_id)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise SandboxLimitExceeded(reason)
                if told:
                    try:
                        await asyncio.wait_for(self._released.wait(), remaining)
                    except TimeoutError:
                        pass
                    continue
            told = True
            try:
                await on_wait()  # type: ignore[misc]
            except Exception:
                logger.debug("governor: on_wait callback failed", exc_info=True)

    def _take(self, org_id: str | None) -> GovernorLease:
        """Count a new slot. Call under the lock, after ``_full_reason`` returned ``None``."""
        self._total += 1
        if org_id:
            self._per_org[org_id] = self._per_org.get(org_id, 0) + 1
        logger.debug(
            "governor: acquired slot (total=%d org=%s org_count=%s)",
            self._total, org_id,
            self._per_org.get(org_id) if org_id else "n/a",
        )
        return GovernorLease(governor=self, org_id=org_id)

    async def release(self, lease: GovernorLease) -> None:
        """Release a previously acquired slot. Idempotent.

        The released check lives inside the lock: checking it outside lets
        two concurrent releases of the same lease both pass and decrement
        twice, which under-counts the process and lets the caps drift open.
        """
        async with self._lock:
            if lease.released:
                return
            lease.released = True
            self._total = max(0, self._total - 1)
            org_id = lease.org_id
            if org_id and org_id in self._per_org:
                remaining = max(0, self._per_org[org_id] - 1)
                if remaining:
                    self._per_org[org_id] = remaining
                else:
                    del self._per_org[org_id]
            self._released.notify_all()
            logger.debug(
                "governor: released slot (total=%d org=%s)", self._total, org_id,
            )

    def snapshot(self) -> dict[str, int | dict[str, int]]:
        """Non-blocking read of current counters — for /health and logs."""
        return {"total": self._total, "per_org": dict(self._per_org)}


_default_governor: SandboxResourceGovernor | None = None
_default_lock = threading.Lock()


def get_default_governor(
    limits: GovernorLimits | None = None,
) -> SandboxResourceGovernor:
    """The process-wide governor.

    ``limits`` configures the instance on first call and is ignored
    afterwards — later callers are per-request and must not be able to
    widen a ceiling another request already set. A mismatch is logged so a
    surprising cap is traceable.
    """
    global _default_governor
    if _default_governor is None:
        with _default_lock:
            if _default_governor is None:
                _default_governor = SandboxResourceGovernor(limits)
                logger.info(
                    "sandbox governor: process-wide limits total=%s per_org=%s",
                    _default_governor.limits.max_total_sandboxes,
                    _default_governor.limits.max_per_org,
                )
                return _default_governor
    if limits is not None and limits != _default_governor.limits:
        logger.debug(
            "sandbox governor: ignoring limits %s; process governor already "
            "configured with %s", limits, _default_governor.limits,
        )
    return _default_governor


def reset_default_governor() -> None:
    """Drop the process-wide governor. For tests only."""
    global _default_governor
    with _default_lock:
        _default_governor = None
