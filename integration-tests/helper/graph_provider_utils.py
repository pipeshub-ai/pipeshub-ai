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
) -> int:
    """
    Wait for sync to complete by polling connector status until IDLE, then read record count.

    1. Poll connector.status until it becomes IDLE (not SYNCING/FULL_SYNCING).
    2. Query graph record count once (sync is awaited before IDLE; no extra settle wait).
    3. Optionally assert min_records threshold.

    Args:
        pipeshub_client: Client for accessing connector API
        graph_provider: Graph provider for querying records
        connector_id: Connector ID to monitor
        timeout: Maximum seconds to wait for completion (default 300)
        poll_interval: Seconds between status polls (default 5)
        min_records: Minimum record count threshold (optional)

    Returns:
        Record count after connector reports IDLE

    Raises:
        TimeoutError: If sync doesn't complete within timeout
        AssertionError: If min_records threshold not met

    Example:
        final_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=10, timeout=180
        )
    """
    deadline = time.time() + timeout
    logger.info("⏳ Waiting for sync completion for connector %s", connector_id)

    while time.time() < deadline:
        connector = pipeshub_client.get_connector(connector_id)
        status = connector.get("status", "IDLE")

        if status == AppStatus.IDLE.value:
            logger.info("✅ Connector %s status is IDLE", connector_id)
            break

        logger.info(
            "⏳ Connector %s status: %s, waiting... (%.0fs remaining)",
            connector_id, status, deadline - time.time(),
        )
        await asyncio.sleep(poll_interval)
    else:
        raise TimeoutError(f"Connector {connector_id} did not reach IDLE status within {timeout}s")

    final_count = await graph_provider.count_records(connector_id)

    if min_records is not None and final_count < min_records:
        raise AssertionError(
            f"Expected at least {min_records} records, got {final_count} "
            f"for connector {connector_id}"
        )

    logger.info(
        "✅ Sync complete for connector %s: %d records",
        connector_id, final_count
    )
    return final_count
