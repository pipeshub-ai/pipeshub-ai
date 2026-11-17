"""Container-wide resource governance for sandbox instances.

Prevents unbounded sandbox creation from OOMing the host: each
``SandboxManager`` (one per request) calls ``acquire()`` and ``release()``
against a single shared ``SandboxResourceGovernor`` instance. A metered
backend holds its slot from provisioning to destroy; any other backend
holds one only while a call runs (``SandboxManager.slot()``).

The instance has to be shared to mean anything. Per-request limits
(``SandboxLimits.max_concurrent``) bound one chat; only a shared governor
bounds the host when N chats run at once, so both
``build_coding_sandbox_manager()`` and ``ControlPlane.start()`` take theirs
from ``get_default_governor()`` rather than constructing one.

How many may run at once is the limit provider's: ``LiveLimit`` follows the
machine the sandboxes run on in docker and local mode (``live_limit.py``),
``FixedLimit`` keeps the ceiling.

A slot is a file lock in the sandbox state directory (``SharedSlots``), so
every query worker of the container counts the same slots, and a worker that
dies gives its slots back with it. Each held slot records a hash of its org,
so the per-org cap is exact across workers too. Where file locks cannot be
used, slots are counted per worker (``LocalTaggedSlots``) with a warning.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from app.agent_loop_lib.sandbox.capacity import (
    AcquireObserver,
    FixedLimit,
    HoldReason,
    LimitProvider,
    LiveView,
    SandboxCapacity,
    get_sandbox_capacity,
    hold_reason,
    org_tag,
    reset_sandbox_capacity,
    sandbox_state_directory,
)
from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotHold,
)

if TYPE_CHECKING:
    from pathlib import Path

    from app.agent_loop_lib.sandbox.live_limit import LiveLimit

__all__ = [
    "GovernorLimits",
    "GovernorLease",
    "SandboxResourceGovernor",
    "SandboxLimitExceeded",
    "get_default_governor",
    "message",
    "reset_default_governor",
]

logger = logging.getLogger(__name__)

# A mutex held by another worker is normally free again within microseconds;
# after this many quick retries the attempt waits for the next poll. Nothing
# is known about the slots then, so the limit provider is not told that runs
# wait at its limit.
_BUSY_RETRIES = 5
_BUSY_RETRY_S = 0.002
_BUSY_WARN_AFTER_S = 5.0
_BUSY_WARN_EVERY_S = 60.0
_WAITING_LOG_EVERY_S = 10.0


class GovernorLimits(BaseModel):
    """Explicit caps from code, with the meaning of ``SANDBOX_MAX_TOTAL`` and
    ``SANDBOX_MAX_PER_ORG``. ``None`` leaves that cap to the environment and
    the machine's size."""

    max_total_sandboxes: int | None = None
    max_per_org: int | None = None


@dataclass
class GovernorLease:
    """Opaque token returned by ``acquire()``; pass to ``release()``."""

    governor: SandboxResourceGovernor
    hold: SlotHold
    tag: bytes = b""
    released: bool = field(default=False, repr=False)

    async def release(self) -> None:
        await self.governor.release(self)


def message(
    reason: HoldReason, *, held: int | None, limit: int, tag_held: int | None = None,
    org_limit: int | None = None, memory_source: Literal["host", "sidecar"] = "host",
) -> str:
    """What the model is told when a run gives up waiting for a sandbox."""
    if reason is HoldReason.ORG_SHARE:
        return (
            f"this organisation's share of code sandboxes is in use "
            f"({tag_held}/{org_limit}); retry once one of its runs ends"
        )
    runner = "the code runner" if memory_source == "sidecar" else "the server"
    if reason in (HoldReason.LIVE_MEMORY, HoldReason.HOST_MEMORY_FLOOR):
        return (
            f"{runner} is short of memory right now ({held}/{limit} code sandboxes "
            f"in use); retry in a minute"
        )
    if reason is HoldReason.LIVE_CPU:
        runner = "the code runner's" if memory_source == "sidecar" else "the server's"
        return (
            f"{runner} CPU is busy, so fewer code sandboxes run at once "
            f"({held}/{limit}); retry in a minute"
        )
    count = "" if held is None else f" ({held}/{limit})"
    return f"all {limit} code sandboxes on this server are busy{count}; retry once one is released"


def _memory_source(view: LiveView) -> Literal["host", "sidecar"]:
    return "sidecar" if (view.source or "").startswith("sidecar") else "host"


