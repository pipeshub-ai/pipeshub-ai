"""appDisabled must not sweep or clean up under a sync that is still unwinding."""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys


def _service(doc: dict):
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=doc)
    graph.batch_upsert_nodes = AsyncMock()
    graph.update_node = AsyncMock()
    graph.reset_indexing_status_for_connector = AsyncMock()
    container = MagicMock()
    container.connectors_map = {}
    return EntityEventService(MagicMock(spec=logging.Logger), graph, container)


PAYLOAD = {"orgId": "o1", "apps": ["MinIO"], "connectorId": "c1"}


class TestDisableWaitsForTheSync:
    @pytest.mark.asyncio
    async def test_the_sweep_runs_only_after_the_sync_has_stopped(self) -> None:
        """Run while the sync unwinds, the sweep missed records it wrote after,
        leaving them QUEUED with nothing to pick them up."""
        svc = _service({"_key": "c1", "name": "m", "status": AppStatus.SYNCING.value})
        order: list[str] = []
        running = iter([True, True, False])
        coordinator = MagicMock()
        coordinator.request_stop = AsyncMock(side_effect=lambda _c: order.append("stop") or True)
        coordinator.is_running = AsyncMock(
            side_effect=lambda _c: order.append("poll") or next(running)
        )
        svc.graph_provider.reset_indexing_status_for_connector = AsyncMock(
            side_effect=lambda *_a, **_k: order.append("sweep")
        )
        with patch("app.services.messaging.kafka.handlers.entity.get_coordinator",
                   return_value=coordinator):
            assert await svc.process_event("appDisabled", PAYLOAD) is True

        assert order == ["stop", "poll", "poll", "poll", "sweep"]

    @pytest.mark.asyncio
    async def test_a_stuck_sync_does_not_block_the_consumer_for_ever(self, monkeypatch) -> None:
        monkeypatch.setenv("CONNECTOR_SYNC_DELETE_STOP_WAIT_SEC", "0.3")
        svc = _service({"_key": "c1", "name": "m", "status": AppStatus.SYNCING.value})
        coordinator = MagicMock()
        coordinator.request_stop = AsyncMock(return_value=True)
        coordinator.is_running = AsyncMock(return_value=True)
        with patch("app.services.messaging.kafka.handlers.entity.get_coordinator",
                   return_value=coordinator):
            assert await svc.process_event("appDisabled", PAYLOAD) is True
        svc.graph_provider.reset_indexing_status_for_connector.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_queued_connector_goes_back_to_idle(self) -> None:
        """Left QUEUED, it showed as waiting to sync until it was re-enabled."""
        svc = _service({"_key": "c1", "name": "m", "status": AppStatus.QUEUED.value,
                        ConnectorStateKeys.PENDING_RESYNC: True})
        coordinator = MagicMock()
        coordinator.request_stop = AsyncMock(return_value=False)
        coordinator.is_running = AsyncMock(return_value=False)
        with patch("app.services.messaging.kafka.handlers.entity.get_coordinator",
                   return_value=coordinator):
            assert await svc.process_event("appDisabled", PAYLOAD) is True
        updates = [c.args[2] for c in svc.graph_provider.update_node.await_args_list]
        assert any(u.get("status") == AppStatus.IDLE.value
                   and u.get(ConnectorStateKeys.PENDING_RESYNC) is False for u in updates)
