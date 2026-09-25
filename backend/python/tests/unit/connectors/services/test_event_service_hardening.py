"""Start-path behaviour found missing by live testing of the sync coordinator."""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_coordinator import Admission, SyncLease
from app.connectors.services.event_service import EventService


class _Coordinator:
    def __init__(self, admission: Admission, held_since: int | None = None) -> None:
        self.admission = admission
        self.held_since = held_since
        self.lease: SyncLease | None = None
        self.spawn = AsyncMock(return_value=MagicMock(name="task"))
        self.end = AsyncMock()
        self.is_running_here = MagicMock(return_value=False)
        self.is_running = AsyncMock(return_value=False)
        self.reports_liveness = False

    async def begin(self, connector_id, *, org_id=None, message_ts_ms=None):
        if self.admission is not Admission.GRANTED:
            return self.admission, None
        self.lease = SyncLease(connector_id, "tok", 1)
        return Admission.GRANTED, self.lease

    def held_since_ms(self, connector_id):
        return self.held_since


def _service(doc: dict | None) -> EventService:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=doc)
    graph.update_node = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock()
    graph.delete_sync_points_by_connector_id = AsyncMock(return_value=(1, True))
    graph.delete_connector_sync_edges = AsyncMock(return_value=(1, True))
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    return EventService(MagicMock(spec=logging.Logger), container, graph)


def _updates(svc: EventService) -> list[dict]:
    return [c.args[2] for c in svc.graph_provider.update_node.await_args_list]


class TestADeclinedRequestTheRunningSyncServes:
    """HELD_ELSEWHERE used to flag every request, so a duplicate of an event
    already consumed -- two drains publishing one queued connector, a slow
    re-publish -- became a second back-to-back sync."""

    async def _handle(self, created_at: int, *, full: bool = False) -> EventService:
        svc = _service({"id": "c1", "isActive": True})
        coordinator = _Coordinator(Admission.HELD_ELSEWHERE, held_since=2_000)
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator):
            ok = await svc._handle_start_sync(
                "gmail",
                {"orgId": "o1", "connectorId": "c1", "fullSync": full,
                 "createdAtTimestamp": str(created_at)},
            )
        assert ok is True
        return svc

    @pytest.mark.asyncio
    async def test_an_older_request_is_not_recorded(self) -> None:
        svc = await self._handle(1_000)
        assert not any(u.get(ConnectorStateKeys.PENDING_RESYNC) for u in _updates(svc))

    @pytest.mark.asyncio
    async def test_a_newer_request_is_recorded(self) -> None:
        svc = await self._handle(3_000)
        assert {ConnectorStateKeys.PENDING_RESYNC: True} in _updates(svc)

    @pytest.mark.asyncio
    async def test_an_older_full_sync_request_is_still_recorded(self) -> None:
        """The running sync may be incremental, so it does not serve a full one."""
        svc = await self._handle(1_000, full=True)
        assert _updates(svc)[-1][ConnectorStateKeys.PENDING_FULL_SYNC] is True


class TestInactiveConnectorsAreNeverParked:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "doc",
        [
            {"id": "c1", ConnectorStateKeys.IS_ACTIVE: False},
            {"id": "c1", ConnectorStateKeys.IS_AUTHENTICATED: False},
            {"id": "c1", "status": "DELETING"},
        ],
    )
    async def test_at_capacity_does_not_queue_it(self, doc) -> None:
        """Nothing would ever run it, and every drain would publish it again."""
        svc = _service(doc)
        coordinator = _Coordinator(Admission.AT_CAPACITY)
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator):
            assert await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"}) is True
        assert not any(u.get("status") == AppStatus.QUEUED.value for u in _updates(svc))

    @pytest.mark.asyncio
    async def test_admitted_but_disabled_clears_its_queue_entry(self) -> None:
        svc = _service({"id": "c1", ConnectorStateKeys.IS_ACTIVE: False,
                        "status": AppStatus.QUEUED.value, ConnectorStateKeys.PENDING_RESYNC: True})
        coordinator = _Coordinator(Admission.GRANTED)
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch.object(svc, "_ensure_connector", AsyncMock()) as ensure:
            assert await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"}) is True
        ensure.assert_not_awaited()
        last = _updates(svc)[-1]
        assert last["status"] == AppStatus.IDLE.value
        assert last[ConnectorStateKeys.PENDING_RESYNC] is False
        coordinator.end.assert_awaited_once()


class TestAStopDuringConnectorInit:
    @pytest.mark.asyncio
    async def test_skips_the_destructive_full_sync_prep(self) -> None:
        """The caller was already told the sync stopped; deleting its sync points
        anyway made the next incremental sync re-read everything."""
        svc = _service({"id": "c1", ConnectorStateKeys.IS_ACTIVE: True,
                        ConnectorStateKeys.PENDING_FULL_SYNC: True})
        coordinator = _Coordinator(Admission.GRANTED)

        async def ensure(*_a, **_k):
            coordinator.lease.stop_requested.set()
            return MagicMock()

        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch.object(svc, "_ensure_connector", AsyncMock(side_effect=ensure)):
            assert await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"}) is True

        svc.graph_provider.delete_sync_points_by_connector_id.assert_not_awaited()
        svc.graph_provider.delete_connector_sync_edges.assert_not_awaited()
        coordinator.spawn.assert_not_awaited()
        coordinator.end.assert_awaited_once()
