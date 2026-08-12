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
    from app.models.entities import Record
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


def _connector_status(pipeshub_client: "PipeshubClient", connector_id: str) -> str:
    return pipeshub_client.get_connector(connector_id).get("status", AppStatus.IDLE.value)


async def _record_counts(
    graph_provider: "GraphProviderProtocol", connector_id: str,
) -> tuple[int, int]:
    """``(total, scoped)`` record counts.

    Total counts every Record node; scoped counts only those with a live ``BELONGS_TO`` →
    RecordGroup edge. They diverge after a filter narrows: a full sync rewrites those edges
    but never deletes nodes, so the total alone cannot show a scope change.
    """
    return (
        await graph_provider.count_records(connector_id),
        await graph_provider.count_records(connector_id, scoped=True),
    )


async def wait_for_sync_completion(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    timeout: int = 300,
    poll_interval: int = 5,
    min_records: int | None = None,
    sync_start_timeout: int = 15,
    settle_checks: int = 3,
    settle_interval: int = 4,
) -> int:
    """
    Wait for a sync to finish and for the graph to stop changing, then return the record count.

    ``status == IDLE`` alone is **not** proof that a sync has finished. Two backend
    behaviours make it lie, and both produced intermittent failures:

    * The toggle/resync API publishes an event and returns; the connector service may
      set ``SYNCING`` seconds later. A status poll in that window sees the *pre*-sync IDLE.
    * ``SyncTaskManager.start_sync`` cancels an in-flight sync before spawning the new one,
      and the cancelled task's ``finally`` writes ``IDLE`` *after* the new sync's ``SYNCING``
      was written — so the whole replacement sync runs while the app doc reads IDLE.

    So IDLE is treated as a hint, and completion is confirmed by the observable effect: the
    total **and** scoped record counts must hold steady across *settle_checks* consecutive
    polls **with the status still IDLE the entire time**. Both are needed — a filter-narrowing
    sync only moves the scoped count. If the status leaves IDLE mid-settle (a late-started or
    restarted sync), the wait restarts rather than returning a mid-sync graph.

    Args:
        pipeshub_client: Client for accessing connector API
        graph_provider: Graph provider for querying records
        connector_id: Connector ID to monitor
        timeout: Maximum seconds to wait for completion (default 300)
        poll_interval: Seconds between status polls while syncing (default 5)
        min_records: Minimum record count threshold (optional)
        sync_start_timeout: Max seconds to spend trying to *observe* the sync start
            (default 15). Only picks how long the settle must stay quiet — a sync that
            starts later is still caught by the status check inside the settle loop, which
            restarts the wait. Kept short because a small incremental sync routinely
            finishes before the first poll, and waiting on it is pure dead time.
        settle_checks: Consecutive unchanged record counts required (default 3)
        settle_interval: Seconds between settle polls (default 4)

    Returns:
        Total record-node count once the graph has settled. Callers that need filter-aware
        counts should read ``count_records(..., scoped=True)`` themselves.

    Raises:
        TimeoutError: If sync doesn't complete/settle within timeout
        AssertionError: If min_records threshold not met
    """
    deadline = time.time() + timeout
    logger.info("⏳ Waiting for sync completion...")

    # Best-effort observation that the sync started. Never trusted as proof of the
    # opposite — see the docstring on why "still IDLE" does not mean "already done".
    start_deadline = min(time.time() + sync_start_timeout, deadline)
    sync_observed = False
    while time.time() < start_deadline:
        status = _connector_status(pipeshub_client, connector_id)
        if status != AppStatus.IDLE.value:
            logger.info("🔄 Sync started: status=%s", status)
            sync_observed = True
            break
        await asyncio.sleep(2)

    # Not seeing the start means either the sync already finished (common for a small
    # incremental) or the status is stale — indistinguishable from here, so demand a longer
    # quiet period before believing the graph is final.
    required = settle_checks if sync_observed else settle_checks * 2
    if not sync_observed:
        logger.info(
            "ℹ️ Connector stayed IDLE for %ds — either the sync finished instantly or the "
            "status is stale; requiring %d stable record counts before continuing",
            sync_start_timeout, required,
        )

    settled: tuple[int, int] | None = None
    while time.time() < deadline and settled is None:
        # Wait for IDLE.
        while time.time() < deadline:
            status = _connector_status(pipeshub_client, connector_id)
            if status == AppStatus.IDLE.value:
                break
            logger.info(
                "⏳ Connector status: %s, waiting... (%.0fs remaining)",
                status, deadline - time.time(),
            )
            await asyncio.sleep(poll_interval)
        else:
            raise TimeoutError(f"Connector did not reach IDLE status within {timeout}s")

        # Confirm IDLE by holding the record counts steady while the status stays IDLE.
        stable = 0
        last = await _record_counts(graph_provider, connector_id)
        while stable < required and time.time() < deadline:
            await asyncio.sleep(settle_interval)
            status = _connector_status(pipeshub_client, connector_id)
            if status != AppStatus.IDLE.value:
                logger.info("🔄 Sync (re)started while settling: status=%s", status)
                break
            current = await _record_counts(graph_provider, connector_id)
            if current == last:
                stable += 1
            else:
                logger.info("Record counts still settling: %s -> %s", last, current)
                last = current
                stable = 0
        if stable >= required:
            settled = last

    if settled is None:
        raise TimeoutError(
            f"Connector {connector_id} sync did not settle within {timeout}s "
            f"(needed {required} stable record counts while IDLE)"
        )

    final_count, scoped_count = settled
    if min_records is not None and final_count < min_records:
        raise AssertionError(
            f"Expected at least {min_records} records, got {final_count}"
        )

    if scoped_count != final_count:
        logger.info(
            "✅ Sync complete: %d records (%d in scope — %d out-of-scope node(s) kept by an "
            "earlier filter or source deletion)",
            final_count, scoped_count, final_count - scoped_count,
        )
    else:
        logger.info("✅ Sync complete: %d records", final_count)
    return final_count


