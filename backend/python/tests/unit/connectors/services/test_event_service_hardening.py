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

    def stopped_at_ms(self, connector_id):
        return getattr(self, "stopped_at", None)


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


class TestARequestMadeBeforeAStop:
    """Consumed after /sync/stop cleared the flags, a request made before the stop
    was recorded and re-issued -- restarting what the user had just stopped."""

    async def _handle(self, created_at, *, stopped_at=5_000):
        svc = _service({"id": "c1", "isActive": True})
        coordinator = _Coordinator(Admission.HELD_ELSEWHERE, held_since=1_000)
        coordinator.stopped_at = stopped_at
        payload = {"orgId": "o1", "connectorId": "c1"}
        if created_at is not None:
            payload["createdAtTimestamp"] = str(created_at)
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator):
            await svc._handle_start_sync("gmail", payload)
        return svc

    @pytest.mark.asyncio
    async def test_is_dropped(self) -> None:
        svc = await self._handle(4_000)
        assert not any(u.get(ConnectorStateKeys.PENDING_RESYNC) for u in _updates(svc))

    @pytest.mark.asyncio
    async def test_one_made_after_the_stop_is_kept(self) -> None:
        svc = await self._handle(6_000)
        assert {ConnectorStateKeys.PENDING_RESYNC: True} in _updates(svc)

    @pytest.mark.asyncio
    async def test_one_without_a_timestamp_is_kept(self) -> None:
        """The inline start on re-enable carries none, and must still run."""
        svc = await self._handle(None)
        assert {ConnectorStateKeys.PENDING_RESYNC: True} in _updates(svc)


class TestStopDuringInitOnADrainedConnector:
    @pytest.mark.asyncio
    async def test_the_queue_entry_is_cleared(self) -> None:
        """/sync/stop saw the lease and repaired nothing, so the drain's stale arm
        restarted the stopped connector two minutes later."""
        svc = _service({"id": "c1", ConnectorStateKeys.IS_ACTIVE: True,
                        "status": AppStatus.QUEUED.value})
        coordinator = _Coordinator(Admission.GRANTED)

        async def ensure(*_a, **_k):
            coordinator.lease.stop_requested.set()
            return MagicMock()

        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch.object(svc, "_ensure_connector", AsyncMock(side_effect=ensure)), \
                patch("app.connectors.services.event_service.drain_queued_syncs", AsyncMock(return_value=[])):
            await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"})

        assert _updates(svc)[-1]["status"] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_a_delete_in_that_window_keeps_deleting(self) -> None:
        """The delete route writes DELETING and its appDisabled stops this lease;
        the doc read before init still said QUEUED and was written back as IDLE."""
        docs = {"c1": {"id": "c1", ConnectorStateKeys.IS_ACTIVE: True, "status": AppStatus.QUEUED.value}}
        svc = _service(None)
        svc.graph_provider.get_document = AsyncMock(side_effect=lambda *a, **k: dict(docs["c1"]))
        coordinator = _Coordinator(Admission.GRANTED)

        async def ensure(*_a, **_k):
            docs["c1"]["status"] = "DELETING"
            coordinator.lease.stop_requested.set()
            return MagicMock()

        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch.object(svc, "_ensure_connector", AsyncMock(side_effect=ensure)), \
                patch("app.connectors.services.event_service.drain_queued_syncs", AsyncMock(return_value=[])):
            assert await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"}) is True

        assert not any(u.get("status") == AppStatus.IDLE.value for u in _updates(svc))
        coordinator.end.assert_awaited_once()


class TestDeleteLeavesPendingResyncAlone:
    """A failed delete reverts and keeps the doc; main's strict Arango app schema
    then rejects every update to a doc carrying the field."""

    async def _delete(self, doc: dict) -> EventService:
        svc = _service(doc)
        svc.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True})
        svc.app_container.config_service = MagicMock(return_value=AsyncMock())
        coordinator = MagicMock(cancel_and_wait=AsyncMock())
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch("app.connectors.services.event_service.reindex_task_manager") as rtm, \
                patch("app.connectors.services.event_service.build_connector_cleanup_events", return_value=[]):
            rtm.cancel_by_prefix = AsyncMock()
            assert await svc._handle_delete("gmail", {"orgId": "o1", "connectorId": "c1"}) is True
        return svc

    @pytest.mark.asyncio
    async def test_not_written_when_absent(self) -> None:
        svc = await self._delete({"id": "c1", "status": "DELETING"})
        assert not any(ConnectorStateKeys.PENDING_RESYNC in u for u in _updates(svc))

    @pytest.mark.asyncio
    async def test_cleared_when_set(self) -> None:
        svc = await self._delete({"id": "c1", "status": "DELETING", ConnectorStateKeys.PENDING_RESYNC: True})
        assert {ConnectorStateKeys.PENDING_RESYNC: False} in _updates(svc)


class TestQueueOrderIsArrivalOrder:
    @pytest.mark.asyncio
    async def test_entering_the_queue_stamps_it(self) -> None:
        svc = _service({"id": "c1", "status": AppStatus.IDLE.value})
        await svc._mark_queued("c1")
        assert isinstance(_updates(svc)[-1].get("queuedAtTimestamp"), int)

    @pytest.mark.asyncio
    async def test_bouncing_back_keeps_the_place(self) -> None:
        """A drained connector answered AT_CAPACITY again must not go to the back."""
        svc = _service({"id": "c1", "status": AppStatus.QUEUED.value, "queuedAtTimestamp": 42})
        await svc._mark_queued("c1")
        assert "queuedAtTimestamp" not in _updates(svc)[-1]


class TestAReleasedAdmissionDrains:
    @pytest.mark.asyncio
    async def test_a_lease_given_back_without_a_sync_drains_the_queue(self) -> None:
        """No finalizer runs for it, so at a limit of 1 a connector parked behind
        it waited for an unrelated sync to end."""
        import asyncio as _asyncio

        svc = _service({"id": "c1", ConnectorStateKeys.IS_ACTIVE: False})
        coordinator = _Coordinator(Admission.GRANTED)
        drain = AsyncMock(return_value=[])
        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
                patch("app.connectors.services.event_service.drain_queued_syncs", drain):
            await svc._handle_start_sync("gmail", {"orgId": "o1", "connectorId": "c1"})
            await _asyncio.sleep(0)
            await _asyncio.sleep(0)
        drain.assert_awaited_once()
