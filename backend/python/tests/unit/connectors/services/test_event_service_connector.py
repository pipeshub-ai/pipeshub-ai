"""Unit tests for app.connectors.services.event_service.EventService.

Covers:
- __init__: attributes
- _update_app_status: status only, isLocked only, both
- _get_connector: from container attr, from connectors_map, not found
- _store_connector: in container attr, in connectors_map (new + existing)
- _ensure_connector: found in memory, not in DB, not active, init success, init failure
- process_event: invalid format, init, start, resync, reindex, delete, unknown, exception
- _handle_init: success, no orgId, factory fails, init fails, exception
- _handle_start_sync: no orgId, decline-if-running, normal sync, full sync (success, lock fail, prep fail, unlock fail)
- run_sync_task (sync_runner): start/IDLE status writes, errors, cancellation
- _handle_reindex: missing orgId/connectorId, by recordId, by recordGroupId, by status, batch paging
- _handle_delete: missing ids, success, graph fails with revert, config delete fail, kafka fail
"""

import contextlib
import logging
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.sync.task_manager import reindex_task_manager
from app.connectors.services.event_service import EventService

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.constants import ConnectorStateKeys


def _spawned(key, coro):
    """Stand-in for start_if_idle on the success path.

    Must close the coroutine (start_if_idle owns it) *and* return a truthy
    task: the caller treats a None return as "declined, another sync is
    running" and skips the post-spawn bookkeeping.
    """
    coro.close()
    return MagicMock()




# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_graph_provider():
    gp = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    gp.get_document = AsyncMock(return_value=None)
    gp.delete_sync_points_by_connector_id = AsyncMock(return_value=(5, True))
    gp.delete_connector_sync_edges = AsyncMock(return_value=(3, True))
    gp.delete_connector_instance = AsyncMock(return_value={"success": True, "virtual_record_ids": [], "deleted_records_count": 0})
    gp.get_records_by_parent_record = AsyncMock(return_value=[])
    gp.get_records_by_record_group = AsyncMock(return_value=[])
    gp.get_records_by_status = AsyncMock(return_value=[])
    return gp


@pytest.fixture
def mock_container():
    container = MagicMock()
    container.config_service.return_value = AsyncMock()
    container.messaging_producer = AsyncMock()
    container.messaging_producer.send_message = AsyncMock()
    return container


class _StubLeaseManager:
    """A coordinator that always admits, so tests can assert the behaviour
    around admission without wiring Redis.

    `spawn` is an AsyncMock, which is what tests configure to decide whether a
    sync "started": returning None is how the real coordinator reports that one
    was already running for this connector.
    """

    def __init__(self) -> None:
        self.acquired: list[str] = []
        self.released: list[str] = []
        self.spawn = AsyncMock(return_value=MagicMock(name="task"))
        self.reports_liveness = False
        # Mocks, not methods: tests set .return_value on these to say whether
        # a sync is already in flight.
        self.is_running_here = MagicMock(return_value=False)
        self.is_running = AsyncMock(return_value=False)
        #: What begin() answers. Tests set this to AT_CAPACITY to exercise
        #: the queue path, which used to mean patching a separate predicate.
        self.admission = None

    async def try_claim_org(self, org_id) -> bool:
        return True

    async def begin(self, connector_id, *, org_id=None, message_ts_ms=None):
        from app.connectors.core.sync.sync_coordinator import Admission, SyncLease

        outcome = self.admission or Admission.GRANTED
        if outcome is not Admission.GRANTED:
            return outcome, None
        self.acquired.append(connector_id)
        return outcome, SyncLease(connector_id, "stub-token", 1)

    async def end(self, lease) -> bool:
        self.released.append(lease.connector_id)
        return True

    async def cancel_and_wait(self, connector_id) -> None:
        return None

    async def request_stop(self, connector_id) -> bool:
        return False

    def running_count(self) -> int:
        return len(self.acquired) - len(self.released)


#: The coordinator installed for the current test, so a `with` block can reach
#: the same object the autouse fixture patched in rather than layering a second
#: patch on top of it.
_CURRENT: "_StubLeaseManager | None" = None


def _stub():
    return _CURRENT


@contextlib.contextmanager
def _at_capacity():
    """Make the installed coordinator answer AT_CAPACITY.

    Replaces patching a module-level `at_capacity` predicate: capacity is part
    of the admission decision now, so there is nothing separate to patch.
    """
    from app.connectors.core.sync.sync_coordinator import Admission

    assert _CURRENT is not None, "stub_lease_manager fixture is not active"
    _CURRENT.admission = Admission.AT_CAPACITY
    try:
        yield _CURRENT
    finally:
        _CURRENT.admission = None


@contextlib.contextmanager
def _current_coordinator():
    """Hand back the coordinator this test is already running against."""
    assert _CURRENT is not None, "stub_lease_manager fixture is not active"
    yield _CURRENT


@pytest.fixture(autouse=True)
def stub_lease_manager():
    global _CURRENT
    manager = _StubLeaseManager()
    _CURRENT = manager
    with patch(
        "app.connectors.services.event_service.get_coordinator",
        return_value=manager,
    ):
        yield manager


@pytest.fixture
def service(mock_logger, mock_container, mock_graph_provider):
    return EventService(mock_logger, mock_container, mock_graph_provider)


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    def test_attributes(self, service, mock_logger, mock_container, mock_graph_provider):
        assert service.logger is mock_logger
        assert service.app_container is mock_container
        assert service.graph_provider is mock_graph_provider


# ===========================================================================
# _update_app_status
# ===========================================================================


