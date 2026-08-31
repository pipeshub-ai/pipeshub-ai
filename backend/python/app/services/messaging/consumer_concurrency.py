"""Shared admission and lease plumbing for the Kafka and Redis Streams
indexing consumers.

Both consumers run a worker thread with its own event loop for message
processing, and both need identical admission, lease and retry-tracking
logic — previously duplicated near-verbatim in both files.

The ``DistributedConcurrencyManager`` and ``RetryManager`` used to be safe
only on the main loop, so every call from a worker task hopped across
threads under a 5s deadline. That deadline was armed on the *worker* loop, so
a loop that stalled past it cancelled the in-flight Redis command, and
redis-py drops a connection whose command was cancelled. Both now hand out a
client per event loop (``redis_client.RedisClientRegistry``), so these
helpers call them directly and ``bridge_to_main_loop`` survives only for the
genuinely broker-bound operations each consumer still owns (XACK/XPENDING,
Kafka commit, producer sends).

Functions here take the consumer instance (``host``) as their first argument
and read/write its existing attributes (``running``, ``concurrency_manager``,
``retry_manager``, ``governor``, ``lease_renewer``, ``logger``,
``_distributed_log_times``) rather than being methods on a shared base class.
This keeps the fix in one place without changing either consumer's class
hierarchy or the (sometimes name-mangled) method names tests patch directly.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.services.messaging.config import messaging_env
from app.services.messaging.distributed_concurrency import (
    DistributedLeaseSet,
    LeaseKind,
    lease_kind,
)
from app.services.resource_governor import gate_pool, index_pool, parse_cost
from app.services.resource_governor.models import ParseTier, Pool

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.messaging.lease import LeaseHandle, LeaseRenewer
    from app.services.messaging.retry_manager import RetryManager
    from app.services.resource_governor import ResourceGovernor

_MAIN_LOOP_OP_TIMEOUT = 5.0

# Read-ahead depth, as a multiple of the total in-flight index budget and
# clamped at both ends. Two, not four: with the index ceilings now derived per
# tier rather than at 100x a parse tier, four would prefetch more stream
# entries than a node can plausibly start before its lease on them lapses.
_PENDING_TASKS_PER_INDEX_SLOT = 2
_MIN_PENDING_INDEXING_TASKS = 64
_MAX_PENDING_INDEXING_TASKS = 512


class ConcurrencyHost(Protocol):
    """Structural type for the attributes these helpers rely on."""

    logger: "Logger"
    running: bool
    main_loop: asyncio.AbstractEventLoop | None
    concurrency_manager: "DistributedConcurrencyManager | None"
    retry_manager: "RetryManager | None"
    _distributed_log_times: dict[str, float]
    # Present on both indexing consumers; None unless a ResourceGovernor was
    # injected at construction time (see Phase 1 of the adaptive-concurrency
    # plan). When None, consumers fall back to the legacy per-worker-loop
    # ``asyncio.Semaphore`` pair created alongside it.
    governor: "ResourceGovernor | None"
    parsing_semaphore: Any
    indexing_semaphore: Any
    # Created inside the consumer's worker thread and dropped with its loop,
    # so this is None before the thread starts and after it stops, as well as
    # whenever no concurrency_manager was injected.
    lease_renewer: "LeaseRenewer | None"
    # Guarded by the same lock as ``_active_futures`` (see GateWaiterToken):
    # count of tasks spawned but not yet admitted through the local
    # indexing gate/semaphore.
    _gate_waiters: int
    _futures_lock: Any


async def bridge_to_main_loop(
    host: ConcurrencyHost, coro: Any, timeout: float = _MAIN_LOOP_OP_TIMEOUT
) -> Any:
    """Run ``coro`` on ``host.main_loop`` (safe when called from a worker loop)."""
    current_loop = asyncio.get_running_loop()
    main_loop = host.main_loop
    if main_loop is not None and current_loop is not main_loop:
        if not main_loop.is_running():
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise RuntimeError("Main event loop is not running")
        try:
            future = asyncio.run_coroutine_threadsafe(coro, main_loop)
        except BaseException:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future), timeout=timeout
            )
        except BaseException:
            future.cancel()
            raise
    return await coro


def _normalize_operation(operation: str) -> str:
    """Collapse ``<op>:record:<record_id>`` to ``<op>:record``.

    Per-record pool names are unique per record, so without this the
    throttle map would grow one entry per distinct record forever instead
    of sharing a single throttle bucket for "record lease" errors.
    """
    if ":record:" in operation:
        return operation.split(":record:", 1)[0] + ":record"
    return operation


def log_distributed_error(
    host: ConcurrencyHost, operation: str, error: Exception
) -> None:
    operation = _normalize_operation(operation)
    now = time.monotonic()
    if now - host._distributed_log_times.get(operation, 0.0) >= 30.0:
        host.logger.warning(
            "Distributed concurrency %s failed; indexing remains paused: %s",
            operation,
            error,
        )
        host._distributed_log_times[operation] = now


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter.

    Jittered because every waiter on a pool wakes from the same event — a
    fixed schedule would re-synchronise them into a thundering herd against
    the very Redis they are waiting on.
    """
    ceiling = min(cap, base * (2 ** min(attempt, 16)))
    return random.uniform(base, max(base, ceiling))


