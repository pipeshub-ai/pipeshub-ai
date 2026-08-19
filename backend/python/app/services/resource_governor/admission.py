"""Shared HTTP-admission helper for FastAPI routes fronted by a
ResourceGovernor-gated pool (the Parsing Service and the Docling Service both
gate a ``HEAVY_PARSE``/``LIGHT_PARSE`` pool in front of a request handler).

Factored out so the warn-then-wait-then-429 behaviour is identical across
services instead of being re-implemented per route module — see plan
section 1.4 (a queue timeout here must never be treated as a failure) and
Phase 4/5 of the adaptive-concurrency plan.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from app.utils.semaphore_logger import SemaphoreLogger

if TYPE_CHECKING:
    import logging

    from app.services.resource_governor.gate import AdmissionGate
    from app.services.resource_governor.models import ParseTier

# Defaults mirroring the original parsing-route constants; callers may pass
# their own service-appropriate values (see api/routes/parsing.py and
# services/docling/docling_service.py).
DEFAULT_QUEUE_WAIT_WARN_SECONDS = 10.0
DEFAULT_GATE_TIMEOUT_SECONDS = 120.0
DEFAULT_BACKPRESSURE_RETRY_AFTER_SECONDS = 5


async def _cancel_and_release_if_granted(
    acquire_task: "asyncio.Task[bool]", gate: "AdmissionGate", cost: int
) -> None:
    """On our own cancellation, cancel the shielded *acquire_task* and, if it
    had already been granted a permit in that race, release it — otherwise
    the gate's ``in_use`` permanently overcounts by ``cost`` with nothing
    left to release it."""
    acquire_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        if await acquire_task:
            gate.release(cost)


async def acquire_gate_with_backpressure(
    gate: "AdmissionGate",
    cost: int,
    tier: ParseTier,
    message_id: str,
    *,
    logger: logging.Logger,
    log_prefix: str,
    queue_wait_warn_seconds: float = DEFAULT_QUEUE_WAIT_WARN_SECONDS,
    gate_timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
) -> bool:
    """Acquire *cost* permits from *gate*, logging when the wait indicates
    saturation.

    Returns True once admitted. Returns False if *gate_timeout_seconds*
    elapses with no free slot — the caller should respond with 429
    backpressure (``Retry-After`` + a backpressure error code) so the
    client's own longer-horizon retry/back-off takes over instead of the
    request queuing here forever.
    """
    max_slots = gate.limit
    SemaphoreLogger.log_semaphore_acquire_attempt(
        f"{log_prefix}:{tier.value}", message_id, max_slots - gate.in_use, max_slots, 0, 0
    )
    start = time.monotonic()
    # gate.acquire() already carries its own timeout and never re-joins a
    # FIFO waiter list on retry (unlike asyncio.Semaphore) — it re-checks
    # admission in place — so shielding here only protects the in-flight
    # acquire from the *warn* timeout below, letting it keep running to its
    # own deadline instead of being cancelled and losing accrued wait time.
    acquire_task = asyncio.ensure_future(gate.acquire(cost=cost, timeout=gate_timeout_seconds))
    try:
        admitted = await asyncio.wait_for(
            asyncio.shield(acquire_task), timeout=queue_wait_warn_seconds
        )
    except asyncio.CancelledError:
        # shield() only protects acquire_task from wait_for's own timeout —
        # it does nothing to stop an external cancellation of *this*
        # coroutine (e.g. the caller's request task cancelled on a client
        # disconnect) from propagating past it while acquire_task keeps
        # running detached. Cancel it here and, if it had already been
        # granted a permit in that race, release it — otherwise the gate's
        # in_use permanently overcounts by `cost` with nothing left to
        # release it, shrinking real capacity by one permit every time a
        # caller is cancelled during this wait.
        await _cancel_and_release_if_granted(acquire_task, gate, cost)
        raise
    except asyncio.TimeoutError:
        # in_use well below limit while a request still waits means the
        # concurrency limit itself isn't the bottleneck — most likely the
        # pool's StartRateLimiter (see gate.py) is throttling how fast new
        # work is admitted, independent of MAX_CONCURRENT_*. Named directly
        # here so this is diagnosable from logs alone.
        likely_rate_limited = gate.in_use < max_slots
        logger.warning(
            "%s saturated: %s request waited >%.0fs for a slot "
            "(in_use=%d/%d)%s; still waiting up to %.0fs total",
            log_prefix, tier.value, queue_wait_warn_seconds, gate.in_use, max_slots,
            " — capacity is free; likely throttled by the start-rate limiter, not the concurrency limit"
            if likely_rate_limited else "",
            gate_timeout_seconds,
        )
        try:
            # Shielded for the same reason as the wait above: our own
            # cancellation must not directly cancel acquire_task before we
            # get a chance to release a permit it may have already won.
            admitted = await asyncio.shield(acquire_task)
        except asyncio.CancelledError:
            await _cancel_and_release_if_granted(acquire_task, gate, cost)
            raise
        if not admitted:
            logger.warning(
                "%s saturated: %s gate timeout after %.0fs (in_use=%d/%d); "
                "returning 429 backpressure to let the caller retry/back off",
                log_prefix, tier.value, gate_timeout_seconds, gate.in_use, max_slots,
            )
            SemaphoreLogger.log_message_error(
                message_id, f"{log_prefix} gate timeout — 429 backpressure"
            )
            return False

    wait_ms = (time.monotonic() - start) * 1000
    SemaphoreLogger.log_semaphore_acquired(
        message_id, max_slots - gate.in_use, max_slots, 0, 0, wait_time_ms=wait_ms
    )
    if wait_ms >= 1000:
        logger.info(
            "%s slot acquired after %.0fms queue wait (tier=%s in_use=%d/%d)",
            log_prefix, wait_ms, tier.value, gate.in_use, max_slots,
        )
    return True
