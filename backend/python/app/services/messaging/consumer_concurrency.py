"""Shared distributed-concurrency plumbing for the Kafka and Redis Streams
indexing consumers.

Both consumers run a worker thread with its own event loop for message
processing, while the ``DistributedConcurrencyManager`` (Redis-backed) and
``RetryManager`` are only safe to call from the main loop. Both consumers
therefore need the exact same bridging, lease-acquire/release/renew, and
retry-tracking logic — previously duplicated near-verbatim in both files.

Functions here take the consumer instance (``host``) as their first argument
and read/write its existing attributes (``main_loop``, ``running``,
``concurrency_manager``, ``retry_manager``, ``logger``,
``_distributed_log_times``) rather than being methods on a shared base class.
This keeps the fix in one place without changing either consumer's class
hierarchy or the (sometimes name-mangled) method names tests patch directly.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.services.messaging.config import messaging_env
from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.resource_governor import gate_pool, parse_cost
from app.services.resource_governor.models import ParseTier

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.messaging.retry_manager import RetryManager
    from app.services.resource_governor import ResourceGovernor

_MAIN_LOOP_OP_TIMEOUT = 5.0


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


def schedule_on_main_loop(
    host: ConcurrencyHost, coro: Any
) -> "asyncio.Future[None]":
    """Bridge a long-lived coroutine (e.g. the renewal loop) onto the main loop.

    Unlike ``bridge_to_main_loop``, the caller does not want to block until
    the coroutine finishes — it returns a future/task the caller can await
    or cancel independently.
    """
    current_loop = asyncio.get_running_loop()
    main_loop = host.main_loop
    if main_loop is not None and current_loop is not main_loop:
        if not main_loop.is_running():
            coro.close()
            raise RuntimeError("Main event loop is not running")
        try:
            thread_future = asyncio.run_coroutine_threadsafe(coro, main_loop)
        except BaseException:
            coro.close()
            raise
        return asyncio.wrap_future(thread_future)
    return asyncio.create_task(coro)


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


async def acquire_distributed_slot(
    host: ConcurrencyHost,
    pool: str,
    owner: str,
    limit: int,
    deadline_seconds: float | None = None,
) -> bool:
    """Try to acquire a distributed lease on ``pool`` for ``owner``.

    With ``deadline_seconds`` set, gives up (returning False) after that many
    seconds of polling instead of waiting indefinitely — used for the
    per-record lease, which is contended by duplicate in-flight deliveries of
    the *same* record and must not convoy the whole pipeline while already
    holding the outer indexing slot/semaphore (see the indexing-slot pool,
    which has no deadline: that lease is only contended by genuinely
    concurrent *different* records and should wait).
    """
    manager = host.concurrency_manager
    if manager is None:
        return True

    start = time.monotonic()
    while host.running:
        try:
            acquired = await bridge_to_main_loop(
                host,
                manager.try_acquire(
                    pool,
                    owner,
                    limit,
                    messaging_env.concurrency_lease_seconds,
                ),
            )
            if acquired:
                host._distributed_log_times.pop(
                    _normalize_operation(f"acquire:{pool}"), None
                )
                return True
        except Exception as exc:
            log_distributed_error(host, f"acquire:{pool}", exc)

        if (
            deadline_seconds is not None
            and time.monotonic() - start >= deadline_seconds
        ):
            return False

        await asyncio.sleep(messaging_env.concurrency_acquire_poll_seconds)

    return False


async def release_distributed_slot(
    host: ConcurrencyHost, pool: str, owner: str
) -> None:
    manager = host.concurrency_manager
    if manager is None:
        return
    try:
        await bridge_to_main_loop(host, manager.release(pool, owner))
    except Exception as exc:
        log_distributed_error(host, f"release:{pool}", exc)


async def renew_distributed_slots(
    host: ConcurrencyHost, leases: DistributedLeaseSet
) -> None:
    """Periodically renew every lease in ``leases`` until one is lost.

    Runs as a background task alongside the handler; raises (rather than
    returning) when a lease can't be renewed before its safety deadline, so
    the caller can cancel processing instead of continuing to hold a slot
    the rest of the fleet may have already reassigned.
    """
    manager = host.concurrency_manager
    if manager is None:
        return

    lease_seconds = messaging_env.concurrency_lease_seconds
    configured_interval = messaging_env.concurrency_renew_interval_seconds
    interval = max(0.1, min(configured_interval, lease_seconds / 3))
    renewal_deadline = max(0.1, lease_seconds - interval)
    last_successful_renewal = time.monotonic()

    while True:
        await asyncio.sleep(interval)
        renewal_error: Exception | None = None
        for pool, owner in leases.snapshot():
            try:
                renewed = await bridge_to_main_loop(
                    host, manager.renew(pool, owner, lease_seconds)
                )
            except Exception as exc:
                renewal_error = exc
                break
            if not renewed and leases.owns(pool, owner):
                raise RuntimeError(f"Lost distributed {pool} concurrency lease")

        if renewal_error is None:
            last_successful_renewal = time.monotonic()
            continue

        log_distributed_error(host, "renew", renewal_error)
        if time.monotonic() - last_successful_renewal >= renewal_deadline:
            raise RuntimeError(
                "Distributed concurrency lease could not be renewed "
                "before its safety deadline"
            ) from renewal_error


def start_distributed_renewal(
    host: ConcurrencyHost, leases: DistributedLeaseSet
) -> "asyncio.Future[None]":
    renewal_coro = renew_distributed_slots(host, leases)
    return schedule_on_main_loop(host, renewal_coro)


async def clear_retry_tracking(host: ConcurrencyHost, message_id: str) -> None:
    if not host.retry_manager:
        return
    try:
        await bridge_to_main_loop(host, host.retry_manager.clear(message_id))
    except Exception as e:
        host.logger.error(
            "Failed to clear retry tracking for %s: %s", message_id, e
        )


async def get_retry_count(host: ConcurrencyHost, message_id: str) -> int:
    if not host.retry_manager:
        return 0
    return int(
        await bridge_to_main_loop(host, host.retry_manager.get_count(message_id))
    )


async def increment_retry_and_check(
    host: ConcurrencyHost, message_id: str
) -> tuple[int, bool]:
    if not host.retry_manager:
        return 0, False
    return await bridge_to_main_loop(
        host,
        host.retry_manager.increment_and_check(
            message_id, messaging_env.max_delivery_attempts
        ),
    )


# ---------------------------------------------------------------------------
# ResourceGovernor-backed node-local gates (Phase 1 of the adaptive-concurrency
# plan). The distributed Redis lease stays sized to the *resolved ceiling*
# (never the adaptive value) — the lease is the cluster-wide cap, the gate
# below is the node-local cap; see plan section "Phase 1 — Indexing consumers".
# ---------------------------------------------------------------------------


def index_ceiling(host: ConcurrencyHost) -> int:
    """Cluster-wide indexing lease limit when a governor is present, else the
    legacy static env var.

    One number for heavy and light records together: the active-pipeline
    permit bounds how many records are in flight, not what any of them
    costs, so there is nothing to split by tier (see policy
    ``_is_index_pool``).
    """
    governor = host.governor
    if governor is not None:
        return governor.ceilings.index
    return messaging_env.max_concurrent_indexing


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
    governor present the cap derives from the *resolved* index/heavy-parse
    ceilings — which reflect this node's actual cgroup/CPU limits — rather
    than the static ``MAX_CONCURRENT_*`` env defaults baked into
    ``messaging_env.max_pending_indexing_tasks``, which would either overshoot
    a small container or undershoot a large one.
    """
    if os.getenv("MAX_PENDING_INDEXING_TASKS"):
        return messaging_env.max_pending_indexing_tasks
    governor = host.governor
    if governor is not None:
        ceilings = governor.ceilings
        return max(ceilings.index, ceilings.heavy) * 4
    return messaging_env.max_pending_indexing_tasks