async def acquire_distributed_slot(
    host: ConcurrencyHost,
    pool: str,
    owner: str,
    limit: int,
    deadline_seconds: float | None = None,
    *,
    leases: "DistributedLeaseSet | None" = None,
) -> bool:
    """Try to acquire a distributed lease on ``pool`` for ``owner``.

    Records the lease in ``leases`` itself, and *only* when Redis actually
    granted it. This is the one place that can tell a granted lease from a
    fail-open admission — both return True — so it is the only place that can
    register one without registering the other. A caller that added the pool
    on its own would register leases Redis never issued, and the renewer would
    then get 0 back for them the moment Redis recovered and abort a record
    that was processing perfectly well under its real leases. Same reasoning
    as ``DistributedLeaseSet`` mirroring into the renewer rather than leaving
    callers to keep two structures in step.

    With ``deadline_seconds`` set, gives up (returning False) after that many
    seconds instead of waiting indefinitely — used for the per-record lease,
    which is contended by duplicate in-flight deliveries of the *same* record
    and must not convoy the whole pipeline while already holding the outer
    indexing permit.

    Contention and failure are backed off separately, and only failure is
    budgeted. Contention is normal: the pool is full and this record waits its
    turn. A failure means Redis itself is unreachable, and retrying that
    forever is what turned a transient stall into an outage — every waiter
    re-attacking at a flat 0.5s meant load on Redis scaled with the size of
    the queue waiting on it. Once the budget is spent, ``LeaseKind`` decides:
    a capacity lease proceeds on the node-local gate alone (a bounded
    over-admission across the fleet, and exactly what
    ``DISTRIBUTED_INDEXING_CONCURRENCY=false`` does deliberately), while an
    exclusivity lease refuses, so a record is never indexed twice.
    """
    manager = host.concurrency_manager
    if manager is None:
        return True

    kind = lease_kind(pool)
    base = messaging_env.concurrency_acquire_poll_seconds
    cap = messaging_env.concurrency_acquire_max_backoff_seconds
    budget = messaging_env.concurrency_failure_budget
    start = time.monotonic()
    contention_attempt = 0
    failures = 0

    while host.running:
        try:
            acquired = await manager.try_acquire(
                pool,
                owner,
                limit,
                messaging_env.concurrency_lease_seconds,
            )
        except Exception as exc:
            failures += 1
            log_distributed_error(host, f"acquire:{pool}", exc)
            if failures >= budget:
                if kind is LeaseKind.CAPACITY:
                    host.logger.warning(
                        "Distributed %s lease unavailable after %d attempts; "
                        "continuing under node-local limits only. Cluster-wide "
                        "indexing concurrency is unenforced until Redis recovers.",
                        pool, failures,
                    )
                    # Admitted, but holding no lease — deliberately not
                    # recorded. Registering it would hand the renewer a lease
                    # Redis never issued, and the next round after Redis came
                    # back would read 0 for it and abort this record.
                    return True
                return False
            delay = _backoff_delay(failures, base, cap)
        else:
            failures = 0
            if acquired:
                host._distributed_log_times.pop(
                    _normalize_operation(f"acquire:{pool}"), None
                )
                if leases is not None:
                    leases.add(pool, owner)
                return True
            contention_attempt += 1
            delay = _backoff_delay(contention_attempt, base, cap)

        # Checked on both paths, and before sleeping. An earlier version
        # short-circuited straight back to the top on the error path, so a
        # caller's deadline was silently ignored whenever Redis was failing
        # rather than merely contended — exactly the case where the caller
        # most wants to give the permit back and let the message be retried.
        #
        # The sleep is clamped to what is left of the deadline, not just
        # gated on it: the backoff grows to concurrency_acquire_max_backoff
        # (5s) while the record lease only waits 10s, so an unclamped sleep
        # would hold the outer index permit up to half again as long as the
        # caller asked for.
        if deadline_seconds is not None:
            remaining = deadline_seconds - (time.monotonic() - start)
            if remaining <= 0:
                return False
            delay = min(delay, remaining)

        await asyncio.sleep(delay)

    return False


