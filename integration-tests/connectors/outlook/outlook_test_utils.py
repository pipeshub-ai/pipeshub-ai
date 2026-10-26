# pyright: ignore-file

"""Source-side helpers for Outlook integration tests: polling, sync runs and filter values."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger("outlook-test-utils")


async def wait_until_outlook_condition(
    check_fn: Callable[[], Awaitable[bool]],
    *,
    timeout: int = 120,
    poll_interval: int = 5,
    description: str = "Graph API condition",
) -> None:
    """Poll ``check_fn`` until it returns truthy or ``timeout`` elapses.

    Use for "is the resource visible at source yet" gating before triggering a sync, so a later
    graph-DB failure means "synced wrong", not "source not ready".
    """
    start = time.time()
    deadline = start + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            if await check_fn():
                logger.info("✅ %s (attempt %d, %.1fs)", description, attempt, time.time() - start)
                return
        except Exception as e:
            logger.warning("⏳ %s check failed (attempt %d): %s", description, attempt, e)
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_interval, remaining))
    raise TimeoutError(f"Timed out waiting for {description} after {timeout}s ({attempt} attempts)")


_RUNNING_STATUSES = frozenset({"SYNCING", "FULL_SYNCING", "QUEUED"})
# Stamped on the connector when a run starts and ends: a run shorter than one status poll
# still moves them.
_RUN_MARKS = ("lastSyncStartedAtTimestamp", "lastSyncFinishedAtTimestamp")


async def run_sync_and_wait(
    pipeshub_client: Any,
    graph_provider: Any,
    connector_id: str,
    trigger: Callable[[], None],
    *,
    description: str,
    start_timeout: int = 120,
    timeout: int = 900,
) -> int:
    """Run ``trigger`` and return once the sync it started has finished.

    The record counts cannot tell a sync that has not started from one that changed nothing, and
    most phases here mutate only a few records. So the run must be seen: the status leaving IDLE,
    or a last-sync timestamp moving (a run that finished between two polls). A trigger that never
    starts a sync fails loudly instead of letting the phase assert stale data.
    """
    from helper.graph_provider_utils import (
        wait_for_sync_completion,
    )

    before = pipeshub_client.get_connector(connector_id)
    trigger()
    start = time.time()
    seen = None
    while time.time() - start < start_timeout:
        doc = pipeshub_client.get_connector(connector_id)
        if doc.get("status") in _RUNNING_STATUSES:
            seen = doc.get("status")
            break
        moved = [k for k in _RUN_MARKS if (doc.get(k) or 0) > (before.get(k) or 0)]
        if moved:
            seen = f"already ran ({', '.join(moved)} advanced)"
            break
        await asyncio.sleep(1)
    if seen is None:
        raise AssertionError(
            f"{description}: no sync started within {start_timeout}s of the trigger "
            "(status never left IDLE and the last-sync timestamps did not move)"
        )
    logger.info("%s: sync running (%s)", description, seen)
    while time.time() - start < timeout:
        if pipeshub_client.get_connector(connector_id).get("status") not in _RUNNING_STATUSES:
            break
        await asyncio.sleep(3)
    else:
        raise TimeoutError(f"{description}: sync still running after {timeout}s")
    count = await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id,
        timeout=max(60, int(timeout - (time.time() - start))), sync_start_timeout=2,
    )
    logger.info("%s: sync finished in %.0fs (%d records)", description, time.time() - start, count)
    return count


async def admitted(graph_provider: Any, user_graph_id: str, org_id: str, node_ids: list[str]) -> frozenset[str]:
    """The subset of ``node_ids`` the product's access check lets this user read.

    ``check_access`` is the one batch check every record read, search and listing goes through,
    so this asserts reachability rather than the presence of particular edges.
    """
    return (await graph_provider.check_access(user_graph_id, org_id, node_ids=node_ids)).node_ids


def sync_filters(**values: Any) -> dict[str, Any]:
    """Wrap filter fields into the connector's ``config.filters.sync.values`` shape.

    The connector reads ``config.filters.sync.values.<key>`` (see ``load_connector_filters``);
    the filters-sync endpoint stores the request ``filters`` verbatim, so the payload must already
    carry the ``sync.values`` nesting.
    """
    return {"sync": {"values": values}}


def list_value(operator: str, values: list[Any]) -> dict[str, Any]:
    """A multiselect list filter value (e.g. folders/users/groups)."""
    return {"operator": operator, "type": "list", "value": values}


def bool_value(value: bool) -> dict[str, Any]:
    """A boolean filter value (e.g. custom_folders / indexing toggles)."""
    return {"operator": "is", "type": "boolean", "value": value}


def datetime_after(epoch_ms: int) -> dict[str, Any]:
    """A received_date is_after filter (start inclusive, no end)."""
    return {"operator": "is_after", "type": "datetime", "value": {"start": epoch_ms, "end": None}}
