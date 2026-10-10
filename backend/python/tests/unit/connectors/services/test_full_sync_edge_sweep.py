"""A full sync tags the connector's edges and leaves them in place; only a sync that
succeeds removes the ones it did not write again (FS-01).

It used to delete every sync edge before the sync ran, so a sync that then failed
left every record with no hierarchy, inheritance or grant: dark for everyone.
"""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.sync.sync_coordinator import Admission, SyncLease
from app.connectors.services.event_service import EventService


class _Coordinator:
    def __init__(self) -> None:
        self.lease: SyncLease | None = None
        self.spawn = AsyncMock(return_value=MagicMock(name="task"))
        self.end = AsyncMock()
        self.is_running_here = MagicMock(return_value=False)
        self.is_running = AsyncMock(return_value=False)
        self.reports_liveness = False

    async def begin(self, connector_id: str, *, org_id: str | None = None,
                    message_ts_ms: int | None = None) -> tuple[Admission, SyncLease]:
        self.lease = SyncLease(connector_id, "tok", 1)
        return Admission.GRANTED, self.lease

    def held_since_ms(self, connector_id: str) -> None:
        return None

    def stopped_at_ms(self, connector_id: str) -> None:
        return None


def _service() -> EventService:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": "c1", "isActive": True})
    graph.update_node = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock()
    graph.delete_sync_points_by_connector_id = AsyncMock(return_value=(2, True))
    graph.delete_connector_sync_edges = AsyncMock(return_value=(9, True))
    graph.mark_connector_sync_edges = AsyncMock(return_value=(9, True))
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    return EventService(MagicMock(spec=logging.Logger), container, graph)


async def _full_sync(svc: EventService) -> MagicMock:
    """Runs the start path; returns the run_sync_task stand-in to read its arguments."""
    run_sync_task = MagicMock(return_value=MagicMock(name="coroutine"))
    with patch("app.connectors.services.event_service.get_coordinator", return_value=_Coordinator()), \
            patch("app.connectors.services.event_service.run_sync_task", run_sync_task), \
            patch.object(svc, "_ensure_connector", AsyncMock(return_value=MagicMock())), \
            patch.object(svc, "_update_app_status", AsyncMock()):
        assert await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True})
    run_sync_task.assert_called_once()
    return run_sync_task


@pytest.mark.asyncio
async def test_the_prep_deletes_no_edge_and_hands_the_tag_to_the_sync() -> None:
    svc = _service()

    run_sync_task = await _full_sync(svc)

    svc.graph_provider.delete_connector_sync_edges.assert_not_awaited()
    svc.graph_provider.mark_connector_sync_edges.assert_awaited_once()
    generation = svc.graph_provider.mark_connector_sync_edges.await_args.kwargs["generation"]
    assert run_sync_task.call_args.kwargs["sweep_generation"] == generation


@pytest.mark.asyncio
async def test_sync_points_that_could_not_be_deleted_mean_no_sweep() -> None:
    """JIRA-DC-02: the sync then skips unchanged items, and a sweep would take their edges."""
    svc = _service()
    svc.graph_provider.delete_sync_points_by_connector_id = AsyncMock(return_value=(0, False))

    run_sync_task = await _full_sync(svc)

    svc.graph_provider.mark_connector_sync_edges.assert_not_awaited()
    svc.graph_provider.delete_connector_sync_edges.assert_not_awaited()
    assert run_sync_task.call_args.kwargs["sweep_generation"] is None


@pytest.mark.asyncio
async def test_a_failed_tag_means_no_sweep() -> None:
    svc = _service()
    svc.graph_provider.mark_connector_sync_edges = AsyncMock(return_value=(4, False))

    run_sync_task = await _full_sync(svc)

    assert run_sync_task.call_args.kwargs["sweep_generation"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", [AsyncMock(return_value=(4, False)), AsyncMock(side_effect=RuntimeError("oom"))])
async def test_a_tag_that_failed_part_way_is_cleared(mark) -> None:
    """R1-24: the labels or collections already tagged must not keep the tag."""
    svc = _service()
    svc.graph_provider.mark_connector_sync_edges = mark
    svc.graph_provider.clear_connector_sync_edge_tags = AsyncMock(return_value=(4, True))

    await _full_sync(svc)

    generation = mark.await_args.kwargs["generation"]
    svc.graph_provider.clear_connector_sync_edge_tags.assert_awaited_once_with(
        connector_id="c1", generation=generation
    )
