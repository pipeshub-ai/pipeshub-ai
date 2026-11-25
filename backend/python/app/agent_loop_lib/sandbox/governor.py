"""Container-wide resource governance for sandbox instances.

Prevents unbounded sandbox creation from OOMing the host: each
``SandboxManager`` (one per request) calls ``acquire()`` and ``release()``
against a single shared ``SandboxResourceGovernor`` instance. A metered
backend holds its slot from provisioning to destroy; any other backend
holds one only while a call runs (``SandboxManager.slot()``).

The instance has to be shared to mean anything. Per-request limits
(``SandboxLimits.max_concurrent`` sandboxes, ``SANDBOX_MAX_PER_REQUEST``
slots held at once, both in ``SandboxManager``) bound one chat; only a shared governor
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
import math
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
)
from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded
from app.core.org_context import spawn_detached
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotHold,
    ensure_private_directory,
    sandbox_state_directory,
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
    if limit == 1:
        return f"the only code sandbox on this server is busy{count}; retry once it is released"
    return f"all {limit} code sandboxes on this server are busy{count}; retry once one is released"


def _memory_source(view: LiveView) -> Literal["host", "sidecar"]:
    return "sidecar" if (view.source or "").startswith("sidecar") else "host"


@dataclass(eq=False)
class _Waiter:
    """One acquire of this worker that is waiting, or about to."""

    tag: bytes
    start: float
    # Set while it waits for its turn; resolved to wake it.
    future: asyncio.Future[None] | None = None
    reason: HoldReason | None = None
    # What its last try met: "full", "tag_full", "floor" or "busy".
    denied: str | None = None


class _LoopQueue:
    """The waiters of one event loop, oldest first, and the one task that
    polls the slot files for them. Touched only from that loop's thread."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.waiters: dict[_Waiter, None] = {}
        self.poller: asyncio.Task[None] | None = None
        self.kick = asyncio.Event()

    def wake_one(self, released_tag: bytes | None = None) -> None:
        """Wake the oldest waiter that a freed slot may serve: one held back
        by its org's share only when the slot was its own org's."""
        for waiter in self.waiters:
            future = waiter.future
            if future is None or future.done():
                continue
            if waiter.denied == "tag_full" and waiter.tag != released_tag:
                continue
            future.set_result(None)
            return

    def kick_poller(self) -> None:
        self.kick.set()