async def release_distributed_slot(
    host: ConcurrencyHost, pool: str, owner: str
) -> None:
    manager = host.concurrency_manager
    if manager is None:
        return
    try:
        await manager.release(pool, owner)
    except Exception as exc:
        log_distributed_error(host, f"release:{pool}", exc)


def new_lease_set(host: ConcurrencyHost) -> DistributedLeaseSet:
    """Per-message lease bookkeeping, wired to this consumer's renewer."""
    return DistributedLeaseSet(renewer=host.lease_renewer)


def start_lease_guard(
    host: ConcurrencyHost, owner: str
) -> "tuple[LeaseHandle | None, asyncio.Task[bool] | None]":
    """Watch for this owner losing its leases, without a renewal task per message.

    Returns ``(handle, waiter)``. The waiter completes only if the shared
    ``LeaseRenewer`` marks this owner's leases lost, so the caller can race it
    against the handler exactly as it raced the old per-message renewal task —
    but the actual renewing is done once for the whole process, in one
    pipelined round trip, instead of once per in-flight record.
    """
    renewer = host.lease_renewer
    if renewer is None:
        return None, None
    handle = renewer.register(owner)
    return handle, asyncio.ensure_future(handle.lost.wait())


def lease_guard_error(handle: "LeaseHandle | None") -> RuntimeError:
    """The failure to raise when a lease guard fires."""
    reason = getattr(handle, "reason", None) or (
        "Distributed concurrency lease guard stopped"
    )
    return RuntimeError(reason)


async def clear_retry_tracking(host: ConcurrencyHost, message_id: str) -> None:
    if not host.retry_manager:
        return
    try:
        await host.retry_manager.clear(message_id)
    except Exception as e:
        host.logger.error(
            "Failed to clear retry tracking for %s: %s", message_id, e
        )


async def get_retry_count(host: ConcurrencyHost, message_id: str) -> int:
    if not host.retry_manager:
        return 0
    return int(await host.retry_manager.get_count(message_id))


async def increment_retry_and_check(
    host: ConcurrencyHost, message_id: str
) -> tuple[int, bool]:
    if not host.retry_manager:
        return 0, False
    return await host.retry_manager.increment_and_check(
        message_id, messaging_env.max_delivery_attempts
    )


# ---------------------------------------------------------------------------
# ResourceGovernor-backed node-local gates (Phase 1 of the adaptive-concurrency
# plan). The distributed Redis lease stays sized to the *resolved ceiling*
# (never the adaptive value) — the lease is the cluster-wide cap, the gate
# below is the node-local cap; see plan section "Phase 1 — Indexing consumers".
# ---------------------------------------------------------------------------


