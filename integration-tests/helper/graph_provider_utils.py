"""
Graph Provider Utilities

Shared utility functions for graph provider testing (polling, waiting, etc.).
These functions are provider-agnostic and work with any GraphProviderProtocol implementation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

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