class TestUpdateAppStatus:
    @pytest.mark.asyncio
    async def test_status_only(self, service):
        await service._update_app_status("conn1", status="SYNCING")
        service.graph_provider.batch_upsert_nodes.assert_awaited_once()
        call_args = service.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert call_args["status"] == "SYNCING"
        assert "isLocked" not in call_args

    @pytest.mark.asyncio
    async def test_locked_only(self, service):
        await service._update_app_status("conn1", is_locked=True)
        call_args = service.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert call_args["isLocked"] is True
        assert "status" not in call_args

    @pytest.mark.asyncio
    async def test_both(self, service):
        await service._update_app_status("conn1", status="IDLE", is_locked=False)
        call_args = service.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert call_args["status"] == "IDLE"
        assert call_args["isLocked"] is False


# ===========================================================================
# _get_connector / _store_connector
# ===========================================================================


class TestGetConnector:
    def test_from_container_attr(self, service):
        mock_conn = MagicMock()
        service.app_container.conn1_connector = MagicMock(return_value=mock_conn)
        result = service._get_connector("conn1")
        assert result is mock_conn

    def test_from_connectors_map(self, service):
        mock_conn = MagicMock()
        service.app_container.connectors_map = {"conn1": mock_conn}
        # Make sure the container doesn't have the attr
        delattr(service.app_container, "conn1_connector") if hasattr(service.app_container, "conn1_connector") else None
        # Mock hasattr for the connector_key
        original_hasattr = hasattr

        result = service._get_connector("conn1")
        assert result is mock_conn

    def test_not_found(self, service):
        # Ensure neither method finds the connector
        spec_container = MagicMock(spec=[])
        service.app_container = spec_container
        result = service._get_connector("nonexistent")
        assert result is None


class TestStoreConnector:
    @pytest.mark.asyncio
    async def test_store_in_container_attr(self, service):
        mock_provider = MagicMock()
        # The provider is called to read the previous instance; return an
        # AsyncMock so the cleanup path runs for real instead of raising a
        # TypeError that the handler swallows.
        mock_provider.return_value = AsyncMock()
        service.app_container.conn1_connector = mock_provider
        mock_conn = MagicMock()

        with patch("app.connectors.services.event_service._running_here", return_value=False):
            await service._store_connector("conn1", mock_conn)

        mock_provider.override.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_in_connectors_map_new(self, service):
        spec_container = MagicMock(spec=[])
        service.app_container = spec_container
        mock_conn = MagicMock()
        await service._store_connector("conn1", mock_conn)
        assert service.app_container.connectors_map["conn1"] is mock_conn

    @pytest.mark.asyncio
    async def test_store_in_connectors_map_existing(self, service):
        spec_container = MagicMock(spec=[])
        spec_container.connectors_map = {}
        service.app_container = spec_container
        mock_conn = MagicMock()
        await service._store_connector("conn1", mock_conn)
        assert service.app_container.connectors_map["conn1"] is mock_conn

    @pytest.mark.asyncio
    async def test_replacing_an_instance_closes_the_old_one(self, service):
        """A superseded instance still owns an open connection pool."""
        spec_container = MagicMock(spec=[])
        previous = AsyncMock()
        spec_container.connectors_map = {"conn1": previous}
        service.app_container = spec_container

        with patch("app.connectors.services.event_service._running_here", return_value=False):
            await service._store_connector("conn1", MagicMock())

        previous.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_close_an_instance_whose_sync_is_running(self, service):
        """cleanup() nulls the client and data source, so closing mid-sync would
        kill the running sync; leaking the pool is the lesser evil."""
        spec_container = MagicMock(spec=[])
        previous = AsyncMock()
        spec_container.connectors_map = {"conn1": previous}
        service.app_container = spec_container

        with patch("app.connectors.services.event_service._running_here", return_value=True):
            await service._store_connector("conn1", MagicMock())

        previous.cleanup.assert_not_awaited()


# ===========================================================================
# _ensure_connector
# ===========================================================================