def effective_index_tier(
    host: ConcurrencyHost, tier: ParseTier | None
) -> ParseTier:
    """The tier whose index pool this record actually routes to.

    Normally the record's own tier. When ``MAX_CONCURRENT_INDEXING`` is too
    small to split — a total of 1 cannot be two tiers each floored at 1 —
    ``resolve_ceilings`` collapses light to zero and everything routes to
    heavy, so the operator's total holds exactly. Routing a record to a
    zero-ceiling pool would not honour it anyway: ``AdmissionGate`` admits
    into an empty pool regardless of its limit (its deadlock guard), and the
    distributed lease rejects a limit below 1 outright.

    Idempotent, so callers may resolve once at the top of a message and pass
    the result down, or re-resolve locally.
    """
    resolved = tier if tier is not None else ParseTier.HEAVY
    governor = host.governor
    if (
        resolved is ParseTier.LIGHT
        and governor is not None
        and governor.ceilings.index_light == 0
    ):
        return ParseTier.HEAVY
    return resolved


def index_ceiling(host: ConcurrencyHost, tier: ParseTier | None = None) -> int:
    """Cluster-wide indexing lease limit for *tier* when a governor is
    present, else the legacy static env var — which was never split by tier,
    so it stays a single shared limit regardless of *tier*.

    Split for the same reason ``parse_ceiling`` is, one stage further up. An
    index permit is held for the record's whole lifetime, *including* the
    time it spends queued for a parse slot, so a single shared budget lets
    the slow tier set the fast tier's throughput: a bulk PDF upload fills
    every permit with records waiting on the handful of heavy-parse slots,
    and light records that would turn over in seconds never get admitted.
    """
    governor = host.governor
    if governor is not None:
        tier = effective_index_tier(host, tier)
        if tier is ParseTier.LIGHT and messaging_env.split_index_lease_pools:
            return governor.ceilings.index_light
        # On the shared pool this must be the *total* budget, matching what a
        # previous-build replica passes for the same pool — the Lua script
        # enforces whichever limit the caller sends.
        return (
            governor.ceilings.index_heavy
            if messaging_env.split_index_lease_pools
            else governor.ceilings.index
        )
    return messaging_env.max_concurrent_indexing


def index_lease_pool(tier: ParseTier | None) -> str:
    """Redis pool name for the cluster-wide indexing lease of *tier*.

    Heavy keeps the original ``indexing`` name so a rolling upgrade shares one
    pool with previous-build replicas. Light only moves to its own pool once
    ``INDEXING_SPLIT_LEASE_POOLS`` is on, because a previous-build replica
    admits *every* record into ``indexing`` at the full budget: while both
    builds run, a separate light pool is additive and the fleet can exceed
    MAX_CONCURRENT_INDEXING by ``index_light``.

    Off by default for one release. This costs almost nothing meanwhile — the
    per-tier split that actually prevents head-of-line blocking is the
    node-local ``INDEX_HEAVY``/``INDEX_LIGHT`` gate pair, which is per-process
    and unaffected. All this defers is the cluster-wide cap being split, so a
    light record waits only when the whole fleet budget is spent rather than
    behind heavy records specifically. Turn it on once no previous-build
    replica remains, then delete the flag.
    """
    if tier is ParseTier.LIGHT and messaging_env.split_index_lease_pools:
        return "indexing:light"
    return "indexing"


def parse_ceiling(host: ConcurrencyHost, tier: ParseTier | None = None) -> int:
    """Cluster-wide parsing lease limit for *tier* when a governor is
    present, else the legacy static env var — which was never split by
    tier, so it stays a single shared limit regardless of *tier*.

    Light must not share the heavy ceiling: that cap is sized for Docling
    RSS, and a Jira/Slack/Markdown parse waiting on it will never occupy
    more than a handful of local LIGHT_PARSE slots, so the node-local
    gate never sees demand and stays on its floor.
    """
    governor = host.governor
    if governor is not None:
        if tier is ParseTier.LIGHT:
            return governor.ceilings.light
        return governor.ceilings.heavy
    return messaging_env.max_concurrent_parsing


def parse_lease_pool(tier: ParseTier | None) -> str:
    """Redis pool name for the cluster-wide parsing lease of *tier*.

    Split by tier because the two ceilings differ: a light record must not
    consume one of the few heavy-parse leases a Docling PDF holds for
    minutes.
    """
    return "parsing:light" if tier is ParseTier.LIGHT else "parsing"