@dataclass
class ParsingAdmission:
    """What was acquired for one message's parsing phase, so release can
    hand back exactly what was taken.

    ``_release`` is a closure bound at acquisition time to whichever
    primitive (governor gate or legacy semaphore) actually granted the
    permit, so ``release_parsing_slot`` doesn't need to branch on the
    primitive's type.
    """

    cost: int
    _release: Callable[[], None]


async def acquire_parsing_slot(
    host: ConcurrencyHost,
    tier: ParseTier | None,
    size_bytes: int | None,
) -> ParsingAdmission:
    """Acquire a parsing permit, routing heavy vs. light through the
    governor when one is configured, else falling back to the single legacy
    ``parsing_semaphore`` (pre-governor behaviour, cost always 1).
    """
    governor = host.governor
    if governor is not None:
        resolved_tier = tier if tier is not None else ParseTier.HEAVY
        cost = parse_cost(resolved_tier, size_bytes)
        gate = governor.gate(gate_pool(resolved_tier))
        await gate.acquire(cost=cost)
        return ParsingAdmission(cost=cost, _release=lambda: gate.release(cost))

    legacy_semaphore = host.parsing_semaphore
    if legacy_semaphore is None:
        raise RuntimeError("No parsing concurrency primitive configured")
    await legacy_semaphore.acquire()
    return ParsingAdmission(cost=1, _release=legacy_semaphore.release)


def release_parsing_slot(admission: ParsingAdmission | None) -> None:
    """Release a permit acquired via ``acquire_parsing_slot``. A no-op when
    ``admission`` is ``None`` so callers can release unconditionally from a
    ``finally`` block without an extra guard."""
    if admission is None:
        return
    admission._release()


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
