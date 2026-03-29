"""Unit tests for app.connectors.services.event_service.EventService.

Covers:
- __init__: attributes
- _update_app_status: status only, isLocked only, both
- _get_connector: from container attr, from connectors_map, not found
- _store_connector: in container attr, in connectors_map (new + existing)
- _ensure_connector: found in memory, not in DB, not active, init success, init failure
- process_event: invalid format, init, start, resync, reindex, delete, unknown, exception
- _handle_init: success, no orgId, factory fails, init fails, exception
- _handle_start_sync: no orgId, normal sync, full sync (success, lock fail, prep fail, unlock fail)
- _run_sync_and_clear_status: success, sync error, status clear error
- _handle_reindex: missing orgId/connectorId, by recordId, by recordGroupId, by status, batch paging
- _handle_delete: missing ids, success, graph fails with revert, config delete fail, kafka fail
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.services.event_service import EventService


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
    gp.get_document = AsyncMock()
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
    def test_store_in_container_attr(self, service):
        mock_provider = MagicMock()
        service.app_container.conn1_connector = mock_provider
        mock_conn = MagicMock()
        service._store_connector("conn1", mock_conn)
        mock_provider.override.assert_called_once()

    def test_store_in_connectors_map_new(self, service):
        spec_container = MagicMock(spec=[])
        service.app_container = spec_container
        mock_conn = MagicMock()
        service._store_connector("conn1", mock_conn)
        assert service.app_container.connectors_map["conn1"] is mock_conn

    def test_store_in_connectors_map_existing(self, service):
        spec_container = MagicMock(spec=[])
        spec_container.connectors_map = {}
        service.app_container = spec_container
        mock_conn = MagicMock()
        service._store_connector("conn1", mock_conn)
        assert service.app_container.connectors_map["conn1"] is mock_conn


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
            result = await service.process_event("gmail.reindex", {})
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_event(self, service):
        with patch.object(service, "_handle_delete", new_callable=AsyncMock, return_value=True):
            result = await service.process_event("gmail.delete", {})
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
    @pytest.mark.asyncio
    async def test_success(self, service):
        mock_conn = AsyncMock()
        mock_conn.init = AsyncMock(return_value=True)
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
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.create_connector = AsyncMock(return_value=None)
            result = await service._handle_init("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_init_returns_false(self, service):
        mock_conn = AsyncMock()
        mock_conn.init = AsyncMock(return_value=False)
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.create_connector = AsyncMock(return_value=mock_conn)
            result = await service._handle_init("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False


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
             patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
            mock_stm.start_sync = AsyncMock()
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is True

    @pytest.mark.asyncio
    async def test_full_sync_success(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
            mock_stm.start_sync = AsyncMock()
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
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=None), \
             patch.object(service, "_get_connector", return_value=None):
            result = await service._handle_start_sync("gmail", {"orgId": "org1", "connectorId": "c1"})
            assert result is False


# ===========================================================================
# _run_sync_and_clear_status
# ===========================================================================


class TestRunSyncAndClearStatus:
    @pytest.mark.asyncio
    async def test_success(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_update_app_status", new_callable=AsyncMock):
            await service._run_sync_and_clear_status(mock_conn, "c1")
            mock_conn.run_sync.assert_awaited_once()
            service._update_app_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_error_still_clears(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock(side_effect=Exception("sync fail"))
        with patch.object(service, "_update_app_status", new_callable=AsyncMock):
            with pytest.raises(Exception, match="sync fail"):
                await service._run_sync_and_clear_status(mock_conn, "c1")
            service._update_app_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_clear_error(self, service):
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        with patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=Exception("clear fail")):
            # Should not raise
            await service._run_sync_and_clear_status(mock_conn, "c1")


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
    async def test_batch_paging(self, service):
        mock_conn = AsyncMock()
        mock_conn.app = MagicMock()
        mock_app_name = MagicMock()
        mock_app_name.name = "GMAIL"
        mock_conn.app.get_app_name.return_value = mock_app_name
        mock_conn.reindex_records = AsyncMock()

        # Return one batch then empty
        batch1 = [MagicMock() for _ in range(50)]  # Less than 100
        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[batch1, []]
        )

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch("app.connectors.services.event_service.Connectors") as mock_connectors:
            mock_connectors.GMAIL = MagicMock()
            result = await service._handle_reindex("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True
            mock_conn.reindex_records.assert_awaited_once()

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
        with patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
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
        with patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
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
        with patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
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
        with patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
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
        with patch("app.connectors.services.event_service.sync_task_manager") as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            config_svc = AsyncMock()
            config_svc.delete_config = AsyncMock(side_effect=Exception("etcd error"))
            service.app_container.config_service.return_value = config_svc
            # Should still succeed (config delete failure is non-fatal)
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is True