class GateWaiterToken:
    """Tracks whether one spawned task still counts toward
    ``pending_task_ceiling``'s backpressure check.

    A task counts as a "gate waiter" from the moment it's spawned (added to
    ``_active_futures``) until it is admitted through the local indexing
    gate/semaphore — the resource actually contended by every task racing
    to start, including ones still parked in retry-backoff or waiting on a
    distributed indexing lease. Once admitted, the task is doing real work
    and competing for parsing/CPU/etc. instead of queue space, so it must
    stop counting even though it stays in ``_active_futures`` until it
    finishes (that set backs shutdown draining and diagnostic logging, not
    backpressure).
    """

    __slots__ = ("_host", "_admitted", "_released")

    def __init__(self, host: ConcurrencyHost) -> None:
        self._host = host
        self._admitted = False
        self._released = False
        with host._futures_lock:
            host._gate_waiters += 1

    def admit(self) -> None:
        """Call once the local indexing gate/semaphore has been acquired."""
        if self._admitted or self._released:
            return
        self._admitted = True
        with self._host._futures_lock:
            self._host._gate_waiters -= 1

    def release(self) -> None:
        """Idempotent cleanup for the task's terminal state (call from the
        future-done callback). A no-op if ``admit()`` already ran — the
        waiter count was already decremented then."""
        if self._released:
            return
        self._released = True
        if not self._admitted:
            with self._host._futures_lock:
                self._host._gate_waiters -= 1


def index_gates_saturated(host: ConcurrencyHost) -> bool:
    """Whether every in-flight index permit this node can grant is already out.

    Backpressure used to count only tasks *queued* for admission, and a task
    stopped counting the moment it was admitted (``GateWaiterToken.admit``).
    That left the read loop blind to the state that actually matters: with the
    index pools full and nothing queued behind them — the steady state while a
    batch of slow records is in flight — the waiter count reads zero and the
    consumer keeps claiming messages it cannot start. Those claims are not
    free: on Redis Streams they sit in this consumer's PEL, and on Kafka they
    hold their partition.

    Without a governor there is one static semaphore and no per-pool
    occupancy to read, so this reports False and the waiter count remains the
    only signal — matching pre-governor behaviour exactly.
    """
    governor = host.governor
    if governor is None:
        return False
    return all(
        governor.gate(pool).in_use >= governor.gate(pool).limit
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)
    )


def get_gate_waiter_count(host: ConcurrencyHost) -> int:
    """Number of spawned tasks not yet admitted through the local indexing
    gate/semaphore — see ``GateWaiterToken``."""
    with host._futures_lock:
        return host._gate_waiters


def pending_task_ceiling(host: ConcurrencyHost) -> int:
    """How many tasks waiting for local indexing-gate admission (retry
    backoff, distributed lease wait, or the gate/semaphore itself — see
    ``GateWaiterToken``) the consumer allows before pausing partition/
    stream reads (Phase 6 of the adaptive-concurrency plan).

    An explicit ``MAX_PENDING_INDEXING_TASKS`` always wins. Otherwise, with a
    governor present the cap derives from the *resolved* index ceilings —
    which reflect this node's actual cgroup/CPU limits — rather than the
    static ``MAX_CONCURRENT_*`` env defaults baked into
    ``messaging_env.max_pending_indexing_tasks``, which would either overshoot
    a small container or undershoot a large one.

    Absolutely clamped as well as derived: a queued task holds only a parsed
    envelope (the download happens after admission), but each is still an
    asyncio task on the single worker loop, and prefetching thousands of
    stream entries a node cannot start on only delays their redelivery
    elsewhere.
    """
    if os.getenv("MAX_PENDING_INDEXING_TASKS"):
        return messaging_env.max_pending_indexing_tasks
    governor = host.governor
    if governor is not None:
        return max(
            _MIN_PENDING_INDEXING_TASKS,
            min(
                _MAX_PENDING_INDEXING_TASKS,
                governor.ceilings.index * _PENDING_TASKS_PER_INDEX_SLOT,
            ),
        )
    return messaging_env.max_pending_indexing_tasks


@dataclass
class Admission:
    """A permit taken from whichever primitive granted it.

    ``_release`` is bound at acquisition time to that primitive, so callers
    never branch on governor-vs-legacy. ``released`` makes releasing
    idempotent, which both stages need: the handler drops its index permit on
    INDEXING_COMPLETE and its parse permit on PARSING_COMPLETE, both well
    before the wrapper's ``finally`` runs, and a second release would hand
    back a permit the gate never issued — admitting one more record than the
    limit allows.
    """

    _release: Callable[[], None]
    tier: ParseTier | None = None
    cost: int = 1
    released: bool = False