class SandboxResourceGovernor:
    """Counts code sandbox slots for every worker of the container.

    A caller that would exceed the limit, or its org's cap, waits up to
    ``wait_s`` for a slot, then gets ``SandboxLimitExceeded``. ``wait_s=0``
    (the default) fails at once. The agent surfaces the error to the model as
    a retryable denial.

    Waiting costs one scan per poll, not one per waiter: each event loop has
    one poller task, running only while that loop has waiters. Every jittered
    ``poll_s`` it counts the held slots once and wakes as many waiters as
    there are free slots, oldest first, each of which then tries for a slot
    itself. A release in this process wakes one waiter at once; releases in
    other workers have no notification across processes and are seen at the
    next poll. ``capacity_changed()`` makes the pollers look at once.
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
        # One queue per event loop with waiters; any thread may reach a queue
        # through its loop's call_soon_threadsafe.
        self._queues: dict[asyncio.AbstractEventLoop, _LoopQueue] = {}
        self._queues_lock = threading.Lock()
        self._waiting_here = 0
        self._sizing_awaited = False
        self._last_counts: Counter[bytes] = Counter()
        self._warned_observers: set[tuple[int, str]] = set()
        self._warned_poller = False
        self._warned_unavailable = False
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

    def _set_reason(self, waiter: _Waiter, reason: HoldReason) -> None:
        if waiter.reason is None:
            self._notify("wait_start", reason)
        elif reason != waiter.reason:
            self._notify("wait_reason", reason)
        waiter.reason = reason

    def _unavailable(self, exc: OSError) -> SandboxLimitExceeded:
        if not self._warned_unavailable:
            self._warned_unavailable = True
            logger.warning(
                "sandbox slots in %s cannot be used: %s",
                getattr(self._slots, "directory", None) or "this worker", exc,
            )
        return SandboxLimitExceeded(
            f"code sandbox capacity is unavailable: {exc.strerror or exc}", reason=None,
        )

    async def acquire(
        self,
        *,
        org_id: str | None = None,
        wait_s: float = 0.0,
        on_wait: Callable[[], Awaitable[None]] | None = None,
    ) -> GovernorLease:
        """Take a slot. When none is free, wait up to ``wait_s`` for one, then
        raise ``SandboxLimitExceeded`` (with ``.reason``). A slot directory
        that cannot be used raises it too, with ``reason`` ``None``.

        ``on_wait`` runs once, when the caller starts waiting, so a chat can
        tell its user why it paused. A mutex another worker holds for an
        instant is not a wait. A mutex held longer is a wait but not a wait at
        the limit: observers and the limit provider are told only of waits
        with a known reason.

        Nothing here blocks the event loop: each try at the slots is one
        non-blocking pass.
        """
        # shortcut: a call arriving just as a slot frees can take it before
        # the woken waiter; waiters are served in arrival order only among
        # themselves, and only within one worker.
        if not wait_s >= 0:  # NaN or negative: a NaN deadline never passes
            wait_s = 0.0
        tag = org_tag(org_id)
        me = _Waiter(tag=tag, start=self._clock())
        told = on_wait is None
        busy_tries = 0
        outcome: Literal["granted", "refused", "cancelled"] | None = None
        held: int | None = 0
        limit = 0
        held_known = False
        tag_held: int | None = None
        org_limit: int | None = None
        counted = False
        queue: _LoopQueue | None = None
        try:
            if not self._sizing_awaited:
                # Inside the try: a cancel here is still one "cancelled" outcome.
                self._sizing_awaited = True
                await self.capacity.ensure_sized(timeout=2.0)
                me.start = self._clock()
            deadline = me.start + wait_s
            while True:
                view = self.limit_provider.view()
                limit = view.limit
                org_limit = self.capacity.per_org_limit(limit) if tag else None
                try:
                    result = self._slots.try_acquire_tagged(limit, tag=tag, tag_limit=org_limit)
                except OSError as exc:
                    outcome = "refused"
                    raise self._unavailable(exc) from exc
                reason: HoldReason | None
                if isinstance(result, SlotHold):
                    self._busy_since = None
                    held, held_known = result.held_before, True
                    try:
                        floor = await self.limit_provider.admit_and_charge(held=held)
                    except BaseException:
                        result.release()
                        self._wake_one(tag)
                        raise
                    if floor is None:
                        outcome = "granted"
                        return GovernorLease(governor=self, hold=result, tag=tag)
                    # Given back without waking anyone: another waiter would
                    # meet the same floor, and wake this one back.
                    result.release()
                    me.denied = "floor"
                    reason = hold_reason(view, denied=None, floor=floor)
                elif result.reason == "busy":
                    self._note_busy()
                    if busy_tries < _BUSY_RETRIES:
                        busy_tries += 1
                        await asyncio.sleep(_BUSY_RETRY_S)
                        continue
                    # Nothing is known about the slots: a wait, but no reason.
                    me.denied = "busy"
                    reason = None
                else:
                    self._busy_since = None
                    held, tag_held, held_known = result.held, result.tag_held, True
                    denied = result.reason
                    if (
                        denied == "full" and org_limit is not None and org_limit < limit
                        and tag_held is not None and tag_held >= org_limit
                    ):
                        # Its org's own cap binds too: no slot another org
                        # frees would serve it, so it waits for its share.
                        denied = "tag_full"
                    me.denied = denied
                    reason = hold_reason(view, denied=denied, floor=None)
                busy_tries = 0
                now = self._clock()
                if reason is not None:
                    self._set_reason(me, reason)
                    self.limit_provider.waiting(reason, now - me.start)
                if now >= deadline:
                    outcome = "refused"
                    if not held_known:
                        held = self.held_total()
                    refusal = reason or me.reason or HoldReason.SLOTS_FULL
                    raise SandboxLimitExceeded(
                        message(
                            refusal, held=held, limit=limit, tag_held=tag_held,
                            org_limit=org_limit, memory_source=_memory_source(view),
                        ),
                        reason=refusal,
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
                if queue is None:
                    queue = self._join(me)
                await self._wait_turn(me, deadline - now)
        except BaseException:
            if outcome is None:
                outcome = "cancelled"
            raise
        finally:
            if queue is not None:
                self._leave(queue, me, granted=outcome == "granted")
            if counted:
                self._waiting_here -= 1
            if me.reason is not None:
                try:
                    self.limit_provider.wait_ended(granted=outcome == "granted")
                except Exception:
                    logger.debug("governor: wait_ended failed", exc_info=True)
            self._notify(
                "outcome", outcome or "cancelled",
                0.0 if me.reason is None else self._clock() - me.start, me.reason,
            )

    def _join(self, waiter: _Waiter) -> _LoopQueue:
        """Put *waiter* at the back of its loop's queue, starting the poller."""
        loop = asyncio.get_running_loop()
        with self._queues_lock:
            queue = self._queues.get(loop)
            if queue is None:
                queue = self._queues[loop] = _LoopQueue(loop)
        queue.waiters[waiter] = None
        if queue.poller is None or queue.poller.done():
            queue.poller = spawn_detached(self._poll(queue), name="sandbox-slot-poller")
        return queue

    def _leave(self, queue: _LoopQueue, waiter: _Waiter, *, granted: bool) -> None:
        queue.waiters.pop(waiter, None)
        future = waiter.future
        if not granted and future is not None and future.done() and not future.cancelled():
            # Woken for a slot it will not take: pass the turn on.
            queue.wake_one()
        if not queue.waiters:
            queue.kick_poller()  # lets the poller see it has nothing left to do

    @staticmethod
    async def _wait_turn(waiter: _Waiter, timeout: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        waiter.future = future
        try:
            await asyncio.wait({future}, timeout=None if math.isinf(timeout) else max(0.0, timeout))
        finally:
            if not future.done():
                future.cancel()

    async def _poll(self, queue: _LoopQueue) -> None:
        """While *queue* has waiters: every jittered ``poll_s`` (or at once
        when kicked), count the slots once and wake waiters for the free ones."""
        busy_tries = 0
        try:
            while queue.waiters:
                if busy_tries:
                    delay = _BUSY_RETRY_S
                else:
                    delay = self._poll_s * self._rng.uniform(0.5, 1.0)
                try:
                    await asyncio.wait_for(queue.kick.wait(), delay)
                except TimeoutError:
                    pass
                queue.kick.clear()
                if not queue.waiters:
                    break
                try:
                    looked = self._poll_once(queue)
                except Exception:
                    looked = True
                    if not self._warned_poller:
                        self._warned_poller = True
                        logger.warning("sandbox slots: the waiters' poll failed", exc_info=True)
                busy_tries = 0 if looked or busy_tries >= _BUSY_RETRIES else busy_tries + 1
        finally:
            with self._queues_lock:
                if not queue.waiters and self._queues.get(queue.loop) is queue:
                    del self._queues[queue.loop]

    def _poll_once(self, queue: _LoopQueue) -> bool:
        """One look at the slots for *queue*'s waiters. ``False`` when another
        worker held the mutex, so nothing was learned."""
        view = self.limit_provider.view()
        limit = view.limit
        try:
            counts = self._slots.held_by_tag(0.0)
        except TimeoutError:
            self._note_busy()
            return False
        self._busy_since = None
        self._last_counts = counts
        org_limit = self.capacity.per_org_limit(limit)
        free = limit - sum(counts.values())
        planned: Counter[bytes] = Counter()
        now = self._clock()
        longest: dict[HoldReason, float] = {}
        for waiter in list(queue.waiters):
            future = waiter.future
            if future is None or future.done():
                continue  # it is trying for a slot itself right now
            org_full = (
                bool(waiter.tag) and org_limit is not None
                and counts[waiter.tag] + planned[waiter.tag] >= org_limit
            )
            if free > 0 and not org_full:
                free -= 1
                planned[waiter.tag] += 1
                future.set_result(None)
                continue
            # An org at its own cap waits for its share even when every slot
            # is taken too, unless that cap is the whole limit.
            share_binds = org_full and org_limit is not None and org_limit < limit
            reason = hold_reason(view, denied="full" if free <= 0 and not share_binds else "tag_full", floor=None)
            self._set_reason(waiter, reason)
            longest[reason] = max(longest.get(reason, 0.0), now - waiter.start)
        for reason, waited_s in longest.items():
            self.limit_provider.waiting(reason, waited_s)
        if queue.waiters:
            self._log_waiting(now)
        return True

    def _on_loops(self, method: str, *args: Any) -> None:
        with self._queues_lock:
            queues = list(self._queues.values())
        for queue in queues:
            if queue.loop.is_closed():
                continue
            try:
                queue.loop.call_soon_threadsafe(getattr(queue, method), *args)
            except RuntimeError:
                pass  # the loop closed in between

    def _wake_one(self, released_tag: bytes) -> None:
        self._on_loops("wake_one", released_tag)

    def capacity_changed(self) -> None:
        """The limit may have risen (or sizing finished): this process's
        pollers look at the slots now. Safe from any thread."""
        self._on_loops("kick_poller")

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
        self._wake_one(lease.tag)

    def held_total(self, budget: float = 0.0) -> int | None:
        """Slots held by every worker, or ``None`` when another worker held
        the mutex for all of *budget* seconds. A budget above 0 spins: never
        call it with one on the event loop."""
        try:
            return sum(self._slots.held_by_tag(budget).values())
        except (TimeoutError, OSError):
            # A slot file that cannot be read says nothing about the count;
            # the next acquire meets the same error and is refused for it.
            return None

    def snapshot(self) -> dict[str, Any]:
        """Current counts and limits, for logs and health. Never blocks: when
        another worker is counting, the last counts seen are returned with
        ``stale`` set."""
        stale = False
        try:
            self._last_counts = self._slots.held_by_tag(0.0)
        except (TimeoutError, OSError):
            # For logs and health: never raises, or a waiting run's log line
            # would fail it with an error other than ``SandboxLimitExceeded``.
            stale = True
        counts = self._last_counts
        view = self.limit_provider.view()
        live_block = getattr(self.limit_provider, "live_block", None)
        return {
            "total": sum(counts.values()),
            "by_org": {tag.decode(errors="replace"): n for tag, n in counts.items() if tag},
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
            # The state directory itself first: one another user created
            # before this stack started must not hold the slot files.
            ensure_private_directory(directory)
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