def _wake(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class SandboxResourceGovernor:
    """Counts code sandbox slots for every worker of the container.

    A caller that would exceed the limit, or its org's cap, waits up to
    ``wait_s`` for a slot, then gets ``SandboxLimitExceeded``. ``wait_s=0``
    (the default) fails at once. The agent surfaces the error to the model as
    a retryable denial.

    A release in this process wakes this process's waiters at once. Releases
    in other workers have no notification across processes, so every waiter
    also retries on a jittered ``poll_s`` timer.
    """

    def __init__(
        self,
        capacity: SandboxCapacity,
        slots: SharedSlots | LocalTaggedSlots,
        *,
        limit_provider: LimitProvider,
        observers: Sequence[AcquireObserver] = (),
        poll_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.capacity = capacity
        self._slots = slots
        self.limit_provider = limit_provider
        self._observers = list(observers)
        self._poll_s = poll_s
        self._clock = clock
        self._rng = rng or random.Random()
        # Futures of this process's waiters, possibly on different loops;
        # resolved through their own loop so any thread may wake them.
        self._waiters: set[asyncio.Future[None]] = set()
        self._waiters_lock = threading.Lock()
        self._waiting_here = 0
        self._sizing_awaited = False
        self._last_counts: Counter[bytes] = Counter()
        self._warned_observers: set[tuple[int, str]] = set()
        self._busy_since: float | None = None
        self._busy_warned_at: float | None = None
        self._waiting_logged_at: float | None = None
        capacity.add_listener(self.capacity_changed)

    @classmethod
    def in_process(cls, limits: GovernorLimits | None = None) -> SandboxResourceGovernor:
        """A governor whose slots this process alone counts, with explicit caps
        (an unset total allows up to 1024). For callers that need their own
        budget, such as tests."""
        limits = limits or GovernorLimits()
        total = limits.max_total_sandboxes or 1024
        capacity = SandboxCapacity.fixed(total, limits.max_per_org)
        return cls(
            capacity, LocalTaggedSlots(capacity.slot_files), limit_provider=FixedLimit(capacity),
        )

    @property
    def shared(self) -> bool:
        return isinstance(self._slots, SharedSlots)

    def close(self) -> None:
        """Close the slot files, which gives back every slot held here."""
        self._slots.close()

    def _notify(self, hook: str, *args: Any) -> None:
        for observer in self._observers:
            try:
                getattr(observer, hook)(*args)
            except Exception:
                key = (id(observer), hook)
                if key not in self._warned_observers:
                    self._warned_observers.add(key)
                    logger.warning("sandbox governor: observer %r failed in %s", observer, hook, exc_info=True)

    async def acquire(
        self,
        *,
        org_id: str | None = None,
        wait_s: float = 0.0,
        on_wait: Callable[[], Awaitable[None]] | None = None,
    ) -> GovernorLease:
        """Take a slot. When none is free, wait up to ``wait_s`` for one, then
        raise ``SandboxLimitExceeded`` (with ``.reason``).

        ``on_wait`` runs once, when the caller starts waiting, so a chat can
        tell its user why it paused. A mutex another worker holds for an
        instant is not a wait.

        Nothing here blocks the event loop: each try at the slots is one
        non-blocking pass.
        """
        # shortcut: waiters are woken all at once and re-check, not served in
        # arrival order, and a call arriving just as a slot frees can take it
        # before them; add a FIFO queue if waits near slot_wait_s show up.
        if not self._sizing_awaited:
            self._sizing_awaited = True
            await self.capacity.ensure_sized(timeout=2.0)
        tag = org_tag(org_id)
        start = self._clock()
        deadline = start + wait_s
        told = on_wait is None
        busy_tries = 0
        last_reason: HoldReason | None = None
        outcome: Literal["granted", "refused", "cancelled"] | None = None
        held: int | None = 0
        limit = 0
        held_known = False
        tag_held: int | None = None
        org_limit: int | None = None
        counted = False
        try:
            while True:
                view = self.limit_provider.view()
                limit = view.limit
                org_limit = self.capacity.per_org_limit(limit) if tag else None
                result = self._slots.try_acquire_tagged(limit, tag=tag, tag_limit=org_limit)
                denied: Literal["full", "tag_full"] | None = None
                floor: HoldReason | None = None
                busy = False
                if isinstance(result, SlotHold):
                    self._busy_since = None
                    held, held_known = result.held_before, True
                    try:
                        floor = await self.limit_provider.admit_and_charge(held=held)
                    except BaseException:
                        result.release()
                        self._wake_local()
                        raise
                    if floor is None:
                        outcome = "granted"
                        return GovernorLease(governor=self, hold=result, tag=tag)
                    result.release()
                    self._wake_local()
                elif result.reason == "busy":
                    self._note_busy()
                    if busy_tries < _BUSY_RETRIES:
                        busy_tries += 1
                        await asyncio.sleep(_BUSY_RETRY_S)
                        continue
                    busy = True
                else:
                    self._busy_since = None
                    denied = result.reason
                    held, tag_held, held_known = result.held, result.tag_held, True
                busy_tries = 0
                if busy:
                    reason = last_reason or HoldReason.SLOTS_FULL
                else:
                    reason = hold_reason(view, denied=denied, floor=floor)
                if last_reason is None:
                    self._notify("wait_start", reason)
                elif reason != last_reason:
                    self._notify("wait_reason", reason)
                last_reason = reason
                now = self._clock()
                if not busy:
                    self.limit_provider.waiting(reason, now - start)
                if now >= deadline:
                    outcome = "refused"
                    if not held_known:
                        held = self.held_total()
                    raise SandboxLimitExceeded(
                        message(
                            reason, held=held, limit=limit, tag_held=tag_held,
                            org_limit=org_limit, memory_source=_memory_source(view),
                        ),
                        reason=reason,
                    )
                if not counted:
                    counted = True
                    self._waiting_here += 1
                self._log_waiting(now)
                if not told:
                    told = True
                    try:
                        await on_wait()  # type: ignore[misc]
                    except Exception:
                        logger.debug("governor: on_wait callback failed", exc_info=True)
                    continue
                await self._sleep_until_woken(
                    min(deadline - now, self._poll_s * self._rng.uniform(0.5, 1.0)),
                )
        except BaseException:
            if outcome is None:
                outcome = "cancelled"
            raise
        finally:
            if counted:
                self._waiting_here -= 1
            if last_reason is not None:
                try:
                    self.limit_provider.wait_ended(granted=outcome == "granted")
                except Exception:
                    logger.debug("governor: wait_ended failed", exc_info=True)
            self._notify(
                "outcome", outcome or "cancelled",
                0.0 if last_reason is None else self._clock() - start, last_reason,
            )

    async def _sleep_until_woken(self, timeout: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        with self._waiters_lock:
            self._waiters.add(future)
        try:
            await asyncio.wait_for(future, max(0.0, timeout))
        except TimeoutError:
            pass
        finally:
            with self._waiters_lock:
                self._waiters.discard(future)

    def _wake_local(self) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters)
        for future in waiters:
            loop = future.get_loop()
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(_wake, future)
            except RuntimeError:
                pass  # the loop closed in between

    def capacity_changed(self) -> None:
        """The limit may have risen (or sizing finished): this process's
        waiters try again now. Safe from any thread."""
        self._wake_local()

    def _note_busy(self) -> None:
        now = self._clock()
        if self._busy_since is None:
            self._busy_since = now
            return
        if now - self._busy_since < _BUSY_WARN_AFTER_S:
            return
        if self._busy_warned_at is None or now - self._busy_warned_at >= _BUSY_WARN_EVERY_S:
            self._busy_warned_at = now
            logger.warning(
                "sandbox slots: the slot mutex has been busy for %.0fs; another worker "
                "may be stopped while counting", now - self._busy_since,
            )

    def _log_waiting(self, now: float) -> None:
        if self._waiting_logged_at is not None and now - self._waiting_logged_at < _WAITING_LOG_EVERY_S:
            return
        self._waiting_logged_at = now
        snap = self.snapshot()
        logger.info(
            "sandbox slots: total=%s limit=%s ceiling=%s waiting_here=%s stale=%s mode=%s",
            snap["total"], snap["limit"], snap["ceiling"], snap["waiting_here"],
            snap["stale"], snap["mode"],
        )

    async def release(self, lease: GovernorLease) -> None:
        """Release a previously acquired slot. Idempotent.

        Synchronous inside: nothing here awaits, so a cancel cannot land
        between giving the slot back and marking the lease released.
        """
        if lease.released:
            return
        lease.released = True
        lease.hold.release()
        self._wake_local()

    def held_total(self, budget: float = 0.0) -> int | None:
        """Slots held by every worker, or ``None`` when another worker held
        the mutex for all of *budget* seconds. A budget above 0 spins: never
        call it with one on the event loop."""
        try:
            return sum(self._slots.held_by_tag(budget).values())
        except TimeoutError:
            return None

    def snapshot(self) -> dict[str, Any]:
        """Current counts and limits, for logs and health. Never blocks: when
        another worker is counting, the last counts seen are returned with
        ``stale`` set."""
        stale = False
        try:
            self._last_counts = self._slots.held_by_tag(0.0)
        except TimeoutError:
            stale = True
        counts = self._last_counts
        view = self.limit_provider.view()
        live_block = getattr(self.limit_provider, "live_block", None)
        return {
            "total": sum(counts.values()),
            "per_org": {tag.decode(): n for tag, n in counts.items() if tag},
            "stale": stale,
            "limit": view.limit,
            "ceiling": self.capacity.ceiling(),
            "floor": self.capacity.floor(),
            "mode": self.capacity.mode,
            "sized": self.capacity.sized,
            "slot_files": self.capacity.slot_files,
            "shared": self.shared,
            "waiting_here": self._waiting_here,
            "live": None if live_block is None else live_block(view),
        }


def _open_slots(directory: Path | None, capacity: int) -> SharedSlots | LocalTaggedSlots:
    reason = "no POSIX file locks"
    if directory is not None:
        try:
            return SharedSlots(directory / "slots", capacity)
        except OSError as exc:
            reason = str(exc)
    logger.warning(
        "sandbox slots are counted per worker (%s); with N workers up to N x limit can run",
        reason,
    )
    return LocalTaggedSlots(capacity)


_default_governor: SandboxResourceGovernor | None = None
_default_lock = threading.Lock()


def get_default_governor(
    limits: GovernorLimits | None = None,
) -> SandboxResourceGovernor:
    """The process's governor, counting the container's shared slots.

    ``limits`` applies on the first call only, as explicit caps that win
    over the environment; later callers are per-request and must not be able
    to widen a ceiling another request already set. A mismatch is logged so
    a surprising cap is traceable.
    """
    global _default_governor
    if _default_governor is None:
        with _default_lock:
            if _default_governor is None:
                capacity = get_sandbox_capacity()
                if limits is not None:
                    capacity.override(
                        max_total=limits.max_total_sandboxes, max_per_org=limits.max_per_org,
                    )
                slots = _open_slots(sandbox_state_directory(), capacity.slot_files)
                _default_governor = SandboxResourceGovernor(
                    capacity, slots, limit_provider=FixedLimit(capacity),
                    observers=_default_observers(capacity),
                )
                if capacity.mode in ("compose", "local", "helm_dind"):
                    from app.agent_loop_lib.sandbox.live_limit import (
                        LiveLimit,
                        set_live_limit,
                    )

                    live = LiveLimit(capacity, _default_governor)
                    _default_governor.limit_provider = live
                    set_live_limit(live)
                    _attach_indexing_yield(live)
                logger.info(
                    "sandbox governor: mode=%s ceiling=%s floor=%s slot_files=%s shared=%s",
                    capacity.mode, capacity.ceiling(), capacity.floor(),
                    capacity.slot_files, _default_governor.shared,
                )
                return _default_governor
    if limits is not None and limits != GovernorLimits():
        logger.debug(
            "sandbox governor: ignoring limits %s; the process governor is already built", limits,
        )
    return _default_governor


def _default_observers(capacity: SandboxCapacity) -> list[AcquireObserver]:
    """The load ledger behind the admin load alerts. E2B runs elsewhere and
    has no alerts, so its runs are not recorded."""
    if capacity.mode == "e2b":
        return []
    from app.agent_loop_lib.sandbox.load_ledger import get_default_load_ledger

    return [get_default_load_ledger()]


def _attach_indexing_yield(live: LiveLimit) -> None:
    """Let runs waiting for memory ask indexing to slow down, where the
    setting allows it. ``LiveLimit.waits_for_memory`` decides where indexing
    shares the memory."""
    from app.agent_loop_lib.sandbox.live_yield import IndexingYield
    from app.services.resource_governor.yield_signal import YieldSignal

    settings = live.settings
    if not settings.yield_indexing:
        logger.info("sandbox runs waiting for memory do not slow indexing (SANDBOX_YIELD_INDEXING=off)")
        return
    writer = IndexingYield(live, YieldSignal.open_default(role="code sandbox"), settings)
    if writer.enabled:
        live.on_memory_wait = writer.on_memory_wait
        live.after_sample = writer.check_lapsed


def reset_default_governor() -> None:
    """Drop the process's governor and capacity, closing its slots. For tests only."""
    global _default_governor
    with _default_lock:
        governor, _default_governor = _default_governor, None
    if governor is not None:
        governor.close()
    from app.agent_loop_lib.sandbox.live_limit import set_live_limit
    from app.agent_loop_lib.sandbox.load_ledger import reset_default_load_ledger

    set_live_limit(None)
    reset_default_load_ledger()
    reset_sandbox_capacity()