async def apply_filter_full_sync(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    filters: Dict[str, Any],
    *,
    timeout: int = 300,
) -> int:
    """Set a full filter payload then force a full sync (wipes+recreates sync edges).

    A full sync is required for scope *narrowing* to be reflected: it strips
    ``BELONGS_TO`` connector-wide and recreates it only for in-scope entities, so
    entities that left the filter drop out of BELONGS_TO-guarded counts.

    Changing the filter sets the backend ``pendingFullSync`` flag, so the re-enable that
    ``update_connector_filters_sync_safe`` performs is itself a full sync — no separate
    ``resync`` is needed (that would just run a redundant second full sync).

    ``filters`` must already carry the ``sync.values`` nesting the connector reads
    (see ``load_connector_filters``); the filters-sync endpoint stores the payload
    verbatim, so a flat dict is written to the wrong path and silently ignored.
    """
    pipeshub_client.update_connector_filters_sync_safe(connector_id, filters=filters)
    return await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=timeout,
    )


# =============================================================================
# Waits keyed on one record — safe while another run mutates the same workspace
# =============================================================================


async def wait_for_record_by_external_id(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    external_record_id: str,
    *,
    timeout: int = 120,
    interval: int = 5,
    description: str = "record",
) -> "Record":
    """Poll until the record with ``external_record_id`` exists, then return it."""
    return await async_poll_until(
        lambda: graph_provider.get_record_by_external_id(connector_id, external_record_id),
        timeout=timeout,
        interval=interval,
        description=f"{description} (external id {external_record_id})",
    )



# =============================================================================
# "Owned" records — the graph minus other runs' test data
# =============================================================================
#
# The arango and neo4j CI legs run against the *same* Jira site and Linear workspace, so
# one leg's mutation tests create and delete issues inside the other's sync window. Those
# issues are named with the connector's IT prefix; skipping them leaves only records the
# run owns, which is what every live-source-vs-graph assertion actually means.


def _record_name(record: Dict[str, Any]) -> str:
    return record.get("recordName") or record.get("name") or ""


async def owned_record_external_ids(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    prefix: str,
    record_type: str = "",
) -> set[str]:
    """External ids of this run's records (``record_type=''`` = every type)."""
    records = await graph_provider.fetch_records_by_type(connector_id, record_type)
    return {
        str(r["externalRecordId"])
        for r in records
        if r.get("externalRecordId") and prefix not in _record_name(r)
    }


async def count_owned_records(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    prefix: str,
    record_type: str = "",
) -> int:
    """Count this run's records (``record_type=''`` = every type)."""
    records = await graph_provider.fetch_records_by_type(connector_id, record_type)
    return sum(1 for r in records if prefix not in _record_name(r))
