# pyright: ignore-file

"""Small helpers shared by the Notion integration tests."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from app.connectors.core.base.sync_point.sync_point import (  # type: ignore[import-not-found]
    generate_record_sync_point_key,
)
from app.models.entities import RecordType  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from connectors.notion.constants import SYNC_TIMEOUT  # type: ignore[import-not-found]

logger = logging.getLogger("notion-test-utils")


def indexing_filters(**values: bool) -> dict[str, Any]:
    """Wrap boolean indexing filters into the connector's ``filters.indexing.values`` shape.

    ``load_connector_filters`` reads ``filters.indexing.values.<key>``; the filters-sync endpoint
    stores the request ``filters`` verbatim, so a flat ``{"pages": False}`` lands at the wrong
    path and is silently ignored.
    """
    return {
        "indexing": {
            "values": {
                key: {"operator": "is", "type": "boolean", "value": value}
                for key, value in values.items()
            }
        }
    }


def pages_sync_point_key() -> str:
    """Sync-point key the connector writes for pages."""
    return generate_record_sync_point_key(RecordType.WEBPAGE.value, "notion_pages", "global")


def data_sources_sync_point_key() -> str:
    """Sync-point key the connector writes for data sources."""
    return generate_record_sync_point_key(
        RecordType.WEBPAGE.value, "notion_data_sources", "global"
    )


def restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Toggle off/on to trigger a fresh (incremental) sync run."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


async def incremental_sync(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    timeout: int = SYNC_TIMEOUT,
) -> int:
    """Run an incremental sync and wait for it to settle."""
    restart_sync(pipeshub_client, connector_id)
    return await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=timeout
    )


async def full_sync(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    timeout: int = SYNC_TIMEOUT,
) -> int:
    """Force a full sync.

    Required whenever scope should *shrink* or every record must be re-evaluated: the backend
    deletes the connector's sync points and sync edges first, so the connector re-enumerates
    from scratch and rebuilds ``BELONGS_TO`` only for entities the source still returns. That
    is the only way a trashed Notion page leaves ``scoped=True`` counts — the connector itself
    never deletes records.
    """
    pipeshub_client.resync_connector(connector_id, full_sync=True)
    return await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=timeout
    )


async def apply_indexing_filters(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    timeout: int = SYNC_TIMEOUT,
    **values: bool,
) -> int:
    """Set indexing filters and force a full re-sync so every record is re-evaluated.

    The backend sets ``pendingFullSync`` only when ``filters.sync`` changes (see the
    filters-sync route). Notion has no sync filters, so an indexing-only change leaves the
    checkpoint intact and the re-enable runs an *incremental* sync that skips every unchanged
    page — ``indexing_status`` would never flip. The explicit full sync deletes the sync points
    and re-enumerates, which is what actually applies the new filter.
    """
    pipeshub_client.update_connector_filters_sync_safe(
        connector_id, filters=indexing_filters(**values)
    )
    # Let the re-enable's incremental pass settle before forcing the full sync, so the two
    # runs cannot overlap.
    await wait_for_sync_completion(
        pipeshub_client, graph_provider, connector_id, timeout=timeout
    )
    return await full_sync(pipeshub_client, graph_provider, connector_id, timeout=timeout)


async def wait_for_search_visibility(
    helper: Any,
    object_type: str,
    external_id: str,
    *,
    min_last_edited: Optional[str] = None,
    after_edit: bool = False,
    timeout: int = 240,
    interval: int = 10,
) -> None:
    """Block until Notion's Search API reflects a create or an edit.

    The connector enumerates content solely through Search, whose index lags writes by seconds
    to minutes. Syncing before the index catches up means the connector legitimately does not
    see the change, so every mutation test must wait here first or it races the index.

    ``after_edit=True`` reads the object's current ``last_edited_time`` from Notion and waits
    for the index to catch up to it. That matters because the connector transforms records
    straight from the Search payload — a stale entry would re-apply the pre-edit title or
    parent, and its old timestamp keeps the record below the delta checkpoint entirely.
    """
    if after_edit and min_last_edited is None:
        live = (
            await helper.retrieve_page(external_id)
            if object_type == "page"
            else await helper.retrieve_data_source(external_id)
        )
        # An empty threshold would make every comparison below trivially true,
        # silently downgrading "wait for the edit to land" to "wait for
        # visibility" — the exact staleness this flag exists to catch.
        live_edited = live.get("last_edited_time")
        if not live_edited:
            raise AssertionError(
                f"Notion returned no last_edited_time for {object_type} {external_id}; "
                "cannot wait for the edit to be indexed"
            )
        min_last_edited = str(live_edited)

    async def visible() -> bool:
        for obj in await helper.search_objects(object_type):
            if str(obj.get("id")) != external_id:
                continue
            if min_last_edited is None:
                return True
            return str(obj.get("last_edited_time") or "") >= min_last_edited
        return False

    what = "visible" if min_last_edited is None else f"indexed at >= {min_last_edited}"
    await wait_until(
        visible,
        description=f"{object_type} {external_id} {what} in Notion search",
        timeout=timeout,
        interval=interval,
    )


async def wait_past_checkpoint(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    object_type: str,
    *,
    buffer_seconds: int = 5,
) -> None:
    """Sleep until the wall clock is past the connector's delta checkpoint minute.

    **Call this BEFORE mutating**, not after. Notion truncates ``last_edited_time`` to the
    minute and the connector's delta scan stops at ``last_edited_time <= last_sync_time``, so an
    object written during the checkpoint's own minute is invisible to every later incremental
    sync — permanently, because a timestamp never moves on its own. Waiting afterwards for the
    timestamp to exceed the checkpoint can therefore never succeed; the write has to happen in a
    later minute in the first place.
    """
    key = (
        pages_sync_point_key() if object_type == "page" else data_sources_sync_point_key()
    )
    sync_point = await graph_provider.get_sync_point(connector_id, key) or {}
    checkpoint = str(sync_point.get("last_sync_time") or "")
    if not checkpoint:
        return

    try:
        checkpoint_at = datetime.fromisoformat(checkpoint.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable sync checkpoint %r; not waiting", checkpoint)
        return

    target = checkpoint_at + timedelta(minutes=1, seconds=buffer_seconds)
    delay = (target - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        logger.info(
            "Waiting %.0fs so the next write lands after checkpoint minute %s",
            delay, checkpoint,
        )
        await asyncio.sleep(delay)


async def wait_until(
    check: Callable[[], Awaitable[bool]],
    *,
    description: str,
    timeout: int = 120,
    interval: int = 5,
) -> None:
    """Poll ``check`` until it returns True, or fail with ``description``."""
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Optional[Exception] = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            if await check():
                return
            last_error = None
        except Exception as e:  # noqa: BLE001 - transient source errors are expected while polling
            last_error = e
        await asyncio.sleep(interval)
    suffix = f" (last error: {last_error})" if last_error else ""
    raise AssertionError(f"Timed out after {timeout}s waiting for: {description}{suffix}")


async def scoped_external_ids(
    graph_provider: GraphProviderProtocol, connector_id: str, record_type: str
) -> set[str]:
    """External ids of in-scope (BELONGS_TO-guarded) records of one type."""
    records = await graph_provider.fetch_records_by_type(
        connector_id, record_type, scoped=True
    )
    return {
        str(r.get("externalRecordId"))
        for r in records
        if r.get("externalRecordId") is not None
    }
