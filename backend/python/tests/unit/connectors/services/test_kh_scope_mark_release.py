"""Every knowledge hub scope mark a sync path takes is ended, or the connector
stays listed as syncing and is never served from its scopes again."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task


@pytest.mark.asyncio
async def test_a_declined_full_sync_ends_its_prep_mark() -> None:
    from app.connectors.services.event_service import EventService

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=None)
    graph.delete_sync_points_by_connector_id = AsyncMock(return_value=(0, True))
    graph.kh_scope_mark_stale = AsyncMock(return_value=7)
    svc = EventService(MagicMock(spec=logging.Logger), MagicMock(), graph)

    async def _declined(_lease: SyncLease, coro: object) -> None:
        coro.close()

    coordinator = MagicMock()
    coordinator.spawn = AsyncMock(side_effect=_declined)
    with patch.object(svc, "_ensure_connector", AsyncMock(return_value=MagicMock())), patch.object(
        svc, "_persist_pending_resync", AsyncMock()
    ):
        await svc._start_sync_with_lease(
            "slack", {"orgId": "o1", "connectorId": "c1", "fullSync": True}, SyncLease("c1", "tok", 1), coordinator
        )

    graph.kh_scope_sync_ended.assert_awaited_once_with("c1", 7)
    graph.kh_scope_stamp.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cancel_while_the_mark_is_taken_still_finalizes() -> None:
    marking = asyncio.Event()

    async def _slow_mark(_connector_id: str) -> int:
        marking.set()
        await asyncio.sleep(3600)
        return 1

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=None)
    graph.kh_scope_mark_stale = AsyncMock(side_effect=_slow_mark)
    released = asyncio.Event()
    coordinator = AsyncMock()
    coordinator.end = AsyncMock(side_effect=lambda _lease: released.set())
    lease = SyncLease("c1", "tok", 1)

    task = asyncio.create_task(
        run_sync_task(MagicMock(), "c1", graph, logging.getLogger("t"), lease=lease, coordinator=coordinator)
    )
    await marking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(released.wait(), timeout=1)
    coordinator.end.assert_awaited_once_with(lease)
