"""
Graph Provider Utilities

Shared utility functions for graph provider testing (polling, waiting, etc.).
These functions are provider-agnostic and work with any GraphProviderProtocol implementation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar, Dict, Any

from app.config.constants.arangodb import AppStatus
if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("test-graph-provider")

T = TypeVar("T")


async def async_poll_until(
    check_fn: Callable[[], Awaitable[T | None]],
    timeout: float,
    interval: float,
    description: str = "condition",
) -> T:
    """Poll async check_fn until it returns a truthy value or timeout seconds."""
    deadline = time.time() + timeout
    last: T | None = None
    while time.time() < deadline:
        last = await check_fn()
        if last:
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for {description} after {timeout}s. Last: {last!r}"
    )


async def wait_until_graph_condition(
    connector_id: str,
    *,
    check: Callable[[], Awaitable[bool]],
    timeout: int = 180,
    poll_interval: int = 10,
    description: str = "graph condition",
) -> None:
    """Poll until async check returns True (replaces PipeshubClient.wait_for_sync for graph)."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if await check():
            logger.info(
                "✅ %s complete for connector %s (attempt %d)",
                description, connector_id, attempt,
            )
            return
        logger.info(
            "⏳ Waiting for %s on connector %s (attempt %d, %.0fs remaining)...",
            description, connector_id, attempt, deadline - time.time(),
        )
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"Timed out waiting for {description} for connector {connector_id} after {timeout}s"
    )


async def sync_until_condition(
    connector_id: str,
    *,
    sync_fn: Callable[[], Awaitable[None]],
    check: Callable[[], Awaitable[bool]],
    timeout: int = 300,
    settle_sec: int = 15,
    retry_gap_sec: int = 15,
    description: str = "condition",
) -> None:
    """Settle for Drive Changes lag, sync, and re-sync until *check* passes.

    A single incremental sync right after a Drive write often misses the change
    (Changes API lag). Waiting on the graph alone cannot help — another sync is
    required. This helper sleeps *settle_sec*, runs *sync_fn*, evaluates *check*,
    and on failure waits *retry_gap_sec* before syncing again until *timeout*.
    """
    if settle_sec > 0:
        logger.info(
            "Waiting %ds for Drive Changes API settle before sync (%s on connector %s)...",
            settle_sec,
            description,
            connector_id,
        )
        await asyncio.sleep(settle_sec)

    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        remaining_for_attempt = max(deadline - time.time(), 0.0)
        try:
            await asyncio.wait_for(sync_fn(), timeout=remaining_for_attempt or None)
            passed = await asyncio.wait_for(check(), timeout=remaining_for_attempt or None)
        except Exception:
            logger.exception(
                "Attempt %d for %s on connector %s raised an error; will retry if time remains",
                attempt, description, connector_id,
            )
            passed = False
        if passed:
            logger.info(
                "✅ %s for connector %s (sync attempt %d)",
                description,
                connector_id,
                attempt,
            )
            return
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        gap = min(float(retry_gap_sec), remaining)
        logger.info(
            "⏳ %s not met after sync attempt %d on connector %s; "
            "re-syncing in %.0fs (%.0fs remaining)...",
            description,
            attempt,
            connector_id,
            gap,
            remaining,
        )
        await asyncio.sleep(gap)

    raise TimeoutError(
        f"Timed out waiting for {description} for connector {connector_id} "
        f"after {timeout}s ({attempt} sync attempt(s))"
    )


async def async_wait_for_stable_record_count(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    stability_checks: int = 4,
    interval: int = 10,
    max_rounds: int = 16,
) -> int:
    """Poll until record count is stable across stability_checks consecutive checks."""
    prev = await graph_provider.count_records(connector_id)
    stable = 0
    for _ in range(max_rounds):
        await asyncio.sleep(interval)
        current = await graph_provider.count_records(connector_id)
        if current == prev:
            stable += 1
            if stable >= stability_checks:
                return current
        else:
            logger.info(
                "Record count still settling: %d -> %d (connector %s)",
                prev, current, connector_id,
            )
            prev = current
            stable = 0
    return prev


async def wait_for_sync_completion(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    timeout: int = 300,
    poll_interval: int = 5,
    min_records: int | None = None,
    sync_start_timeout: int = 30,
) -> int:
    """
    Wait for sync to complete by polling connector status until IDLE, then read record count.

    Uses a two-phase approach to avoid a race where the first poll sees the
    pre-sync IDLE state before the backend has transitioned to SYNCING/FULL_SYNCING:

    1. **Phase 1** — wait up to *sync_start_timeout* seconds for status to leave IDLE.
       If it never leaves, assume the sync was fast enough to complete between the
       enable call and the first poll.
    2. **Phase 2** — wait for status to return to IDLE (the real post-sync IDLE).
    3. Query graph record count and optionally assert *min_records*.

    Args:
        pipeshub_client: Client for accessing connector API
        graph_provider: Graph provider for querying records
        connector_id: Connector ID to monitor
        timeout: Maximum seconds to wait for completion (default 300)
        poll_interval: Seconds between status polls (default 5)
        min_records: Minimum record count threshold (optional)
        sync_start_timeout: Max seconds to wait for sync to start (default 30)

    Returns:
        Record count after connector reports IDLE

    Raises:
        TimeoutError: If sync doesn't complete within timeout
        AssertionError: If min_records threshold not met
    """
    deadline = time.time() + timeout
    logger.info("⏳ Waiting for sync completion...")

    # Phase 1: wait for sync to actually start (leave IDLE).
    start_deadline = min(time.time() + sync_start_timeout, deadline)
    sync_observed = False
    while time.time() < start_deadline:
        connector = pipeshub_client.get_connector(connector_id)
        status = connector.get("status", "IDLE")
        if status != AppStatus.IDLE.value:
            logger.info("🔄 Sync started: status=%s", status)
            sync_observed = True
            break
        await asyncio.sleep(2)

    if not sync_observed:
        logger.info(
            "ℹ️ Connector remained IDLE for %ds — sync may have completed instantly",
            sync_start_timeout,
        )

    # Phase 2: wait for sync to finish (return to IDLE).
    while time.time() < deadline:
        connector = pipeshub_client.get_connector(connector_id)
        status = connector.get("status", "IDLE")

        if status == AppStatus.IDLE.value:
            logger.info("✅ Connector status is IDLE")
            break

        logger.info(
            "⏳ Connector status: %s, waiting... (%.0fs remaining)",
            status, deadline - time.time(),
        )
        await asyncio.sleep(poll_interval)
    else:
        raise TimeoutError(f"Connector did not reach IDLE status within {timeout}s")

    final_count = await graph_provider.count_records(connector_id)

    if min_records is not None and final_count < min_records:
        raise AssertionError(
            f"Expected at least {min_records} records, got {final_count}"
        )

    logger.info("✅ Sync complete: %d records", final_count)
    return final_count