class TestEnsureConnector:
    @pytest.mark.asyncio
    async def test_already_in_memory(self, service):
        mock_conn = MagicMock()
        with patch.object(service, "_get_connector", return_value=mock_conn):
            result = await service._ensure_connector("gmail", "conn1")
            assert result is mock_conn

    @pytest.mark.asyncio
    async def test_not_in_db(self, service):
        service.graph_provider.get_document = AsyncMock(return_value=None)
        with patch.object(service, "_get_connector", return_value=None):
            result = await service._ensure_connector("gmail", "conn1")
            assert result is None

    @pytest.mark.asyncio
    async def test_not_active(self, service):
        service.graph_provider.get_document = AsyncMock(return_value={"isActive": False})
        with patch.object(service, "_get_connector", return_value=None):
            result = await service._ensure_connector("gmail", "conn1")
            assert result is None

    @pytest.mark.asyncio
    async def test_init_success(self, service):
        service.graph_provider.get_document = AsyncMock(return_value={"isActive": True})
        mock_conn = MagicMock()
        with patch.object(service, "_get_connector", return_value=None), \
             patch.object(service, "_store_connector") as mock_store, \
             patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.initialize_connector = AsyncMock(return_value=mock_conn)
            result = await service._ensure_connector("gmail", "conn1")
            assert result is mock_conn
            mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_failure(self, service):
        service.graph_provider.get_document = AsyncMock(return_value={"isActive": True})
        with patch.object(service, "_get_connector", return_value=None), \
             patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.initialize_connector = AsyncMock(return_value=None)
            result = await service._ensure_connector("gmail", "conn1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_document = AsyncMock(side_effect=Exception("db fail"))
        with patch.object(service, "_get_connector", return_value=None):
            result = await service._ensure_connector("gmail", "conn1")
            assert result is None


# ===========================================================================
# process_event
# ===========================================================================


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_invalid_format(self, service):
        result = await service.process_event("nodotshere", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_init_event(self, service):
        with patch.object(service, "_handle_init", new_callable=AsyncMock, return_value=True) as mock_init:
            result = await service.process_event("gmail.init", {"orgId": "org1"})
            assert result is True
            mock_init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_event(self, service):
        with patch.object(service, "_handle_start_sync", new_callable=AsyncMock, return_value=True) as mock_start:
            result = await service.process_event("gmail.start", {"orgId": "org1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_resync_event(self, service):
        with patch.object(service, "_handle_start_sync", new_callable=AsyncMock, return_value=True) as mock_resync:
            result = await service.process_event("gmail.resync", {"orgId": "org1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_reindex_event(self, service):
        with patch.object(service, "_handle_reindex", new_callable=AsyncMock, return_value=True):
            result = await service.process_event("gmail.reindex", {"orgId": "org1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_event(self, service):
        with patch.object(service, "_handle_delete", new_callable=AsyncMock, return_value=True):
            result = await service.process_event("gmail.delete", {"orgId": "org1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_unknown_action(self, service):
        result = await service.process_event("gmail.unknown_action", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        with patch.object(service, "_handle_init", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await service.process_event("gmail.init", {})
            assert result is False

    @pytest.mark.asyncio
    async def test_connector_name_with_spaces(self, service):
        with patch.object(service, "_handle_init", new_callable=AsyncMock, return_value=True):
            result = await service.process_event("Google Drive.init", {"orgId": "org1"})
            assert result is True


# ===========================================================================
# _handle_init
# ===========================================================================


class TestHandleInit:
    _APP_DOC = {"_key": "c1", "scope": "personal", "createdBy": "user-1"}

    @pytest.mark.asyncio
    async def test_success(self, service):
        mock_conn = AsyncMock()
        mock_conn.init = AsyncMock(return_value=True)
        service.graph_provider.get_document = AsyncMock(return_value=self._APP_DOC)
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"), \
             patch.object(service, "_store_connector"):
            mock_factory.create_connector = AsyncMock(return_value=mock_conn)
            result = await service._handle_init("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_no_org_id(self, service):
        result = await service._handle_init("gmail", {"connectorId": "c1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_factory_fails(self, service):
        service.graph_provider.get_document = AsyncMock(return_value=self._APP_DOC)
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.create_connector = AsyncMock(return_value=None)
            result = await service._handle_init("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_init_returns_false(self, service):
        mock_conn = AsyncMock()
        mock_conn.init = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(return_value=self._APP_DOC)
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.create_connector = AsyncMock(return_value=mock_conn)
            result = await service._handle_init("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False


def _close_then_raise(key, coro):
    """start_if_idle owns the coroutine, so a raising stub must still close it
    or the test emits 'coroutine was never awaited'."""
    coro.close()
    raise Exception("schedule failed")


# ===========================================================================
# _handle_start_sync
# ===========================================================================


class TestHandleStartSync:
    @pytest.mark.asyncio
    async def test_no_org_id(self, service):
        result = await service._handle_start_sync("gmail", {"connectorId": "c1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_normal_sync(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(side_effect=_spawned)
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_scheduled_sync_is_ignored_while_one_is_running(self, service):
        """A tick landing mid-sync must not cancel it.

        start_sync cancels and restarts, so a sync slower than its own interval
        could be killed and restarted for ever and never finish. The request is
        declined and acknowledged instead.
        """
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(return_value=None)  # already running

            result = await service._handle_start_sync(
                "gmail", {"orgId": "org1", "connectorId": "c1"}
            )

        # Acknowledged: the work is already in flight, so redelivering would
        # only repeat the decision.
        assert result is True
        mock_stm.start_sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_declined_sync_is_recorded_for_re_issue(self, service):
        """A declined request must not be silently dropped.

        Declining is only safe if the intent survives: the running sync hands the
        request back from its finalizer. Without this the connector can be left
        never syncing at all — the tick that would have started it was thrown
        away because one was in flight.
        """
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn),              patch.object(service, "_get_connector", return_value=mock_conn),              patch.object(service, "_update_app_status", new_callable=AsyncMock),              patch.object(service, "_persist_pending_resync", new_callable=AsyncMock) as mock_persist,              _current_coordinator() as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(return_value=None)  # already running

            result = await service._handle_start_sync(
                "gmail", {"orgId": "org1", "connectorId": "c1"}
            )

        assert result is True
        mock_persist.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_declined_full_sync_keeps_the_full_sync_intent(self, service):
        """A declined *full* sync must come back as a full sync, not a normal one."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn),              patch.object(service, "_get_connector", return_value=mock_conn),              patch.object(service, "_update_app_status", new_callable=AsyncMock),              patch.object(service, "_persist_pending_resync", new_callable=AsyncMock) as mock_persist,              _current_coordinator() as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(return_value=None)

            result = await service._handle_start_sync(
                "gmail", {"orgId": "org1", "connectorId": "c1", "fullSync": True}
            )

        assert result is True
        mock_persist.assert_awaited_once_with("c1", full_sync=True)

    @pytest.mark.asyncio
    async def test_full_sync_success(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(side_effect=_spawned)
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_full_sync_lock_fails(self, service):
        mock_conn = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=Exception("lock fail")):
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is False

    @pytest.mark.asyncio
    async def test_connector_not_found(self, service):
        # Apps doc is now fetched before init so disabled connectors can be skipped
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=None), \
             patch.object(service, "_get_connector", return_value=None):
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False
            service.graph_provider.get_document.assert_awaited_once_with(
                document_key="c1",
                collection=CollectionNames.APPS.value,
            )

    @pytest.mark.asyncio
    async def test_start_sync_skips_inactive_connector(self, service):
        service.graph_provider.get_document = AsyncMock(
            return_value={"_key": "c1", ConnectorStateKeys.IS_ACTIVE: False, ConnectorStateKeys.IS_AUTHENTICATED: False}
        )
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock) as mock_ensure:
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False
            mock_ensure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_sync_skips_unauthenticated_connector(self, service):
        """isAuthenticated=False alone (e.g. dead refresh token) must also skip."""
        service.graph_provider.get_document = AsyncMock(
            return_value={"_key": "c1", ConnectorStateKeys.IS_ACTIVE: True, ConnectorStateKeys.IS_AUTHENTICATED: False}
        )
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock) as mock_ensure:
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False
            mock_ensure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_sync_proceeds_for_active_connector(self, service):
        service.graph_provider.get_document = AsyncMock(
            return_value={"_key": "c1", ConnectorStateKeys.IS_ACTIVE: True, ConnectorStateKeys.IS_AUTHENTICATED: True}
        )
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=None) as mock_ensure, \
             patch.object(service, "_get_connector", return_value=None):
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            mock_ensure.assert_awaited_once()
            assert result is False

    @pytest.mark.asyncio
    async def test_pending_full_sync_triggers_full_sync(self, service):
        """Verify that pendingFullSync from connector doc triggers full sync even if payload is false."""

        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        
        # Mock connector document with pendingFullSync=True
        connector_doc = {
            "_key": "c1",
            ConnectorStateKeys.PENDING_FULL_SYNC: True
        }
        service.graph_provider.get_document = AsyncMock(return_value=connector_doc)
        service.graph_provider.update_node = AsyncMock()
        
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(side_effect=_spawned)
            
            # Call with fullSync=False in payload, but pendingFullSync=True in doc
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": False
            })
            
            assert result is True
            
            # Verify get_document was called to fetch connector doc
            service.graph_provider.get_document.assert_awaited_once_with(
                document_key="c1",
                collection=CollectionNames.APPS.value,
            )
            
            # Verify full sync path was taken (delete sync points called)
            service.graph_provider.delete_sync_points_by_connector_id.assert_awaited_once_with(
                connector_id="c1"
            )
            service.graph_provider.delete_connector_sync_edges.assert_awaited_once_with(
                connector_id="c1"
            )
            
            # Verify pendingFullSync was cleared after successful schedule
            service.graph_provider.update_node.assert_awaited_once_with(
                "c1",
                CollectionNames.APPS.value,
                {ConnectorStateKeys.PENDING_FULL_SYNC: False},
            )

    @pytest.mark.asyncio
    async def test_manual_full_sync_without_pending_skips_flag_clear(self, service):
        """Manual fullSync with no pendingFullSync in DB should not write pendingFullSync=False."""

        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        connector_doc = {
            "_key": "c1",
            ConnectorStateKeys.PENDING_FULL_SYNC: False,
        }
        service.graph_provider.get_document = AsyncMock(return_value=connector_doc)
        service.graph_provider.update_node = AsyncMock()

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(side_effect=_spawned)

            result = await service._handle_start_sync("gmail", {
                "orgId": "org1",
                "connectorId": "c1",
                "fullSync": True,
            })

            assert result is True
            service.graph_provider.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_full_sync_not_cleared_on_prep_failure(self, service):
        """Verify that pendingFullSync is NOT cleared if full sync prep fails catastrophically.

        Sync point deletion errors are caught and logged (prep continues); use start_sync failure
        to hit the outer prep except block (same as losing the race before clearing pending).
        """

        mock_conn = AsyncMock()
        
        # Mock connector document with pendingFullSync=True
        connector_doc = {
            "_key": "c1",
            ConnectorStateKeys.PENDING_FULL_SYNC: True
        }
        service.graph_provider.get_document = AsyncMock(return_value=connector_doc)
        service.graph_provider.update_node = AsyncMock()
        
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.spawn = AsyncMock(side_effect=_close_then_raise)
            
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": False
            })
            
            assert result is False
            
            # Verify pendingFullSync was NOT cleared since prep failed
            # update_node should not have been called with pendingFullSync=False
            for call in service.graph_provider.update_node.call_args_list:
                kwargs = call[1]
                updates = kwargs.get("node_updates") if kwargs else None
                if updates is None and call[0]:
                    # Positional: update_node(key, collection, node_updates)
                    if len(call[0]) >= 3:
                        updates = call[0][2]
                if isinstance(updates, dict) and updates.get(ConnectorStateKeys.PENDING_FULL_SYNC) is not None:
                    pytest.fail("pendingFullSync should not be cleared on prep failure")

    @pytest.mark.asyncio
    async def test_no_pending_full_sync_normal_sync(self, service):
        """Verify that normal sync works when no pendingFullSync flag is set."""

        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        
        # Mock connector document without pendingFullSync
        connector_doc = {
            "_key": "c1"
        }
        service.graph_provider.get_document = AsyncMock(return_value=connector_doc)
        service.graph_provider.update_node = AsyncMock()
        
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as mock_stm:
            mock_stm.is_running_here.return_value = False
            mock_stm.start_sync = AsyncMock()
            mock_stm.spawn = AsyncMock(side_effect=_spawned)
            
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": False
            })
            
            assert result is True
            
            # Verify normal sync path was taken (delete NOT called)
            service.graph_provider.delete_sync_points_by_connector_id.assert_not_awaited()
            service.graph_provider.delete_connector_sync_edges.assert_not_awaited()
            
            # Verify pendingFullSync was NOT cleared (not in full sync path)
            service.graph_provider.update_node.assert_not_awaited()


# ===========================================================================
# run_sync_task
# ===========================================================================


class TestRunSyncTask:
    """run_sync_task replaced EventService._run_sync_and_clear_status.

    The status writes moved inside the task so a cancelled task can no longer
    overwrite its successor's status, so these assert on the graph provider
    rather than on a service method.
    """

    @pytest.mark.asyncio
    async def test_writes_start_then_idle(self):
        from app.config.constants.arangodb import AppStatus
        from app.connectors.core.sync.sync_runner import run_sync_task

        gp, logger = AsyncMock(), MagicMock()
        conn = AsyncMock()
        conn.run_sync = AsyncMock()

        await run_sync_task(conn, "c1", gp, logger)

        conn.run_sync.assert_awaited_once()
        written = [c.args[0][0]["status"] for c in gp.batch_upsert_nodes.await_args_list]
        assert written == [AppStatus.SYNCING.value, AppStatus.IDLE.value]

    @pytest.mark.asyncio
    async def test_start_status_is_configurable(self):
        from app.config.constants.arangodb import AppStatus
        from app.connectors.core.sync.sync_runner import run_sync_task

        gp, logger = AsyncMock(), MagicMock()
        conn = AsyncMock()
        conn.run_sync = AsyncMock()

        await run_sync_task(
            conn, "c1", gp, logger, start_status=AppStatus.FULL_SYNCING.value
        )

        first = gp.batch_upsert_nodes.await_args_list[0].args[0][0]
        assert first["status"] == AppStatus.FULL_SYNCING.value

    @pytest.mark.asyncio
    async def test_idle_is_written_even_when_sync_raises(self):
        from app.config.constants.arangodb import AppStatus
        from app.connectors.core.sync.sync_runner import run_sync_task

        gp, logger = AsyncMock(), MagicMock()
        conn = AsyncMock()
        conn.run_sync = AsyncMock(side_effect=RuntimeError("sync fail"))

        with pytest.raises(RuntimeError, match="sync fail"):
            await run_sync_task(conn, "c1", gp, logger)

        written = [c.args[0][0]["status"] for c in gp.batch_upsert_nodes.await_args_list]
        assert written[-1] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_status_write_failure_is_not_fatal(self):
        from app.connectors.core.sync.sync_runner import run_sync_task

        gp, logger = AsyncMock(), MagicMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("db down"))
        conn = AsyncMock()
        conn.run_sync = AsyncMock()

        # A status write is bookkeeping; losing it must not fail the sync.
        await run_sync_task(conn, "c1", gp, logger)
        conn.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancellation_still_writes_idle(self):
        """The IDLE write runs as a shielded detached task precisely so a
        cancel landing during unwind cannot leave the connector stuck SYNCING."""

        from app.config.constants.arangodb import AppStatus
        from app.connectors.core.sync.sync_runner import run_sync_task

        gp, logger = AsyncMock(), MagicMock()
        started = asyncio.Event()

        async def _blocked():
            started.set()
            await asyncio.sleep(30)

        conn = AsyncMock()
        conn.run_sync = _blocked

        task = asyncio.create_task(run_sync_task(conn, "c1", gp, logger))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.05)  # let the detached cleanup land
        written = [c.args[0][0]["status"] for c in gp.batch_upsert_nodes.await_args_list]
        assert AppStatus.IDLE.value in written


# ===========================================================================
# _handle_reindex
# ===========================================================================


class TestHandleReindex:
    @pytest.mark.asyncio
    async def test_missing_org_id(self, service):
        result = await service._handle_reindex("gmail", {"connectorId": "c1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_connector_id(self, service):
        result = await service._handle_reindex("gmail", {"orgId": "org1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_connector_not_found(self, service):
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=None):
            result = await service._handle_reindex("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_by_record_id(self, service):
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_parent_record = AsyncMock(return_value=[])

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1", "recordId": "r1", "depth": 1
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_by_record_group_id(self, service):
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_record_group = AsyncMock(return_value=[])

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1", "recordGroupId": "rg1"
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_by_status_filters(self, service):
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_status = AsyncMock(return_value=[])

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1", "statusFilters": ["FAILED"]
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_schedules_background_task_and_returns_immediately(self, service):
        """_handle_reindex must hand off and return, never page inline.

        The Kafka poll loop awaits this handler, so doing the work here would stall
        polling and get the consumer evicted.
        """
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_status = AsyncMock(side_effect=[[], []])

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors, \
             patch.object(reindex_task_manager, "start_if_idle", new_callable=AsyncMock) as mock_start:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })

            assert result is True
            mock_start.assert_awaited_once()
            # Nothing may have been fetched on the handler's own thread of control.
            service.graph_provider.get_records_by_status.assert_not_awaited()
            # Close the coroutine start_if_idle was handed but never ran.
            mock_start.await_args.args[1].close()

    @pytest.mark.asyncio
    async def test_duplicate_event_does_not_restart_running_reindex(self, service):
        """A redelivered event must collapse onto the in-flight task, not restart it."""
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors, \
             patch.object(reindex_task_manager, "start_if_idle", new_callable=AsyncMock, return_value=None) as mock_start:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })

            # Still True: the event is handled, so Kafka must commit it.
            assert result is True
            mock_start.assert_awaited_once()
            mock_start.await_args.args[1].close()

    @pytest.mark.asyncio
    async def test_run_reindex_pages_with_keyset_cursor(self, service):
        """_run_reindex walks batches by cursor and stops on a short batch."""
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock()

        batch1 = [MagicMock(id=f"rec-{i:03d}", is_placeholder=False) for i in range(50)]  # < batch_size
        service.graph_provider.get_records_by_status = AsyncMock(side_effect=[batch1, []])
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        await service._run_reindex(
            connector=mock_conn,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id=None,
            depth=0,
            user_key=None,
            status_filters=["FAILED"],
        )

        mock_conn.reindex_records.assert_awaited_once()
        # A short batch ends the walk without a second fetch.
        assert service.graph_provider.get_records_by_status.await_count == 1

    @pytest.mark.asyncio
    async def test_unknown_connector_name(self, service):
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "UNKNOWN_CONNECTOR"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors:
            # Make getattr return None
            mock_connectors.UNKNOWN_CONNECTOR = None
            type(mock_connectors).UNKNOWN_CONNECTOR = None
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is False


# ===========================================================================
# _handle_delete
# ===========================================================================


class TestHandleDelete:
    @pytest.mark.asyncio
    async def test_missing_ids(self, service):
        result = await service._handle_delete("gmail", {"orgId": "org1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_success_no_records(self, service):
        with _current_coordinator() as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            config_svc = AsyncMock()
            config_svc.delete_config = AsyncMock()
            service.app_container.config_service.return_value = config_svc
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_success_with_records(self, service):
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True, "virtual_record_ids": ["vr1", "vr2"], "deleted_records_count": 2
        })
        with _current_coordinator() as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            config_svc = AsyncMock()
            config_svc.delete_config = AsyncMock()
            service.app_container.config_service.return_value = config_svc
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True
            service.app_container.messaging_producer.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_delete_fails_reverts(self, service):
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": False, "error": "DB error"
        })
        with _current_coordinator() as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1", "previousIsActive": True
            })
            assert result is False
            # Verify revert was attempted
            assert service.graph_provider.batch_upsert_nodes.await_count >= 1

    @pytest.mark.asyncio
    async def test_kafka_publish_fails(self, service):
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True, "virtual_record_ids": ["vr1"], "deleted_records_count": 1
        })
        service.app_container.messaging_producer.send_message = AsyncMock(side_effect=Exception("kafka down"))
        with _current_coordinator() as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            config_svc = AsyncMock()
            config_svc.delete_config = AsyncMock()
            service.app_container.config_service.return_value = config_svc
            # Should still succeed (kafka failure is non-fatal for delete)
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_config_delete_fails(self, service):
        with _current_coordinator() as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            config_svc = AsyncMock()
            config_svc.delete_config = AsyncMock(side_effect=Exception("etcd error"))
            service.app_container.config_service.return_value = config_svc
            # Should still succeed (config delete failure is non-fatal)
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True


# ===========================================================================
# Lease acquisition in the start path
# ===========================================================================


class _RecordingLeaseManager:
    """Records admission order. `lease=None` means "someone else holds it"."""

    def __init__(self, calls: list, lease=None, raises: Exception | None = None) -> None:
        from unittest.mock import AsyncMock, MagicMock

        self._calls = calls
        self._lease = lease
        self._raises = raises
        self.released: list = []
        # The real spawn() assigns lease.task, and the finalizer keys the
        # release off that -- not off the return value.
        async def _spawn(lease, coro):
            coro.close()
            lease.task = MagicMock(name="task")
            return lease.task

        self.spawn = AsyncMock(side_effect=_spawn)
        self.is_running_here = MagicMock(return_value=False)
        self.is_running = AsyncMock(return_value=False)
        self.cancel_and_wait = AsyncMock()
        self.reports_liveness = False

    async def try_claim_org(self, org_id) -> bool:
        return True

    async def begin(self, connector_id, *, org_id=None, message_ts_ms=None):
        from app.connectors.core.sync.sync_coordinator import Admission

        self._calls.append("begin")
        if self._raises:
            raise self._raises
        if self._lease is None:
            return Admission.HELD_ELSEWHERE, None
        return Admission.GRANTED, self._lease

    async def end(self, lease) -> bool:
        self._calls.append("end")
        self.released.append(lease)
        return True

    def running_count(self) -> int:
        return 0


class TestStartSyncLease:
    @pytest.mark.asyncio
    async def test_acquire_precedes_connector_init_and_the_destructive_prep(
        self, service
    ) -> None:
        """The ordering regression most likely to be reintroduced.

        _ensure_connector costs seconds of OAuth and HTTP, and the full-sync
        prep *deletes sync points*. Both were previously unguarded, so two
        workers could wipe the same connector's sync points before either
        checked whether the other was running.
        """
        from app.connectors.core.sync.sync_coordinator import SyncLease

        calls: list = []
        manager = _RecordingLeaseManager(calls, lease=SyncLease("c1", "tok", 1))

        async def _ensure(*_a, **_k):
            calls.append("_ensure_connector")
            return AsyncMock()

        async def _delete_points(**_k):
            calls.append("delete_sync_points")
            return (5, True)

        service.graph_provider.delete_sync_points_by_connector_id = _delete_points

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(service, "_ensure_connector", side_effect=_ensure), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             _current_coordinator() as stm:
            stm.spawn = AsyncMock(side_effect=_spawned)
            # Nothing running locally, so the prep is allowed to proceed and
            # its ordering against acquire is what this asserts.
            stm.is_running = MagicMock(return_value=False)
            await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True}
            )

        assert calls.index("begin") < calls.index("_ensure_connector")
        assert calls.index("begin") < calls.index("delete_sync_points")

    @pytest.mark.asyncio
    async def test_declined_lease_acks_without_touching_the_graph(self, service) -> None:
        """Returning False would redeliver the event and stall the partition
        behind a connector that is syncing perfectly well elsewhere."""
        calls: list = []
        manager = _RecordingLeaseManager(calls, lease=None)

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock) as ensure:
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert result is True
        ensure.assert_not_awaited()
        service.graph_provider.batch_upsert_nodes.assert_not_awaited()
        # The request is remembered even for a plain resync, so the running
        # sync can hand it back rather than it being acked and forgotten.
        service.graph_provider.update_node.assert_awaited_once()
        assert service.graph_provider.update_node.call_args[0][2] == {
            ConnectorStateKeys.PENDING_RESYNC: True
        }

    @pytest.mark.asyncio
    async def test_declined_full_sync_preserves_the_intent(self, service) -> None:
        """Both intents are carried only by this event; losing either loses the
        request. pendingResync is what gets the sync re-issued at all;
        pendingFullSync is what keeps it a *full* sync when it is."""
        manager = _RecordingLeaseManager([], lease=None)
        service.graph_provider.update_node = AsyncMock()

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ):
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True}
            )

        assert result is True
        service.graph_provider.update_node.assert_awaited_once()
        assert service.graph_provider.update_node.call_args[0][2] == {
            ConnectorStateKeys.PENDING_RESYNC: True,
            ConnectorStateKeys.PENDING_FULL_SYNC: True,
        }

    @pytest.mark.asyncio
    async def test_redis_failure_fails_closed(self, service) -> None:
        """Today's guard logs a DB error and starts anyway. This must not.

        False redelivers the event, so the sync is retried rather than run
        concurrently with one already in flight elsewhere.
        """
        manager = _RecordingLeaseManager([], raises=RuntimeError("redis down"))

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock) as ensure:
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert result is False
        ensure.assert_not_awaited()
        service.graph_provider.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_manager_refuses_rather_than_running_unguarded(self, service) -> None:
        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=None,
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock) as ensure:
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert result is False
        ensure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lease_released_when_no_task_is_spawned(self, service) -> None:
        """Only the spawned task releases; every other exit path must do it here
        or the connector stays un-startable for a full TTL."""
        from app.connectors.core.sync.sync_coordinator import SyncLease

        lease = SyncLease("c1", "tok", 1)
        manager = _RecordingLeaseManager([], lease=lease)

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(
            service, "_ensure_connector", new_callable=AsyncMock, return_value=None
        ):
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert result is False
        assert manager.released == [lease]

    @pytest.mark.asyncio
    async def test_lease_not_released_when_handed_to_a_task(self, service) -> None:
        """run_sync_task's shielded finalizer owns the release from here on."""
        from app.connectors.core.sync.sync_coordinator import SyncLease

        manager = _RecordingLeaseManager([], lease=SyncLease("c1", "tok", 1))

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(
            service, "_ensure_connector", new_callable=AsyncMock, return_value=AsyncMock()
        ), _current_coordinator() as stm:
            stm.spawn = AsyncMock(side_effect=_spawned)
            result = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert result is True
        assert manager.released == []


# ===========================================================================
# connector cache bound
# ===========================================================================


class TestFullSyncDoesNotDestroyARunningSyncsState:
    """The full-sync prep deletes sync points and sync edges.

    It used to run before admission, so a full sync requested against a
    connector that was already syncing wiped its incremental checkpoints and
    then declined to run anything, leaving nothing to rebuild them.

    `begin()` now runs first and is the whole protection: the prep is
    unreachable without the claim, and holding the claim means nothing else is
    syncing this connector. A second guard here would only ever see *its own*
    claim and decline every full sync -- which is exactly what it did.
    """

    @pytest.mark.asyncio
    async def test_prep_never_runs_without_admission(self, service) -> None:
        """Admission refused -> the destructive prep must not have run."""
        calls: list = []
        # lease=None means someone else holds it: HELD_ELSEWHERE.
        manager = _RecordingLeaseManager(calls, lease=None)

        async def _delete_points(**_k):
            calls.append("delete_sync_points")
            return (5, True)

        async def _delete_edges(**_k):
            calls.append("delete_sync_edges")
            return (5, True)

        service.graph_provider.delete_sync_points_by_connector_id = _delete_points
        service.graph_provider.delete_connector_sync_edges = _delete_edges
        service._persist_pending_resync = AsyncMock()

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock),                 patch.object(service, "_update_app_status", new_callable=AsyncMock):
            ok = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True}
            )

        assert "delete_sync_points" not in calls
        assert "delete_sync_edges" not in calls
        # Acked so the event is not redelivered, and recorded so it is re-issued.
        assert ok is True
        service._persist_pending_resync.assert_awaited()

    @pytest.mark.asyncio
    async def test_prep_runs_once_admitted(self, service) -> None:
        """Granted -> nothing else holds it, so the prep is safe to run."""
        from app.connectors.core.sync.sync_coordinator import SyncLease

        calls: list = []
        manager = _RecordingLeaseManager(calls, lease=SyncLease("c1", "tok", 1))

        async def _delete_points(**_k):
            calls.append("delete_sync_points")
            return (5, True)

        async def _delete_edges(**_k):
            calls.append("delete_sync_edges")
            return (5, True)

        service.graph_provider.delete_sync_points_by_connector_id = _delete_points
        service.graph_provider.delete_connector_sync_edges = _delete_edges

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=manager,
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock),                 patch.object(service, "_update_app_status", new_callable=AsyncMock):
            ok = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True}
            )

        assert ok is True
        assert "delete_sync_points" in calls



class TestConnectorCacheIsBounded:
    """An unbounded cache OOM-killed three of four workers at 120 connectors.

    Each entry is an initialised connector — client sessions, credentials,
    config — and nothing evicted them, so a worker grew to 3.2 GB RSS and
    throughput fell from 55 rec/s to 9.
    """

    class _Container:
        """A plain object, not a MagicMock.

        MagicMock answers hasattr() for anything, so `<id>_connector` always
        looks present and the store takes the DI-override branch instead of the
        cache it is meant to exercise.
        """

    def _connector(self) -> MagicMock:
        c = MagicMock()
        c.cleanup = AsyncMock()
        return c

    def _with_cache(self, service):
        container = self._Container()
        container.connectors_map = {}
        service.app_container = container
        return container

    @pytest.mark.asyncio
    async def test_cache_stays_within_the_limit(self, service, mock_container, monkeypatch) -> None:
        monkeypatch.setenv("CONNECTOR_CACHE_MAX", "3")
        mock_container = self._with_cache(service)

        with _current_coordinator() as stm:
            stm.is_running_here.return_value = False
            for i in range(10):
                await service._store_connector(f"c{i}", self._connector())

        assert len(mock_container.connectors_map) == 3

    @pytest.mark.asyncio
    async def test_evicts_least_recently_used_first(self, service, mock_container, monkeypatch) -> None:
        monkeypatch.setenv("CONNECTOR_CACHE_MAX", "2")
        mock_container = self._with_cache(service)

        with _current_coordinator() as stm:
            stm.is_running_here.return_value = False
            await service._store_connector("a", self._connector())
            await service._store_connector("b", self._connector())
            service._get_connector("a")          # touch: a is now newest
            await service._store_connector("c", self._connector())

        assert "b" not in mock_container.connectors_map
        assert set(mock_container.connectors_map) == {"a", "c"}

    @pytest.mark.asyncio
    async def test_a_connector_mid_sync_is_never_evicted(self, service, mock_container, monkeypatch) -> None:
        """Evicting it would pull the client out from under a running sync."""
        monkeypatch.setenv("CONNECTOR_CACHE_MAX", "1")
        mock_container = self._with_cache(service)

        with _current_coordinator() as stm:
            stm.is_running_here.side_effect = lambda cid: cid == "busy"
            await service._store_connector("busy", self._connector())
            await service._store_connector("idle", self._connector())

        assert "busy" in mock_container.connectors_map

    @pytest.mark.asyncio
    async def test_eviction_closes_the_connector(self, service, mock_container, monkeypatch) -> None:
        """Dropping the reference alone would leak sockets instead of memory."""
        monkeypatch.setenv("CONNECTOR_CACHE_MAX", "1")
        self._with_cache(service)
        doomed = self._connector()

        with _current_coordinator() as stm:
            stm.is_running_here.return_value = False
            await service._store_connector("old", doomed)
            await service._store_connector("new", self._connector())

        await asyncio.sleep(0)  # let the detached cleanup task run
        doomed.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_zero_disables_the_bound(self, service, mock_container, monkeypatch) -> None:
        monkeypatch.setenv("CONNECTOR_CACHE_MAX", "0")
        mock_container = self._with_cache(service)

        with _current_coordinator() as stm:
            stm.is_running_here.return_value = False
            for i in range(20):
                await service._store_connector(f"c{i}", self._connector())

        assert len(mock_container.connectors_map) == 20


class TestSyncConcurrencyLimit:
    """Requests past the limit are queued, not dropped and not silently idle."""

    @pytest.mark.asyncio
    async def test_at_capacity_queues_without_taking_a_lease(
        self, service, stub_lease_manager
    ) -> None:
        from app.config.constants.arangodb import AppStatus

        service.graph_provider.update_node = AsyncMock()

        with _at_capacity(), patch.object(
            _stub(), "spawn", new_callable=AsyncMock
        ) as spawn, patch.object(
            service, "_ensure_connector", new_callable=AsyncMock
        ) as ensure:
            ok = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert ok is True
        spawn.assert_not_awaited()
        # The queue decision must come before the expensive connector build.
        ensure.assert_not_awaited()
        args = service.graph_provider.update_node.await_args
        assert args.args[2]["status"] == AppStatus.QUEUED.value
        assert args.args[2][ConnectorStateKeys.PENDING_RESYNC] is True

    @pytest.mark.asyncio
    async def test_no_lease_is_taken_when_there_is_no_room(
        self, service, stub_lease_manager
    ) -> None:
        """Capacity is part of admission, so being full means never holding one.

        This used to acquire, discover the limit, then release — and that gap is
        what let a peer acquire in between and have its SYNCING overwritten by
        our late QUEUED. There is no gap to get wrong now, so the ordering hazard
        is unrepresentable rather than merely guarded against.
        """
        service.graph_provider.update_node = AsyncMock()

        with _at_capacity(), patch.object(
            _stub(), "spawn", new_callable=AsyncMock
        ), patch.object(service, "_ensure_connector", new_callable=AsyncMock):
            await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert stub_lease_manager.acquired == []
        assert stub_lease_manager.released == []

    @pytest.mark.asyncio
    async def test_a_connector_running_elsewhere_is_declined_not_queued(
        self, service
    ) -> None:
        """HELD_ELSEWHERE and AT_CAPACITY are different answers.

        A connector another worker is syncing must record intent for that worker
        to hand back, not be marked QUEUED — QUEUED would overwrite the SYNCING
        it is actually in, and the reaper only scans SYNCING/FULL_SYNCING so
        nothing would ever correct it. Collapsing the two into one boolean is
        exactly what made that possible.
        """
        from app.connectors.core.sync.sync_coordinator import Admission

        service.graph_provider.update_node = AsyncMock()

        held_elsewhere = MagicMock()
        held_elsewhere.try_claim_org = AsyncMock(return_value=True)
        held_elsewhere.begin = AsyncMock(
            return_value=(Admission.HELD_ELSEWHERE, None)
        )

        with patch(
            "app.connectors.services.event_service.get_coordinator",
            return_value=held_elsewhere,
        ):
            ok = await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        assert ok is True
        wrote_queued = [
            c for c in service.graph_provider.update_node.await_args_list
            if len(c.args) > 2 and c.args[2].get("status") == "QUEUED"
        ]
        assert wrote_queued == [], "a connector syncing on another worker was queued"

    @pytest.mark.asyncio
    async def test_queue_entry_is_stamped_so_the_drain_can_recover_it(
        self, service
    ) -> None:
        """The drain treats an unflagged QUEUED connector past a grace window as
        owed again. Without this stamp there is nothing to measure that against."""
        service.graph_provider.update_node = AsyncMock()

        with _at_capacity(), patch.object(_stub(), "spawn", new_callable=AsyncMock),              patch.object(service, "_ensure_connector", new_callable=AsyncMock):
            await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1"}
            )

        written = service.graph_provider.update_node.await_args.args[2]
        assert isinstance(written.get("updatedAtTimestamp"), int)
        assert written["updatedAtTimestamp"] > 0

    @pytest.mark.asyncio
    async def test_a_queued_full_sync_stays_a_full_sync(self, service) -> None:
        service.graph_provider.update_node = AsyncMock()

        with _at_capacity(), patch.object(_stub(), "spawn", new_callable=AsyncMock),              patch.object(service, "_ensure_connector", new_callable=AsyncMock):
            await service._handle_start_sync(
                "gmail", {"orgId": "o1", "connectorId": "c1", "fullSync": True}
            )

        written = service.graph_provider.update_node.await_args.args[2]
        assert written[ConnectorStateKeys.PENDING_FULL_SYNC] is True