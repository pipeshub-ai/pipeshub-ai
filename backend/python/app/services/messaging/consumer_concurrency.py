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
import time
from typing import TYPE_CHECKING, Any, Protocol

from app.services.messaging.config import messaging_env
from app.services.messaging.distributed_concurrency import DistributedLeaseSet

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.distributed_concurrency import (
        DistributedConcurrencyManager,
    )
    from app.services.messaging.retry_manager import RetryManager

_MAIN_LOOP_OP_TIMEOUT = 5.0


class ConcurrencyHost(Protocol):
    """Structural type for the attributes these helpers rely on."""

    logger: "Logger"
    running: bool
    main_loop: asyncio.AbstractEventLoop | None
    concurrency_manager: "DistributedConcurrencyManager | None"
    retry_manager: "RetryManager | None"
    _distributed_log_times: dict[str, float]


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