async def acquire_index_slot(
    host: ConcurrencyHost, tier: ParseTier | None
) -> "Admission":
    """Acquire an active-pipeline permit for *tier*, routing heavy vs. light
    through the governor when one is configured, else falling back to the
    single legacy ``indexing_semaphore`` (pre-governor behaviour).

    The tier comes from the record event's own ``extension``/``mimeType``
    rather than the handler's START_PARSING event, because this permit is
    taken before the handler runs. ``classify`` resolves anything it does not
    recognise to HEAVY, so an unclassifiable record draws on the smaller
    budget rather than the one sized for fast records.
    """
    resolved_tier = effective_index_tier(host, tier)
    governor = host.governor
    if governor is not None:
        gate = governor.gate(index_pool(resolved_tier))
        # Checked, not discarded: acquire() returns False on timeout rather
        # than raising, so ignoring it would build an admission for a permit
        # the gate never issued and release it later, freeing someone else's.
        # No timeout is passed here, so False is unreachable today — the point
        # is that adding one can't silently corrupt the gate's accounting.
        if not await gate.acquire():
            raise RuntimeError(
                f"Index admission denied for pool {index_pool(resolved_tier).value}"
            )
        return Admission(tier=resolved_tier, _release=lambda: gate.release())

    legacy_semaphore = host.indexing_semaphore
    if legacy_semaphore is None:
        raise RuntimeError("No indexing concurrency primitive configured")
    await legacy_semaphore.acquire()
    return Admission(tier=resolved_tier, _release=legacy_semaphore.release)


def release_admission(admission: "Admission | None") -> bool:
    """Hand a permit back to the primitive that granted it.

    Returns whether *this* call was the one that released it, so a caller can
    keep its own ``*_held`` bookkeeping in step. A no-op for ``None`` and for
    an already-released permit, so callers can release unconditionally from a
    ``finally`` without an extra guard.
    """
    if admission is None or admission.released:
        return False
    admission.released = True
    admission._release()
    return True


async def acquire_parsing_slot(
    host: ConcurrencyHost,
    tier: ParseTier | None,
    size_bytes: int | None,
) -> "Admission":
    """Acquire a parsing permit, routing heavy vs. light through the
    governor when one is configured, else falling back to the single legacy
    ``parsing_semaphore`` (pre-governor behaviour, cost always 1).
    """
    governor = host.governor
    if governor is not None:
        resolved_tier = tier if tier is not None else ParseTier.HEAVY
        cost = parse_cost(resolved_tier, size_bytes)
        gate = governor.gate(gate_pool(resolved_tier))
        if not await gate.acquire(cost=cost):
            raise RuntimeError(
                f"Parsing admission denied for pool {gate_pool(resolved_tier).value}"
            )
        return Admission(tier=resolved_tier, cost=cost, _release=lambda: gate.release(cost))

    legacy_semaphore = host.parsing_semaphore
    if legacy_semaphore is None:
        raise RuntimeError("No parsing concurrency primitive configured")
    await legacy_semaphore.acquire()
    return Admission(cost=1, _release=legacy_semaphore.release)




def report_memory_incident_if_applicable(
    host: ConcurrencyHost, message_id: str, error: BaseException
) -> None:
    """Feed a real in-process allocation failure into the governor's fast
    incident path (``ResourceGovernor.report_memory_incident``) instead of
    waiting for the next periodic sample to notice the pressure it already
    caused.

    A cgroup OOM-kill usually SIGKILLs the process outright rather than
    raising ``MemoryError`` (that's what the ``BrokenProcessPool`` handlers
    in ``pdf_rasterizer``/``docling_processor`` are for), but some
    allocations still fail with a catchable ``MemoryError`` first — this is
    a cheap, unconditional backstop for that case.
    """
    if host.governor is None or not isinstance(error, MemoryError):
        return
    host.governor.report_memory_incident(f"MemoryError processing {message_id}")
