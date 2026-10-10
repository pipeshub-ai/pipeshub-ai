"""Extended tests for EventService covering uncovered lines.

Targets:
- Lines 216-217: delete action dispatching
- Lines 354-355: sync point deletion failure (exception path)
- Line 466: _reindex_task_key with various argument combinations
- Lines 599-600: _run_reindex record_id mode dispatching
- Lines 609-613: _run_reindex record_group_id mode dispatching
- Lines 617-624: _run_reindex status-only mode dispatching
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, Connectors, ProgressStatus
from app.connectors.services.event_service import EventService
from tests.unit.connectors.services.coordinator_stub import installed_stub, spawned


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_lease_manager():
    with installed_stub() as stub:
        yield stub


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
    gp.delete_connector_instance = AsyncMock(return_value={
        "success": True, "virtual_record_ids": [], "deleted_records_count": 0,
    })
    gp.get_records_by_parent_record = AsyncMock(return_value=[])
    gp.get_records_by_record_group = AsyncMock(return_value=[])
    gp.get_records_by_status = AsyncMock(return_value=[])
    gp.update_indexing_status_for_record_ids = AsyncMock()
    gp.update_node = AsyncMock()
    return gp


@pytest.fixture
def mock_container():
    container = MagicMock()
    container.config_service.return_value = AsyncMock()
    container.messaging_producer = AsyncMock()
    container.messaging_producer.send_message = AsyncMock()
    container.connector_notification_service.return_value = MagicMock()
    return container


@pytest.fixture
def service(mock_logger, mock_container, mock_graph_provider):
    return EventService(mock_logger, mock_container, mock_graph_provider)


def _make_mock_record(record_id="r1", is_placeholder=False, indexing_status="NOT_STARTED"):
    rec = MagicMock()
    rec.id = record_id
    rec.is_placeholder = is_placeholder
    rec.indexing_status = indexing_status
    return rec


# ===========================================================================
# Lines 216-217: process_event dispatches "delete" action
# ===========================================================================



async def _drain_storage_releases() -> None:
    from app.connectors.services import event_service
    while event_service._storage_release_tasks:
        await asyncio.gather(*list(event_service._storage_release_tasks))

class TestProcessEventDeleteAction:
    @pytest.mark.asyncio
    async def test_delete_action_dispatched(self, service):
        """process_event routes 'connector.delete' to _handle_delete."""
        with patch.object(service, "_handle_delete", new_callable=AsyncMock, return_value=True) as mock_del:
            result = await service.process_event("gmail.delete", {
                "orgId": "org1", "connectorId": "c1",
            })
            assert result is True
            mock_del.assert_awaited_once_with("gmail", {
                "orgId": "org1", "connectorId": "c1",
            })

    @pytest.mark.asyncio
    async def test_unknown_action_returns_false(self, service):
        """Unknown action returns False and logs error."""
        result = await service.process_event("gmail.unknown_action", {})
        assert result is False
        service.logger.error.assert_called()


# ===========================================================================
# Lines 354-355: sync point deletion raises exception
# ===========================================================================


class TestSyncPointDeletionException:
    @pytest.mark.asyncio
    async def test_sync_point_deletion_exception_continues(self, service, stub_lease_manager):
        """Exception during sync point deletion logs error and continues sync."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()
        mock_conn.app = MagicMock()
        mock_conn.app.get_app_name.return_value = MagicMock(name="gmail")

        service.graph_provider.delete_sync_points_by_connector_id = AsyncMock(
            side_effect=Exception("db error")
        )
        service.graph_provider.delete_connector_sync_edges = AsyncMock(return_value=(0, True))

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(stub_lease_manager, "spawn", AsyncMock(side_effect=spawned)):

            result = await service._handle_start_sync("gmail", {
                "orgId": "org1",
                "connectorId": "c1",
                "fullSync": True,
            })

            assert result is True
            service.logger.error.assert_called()


# ===========================================================================
# Line 466: _reindex_task_key
# ===========================================================================


class TestReindexTaskKey:
    def test_key_with_record_id(self):
        key = EventService._reindex_task_key("c1", "r1", None, 3, None, None)
        assert key == "reindex:c1:r1:3:*:"

    def test_key_with_record_group_id(self):
        key = EventService._reindex_task_key("c1", None, "rg1", 0, "user1", None)
        assert key == "reindex:c1:rg1:0:user1:"

    def test_key_with_status_filters(self):
        key = EventService._reindex_task_key("c1", None, None, 0, None, ["FAILED", "AUTO_INDEX_OFF"])
        assert key == "reindex:c1:*:0:*:AUTO_INDEX_OFF,FAILED"

    def test_key_all_none(self):
        key = EventService._reindex_task_key("c1", None, None, 0, None, None)
        assert key == "reindex:c1:*:0:*:"


# ===========================================================================
# Lines 599-600, 609-613, 617-624: _run_reindex dispatch modes
# ===========================================================================


class TestRunReindexModes:

    @pytest.mark.asyncio
    async def test_record_id_mode(self, service):
        """Mode 1: reindex by record_id calls get_records_by_parent_record."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_parent_record = AsyncMock(return_value=[])

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id="r1",
            record_group_id=None,
            depth=2,
            user_key=None,
            status_filters=None,
        )

        service.graph_provider.get_records_by_parent_record.assert_awaited_once()
        args = service.graph_provider.get_records_by_parent_record.call_args
        assert args.kwargs["parent_record_id"] == "r1"
        assert args.kwargs["depth"] == 2

    @pytest.mark.asyncio
    async def test_record_group_mode(self, service):
        """Mode 2: reindex by record_group_id calls get_records_by_record_group."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_record_group = AsyncMock(return_value=[])

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id="rg1",
            depth=0,
            user_key="u1",
            status_filters=None,
        )

        service.graph_provider.get_records_by_record_group.assert_awaited_once()
        args = service.graph_provider.get_records_by_record_group.call_args
        assert args.kwargs["record_group_id"] == "rg1"

    @pytest.mark.asyncio
    async def test_status_only_mode(self, service):
        """Mode 3: reindex by status filters calls get_records_by_status."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_status = AsyncMock(return_value=[])

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id=None,
            depth=0,
            user_key=None,
            status_filters=["FAILED"],
        )

        service.graph_provider.get_records_by_status.assert_awaited_once()
        args = service.graph_provider.get_records_by_status.call_args
        assert args.kwargs["status_filters"] == ["FAILED"]
        assert args.kwargs["is_placeholder"] is False

    @pytest.mark.asyncio
    async def test_reindex_processes_batch(self, service):
        """When records are returned, they are processed and status updated."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        rec1 = _make_mock_record("r1")
        rec2 = _make_mock_record("r2")

        # Return two records then empty to end the loop
        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[[rec1, rec2], []]
        )

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id=None,
            depth=0,
            user_key=None,
            status_filters=["FAILED"],
        )

        service.graph_provider.update_indexing_status_for_record_ids.assert_awaited_once_with(
            ["r1", "r2"], ProgressStatus.NOT_STARTED.value
        )
        connector.reindex_records.assert_awaited_once()
        # Verify indexing_status was cleared in memory
        assert rec1.indexing_status == ProgressStatus.NOT_STARTED.value
        assert rec2.indexing_status == ProgressStatus.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_reindex_skips_placeholder_records(self, service):
        """Placeholder records are filtered out after fetch."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        placeholder = _make_mock_record("r1", is_placeholder=True)
        real_rec = _make_mock_record("r2", is_placeholder=False)

        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[[placeholder, real_rec], []]
        )

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id=None,
            depth=0,
            user_key=None,
            status_filters=None,
        )

        # Only the real record should be passed to reindex_records
        call_args = connector.reindex_records.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].id == "r2"

    @pytest.mark.asyncio
    async def test_reindex_continues_on_all_placeholder_batch(self, service):
        """When a batch contains only placeholders, the loop continues with the cursor."""
        connector = AsyncMock()
        connector.reindex_records = AsyncMock()

        # First batch: 100 placeholders (full batch size triggers continuation)
        placeholders = [_make_mock_record(f"p{i}", is_placeholder=True) for i in range(100)]
        # Second batch: empty => terminates
        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[placeholders, []]
        )

        await service._run_reindex(
            connector=connector,
            connector_name="gmail",
            connector_id="c1",
            org_id="org1",
            record_id=None,
            record_group_id=None,
            depth=0,
            user_key=None,
            status_filters=None,
        )

        # reindex_records should not have been called since all were placeholders
        connector.reindex_records.assert_not_awaited()


# ===========================================================================
# _handle_delete: success and failure paths
# ===========================================================================


class TestHandleDelete:

    @pytest.mark.asyncio
    async def test_delete_missing_fields_returns_false(self, service):
        """Missing orgId or connectorId returns False."""
        result = await service._handle_delete("gmail", {"orgId": "org1"})
        assert result is False

        result = await service._handle_delete("gmail", {"connectorId": "c1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_success_publishes_bulk_delete(self, service):
        """Successful delete publishes bulkDeleteRecords for virtual record IDs."""
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True,
            "virtual_record_ids": ["vr1", "vr2"],
            "deleted_records_count": 2,
        })

        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm:
            mock_rtm.cancel_by_prefix = AsyncMock()

            result = await service._handle_delete("gmail", {
                "orgId": "org1",
                "connectorId": "c1",
                "previousIsActive": True,
            })

        assert result is True
        # The record cleanup, then the entity cleanup.
        assert service.app_container.messaging_producer.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_a_deleted_connector_frees_its_queue_lane_after_its_cleanup_events(
        self, service
    ) -> None:
        """Freed after, so the cleanup events still go to the lane its earlier
        events are on."""
        order: list[str] = []
        service.app_container.messaging_producer.send_message = AsyncMock(
            side_effect=lambda **kwargs: order.append(kwargs["message"]["eventType"])
        )

        async def freed(_logger, connector_id) -> None:
            order.append(f"free:{connector_id}")

        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm, \
             patch(
                 "app.connectors.services.event_service.free_lane_of_deleted_connector",
                 side_effect=freed,
             ):
            mock_rtm.cancel_by_prefix = AsyncMock()
            result = await service._handle_delete("gmail", {"orgId": "org1", "connectorId": "c1"})

        assert result is True
        assert order[-1] == "free:c1"
        assert len(order) > 1

    @pytest.mark.asyncio
    async def test_a_connector_whose_delete_failed_keeps_its_queue_lane(self, service) -> None:
        service.graph_provider.delete_connector_instance = AsyncMock(
            return_value={"success": False, "error": "boom"}
        )
        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm, \
             patch(
                 "app.connectors.services.event_service.free_lane_of_deleted_connector",
                 new_callable=AsyncMock,
             ) as free:
            mock_rtm.cancel_by_prefix = AsyncMock()
            await service._handle_delete("gmail", {"orgId": "org1", "connectorId": "c1"})

        free.assert_not_awaited()

    async def _delete_with_helper(self, service, helper):
        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm, \
             patch("app.connectors.services.event_service.StorageCleanupHelper", return_value=helper):
            mock_rtm.cancel_by_prefix = AsyncMock()
            result = await service._handle_delete("gmail", {"orgId": "org1", "connectorId": "c1"})
        await _drain_storage_releases()
        return result

    @pytest.mark.asyncio
    async def test_the_delete_returns_before_its_storage_release_finishes(self, service):
        release_may_finish = asyncio.Event()

        async def release(*_a):
            from app.connectors.core.base.data_processor.storage_cleanup import StorageReleaseResult
            await release_may_finish.wait()
            return StorageReleaseResult(completed=True)

        helper = AsyncMock()
        helper.release_connector_storage = AsyncMock(side_effect=release)
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True})
        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm, \
             patch("app.connectors.services.event_service.StorageCleanupHelper", return_value=helper):
            mock_rtm.cancel_by_prefix = AsyncMock()
            assert await asyncio.wait_for(
                service._handle_delete("gmail", {"orgId": "org1", "connectorId": "c1"}), timeout=5,
            ) is True

        helper.close.assert_not_awaited()
        release_may_finish.set()
        await _drain_storage_releases()
        helper.release_connector_storage.assert_awaited_once_with("org1", "c1")
        helper.close.assert_awaited_once()

    @staticmethod
    def _helper(calls, completed=True):
        from app.connectors.core.base.data_processor.storage_cleanup import StorageReleaseResult

        helper = AsyncMock()
        helper.release_connector_storage = AsyncMock(
            side_effect=lambda *a: calls.append("release") or StorageReleaseResult(completed=completed)
        )
        return helper

    @pytest.mark.asyncio
    async def test_storage_is_released_after_the_graph_delete_and_its_intent_cleared(
        self, service, mock_container,
    ):
        calls = []
        config = mock_container.config_service.return_value
        config.set_config = AsyncMock(side_effect=lambda key, _v: calls.append(key) or True)
        config.delete_config = AsyncMock(side_effect=lambda key: calls.append(f"clear:{key}") or True)
        service.graph_provider.delete_connector_instance = AsyncMock(
            side_effect=lambda **kw: calls.append("graph") or {"success": True}
        )
        helper = self._helper(calls)

        assert await self._delete_with_helper(service, helper) is True

        assert [c for c in calls if "/services/connectors/" not in c][:4] == [
            "/services/entityCleanup/pending/c1",
            "/services/storageRelease/pending/c1",
            "graph",
            "release",
        ]
        assert "clear:/services/storageRelease/pending/c1" in calls
        helper.release_connector_storage.assert_awaited_once_with("org1", "c1")
        helper.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unfinished_release_keeps_its_intent_and_does_not_fail_the_delete(
        self, service, mock_container,
    ):
        config = mock_container.config_service.return_value
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True})

        assert await self._delete_with_helper(service, self._helper([], completed=False)) is True

        cleared = [c.args[0] for c in config.delete_config.await_args_list]
        assert "/services/storageRelease/pending/c1" not in cleared
        key, intent = config.set_config.await_args.args
        assert key == "/services/storageRelease/pending/c1" and intent["attempts"] == 1

    @pytest.mark.asyncio
    async def test_a_release_that_raises_does_not_fail_the_delete(self, service):
        helper = AsyncMock()
        helper.release_connector_storage = AsyncMock(side_effect=RuntimeError("storage down"))
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True})

        assert await self._delete_with_helper(service, helper) is True
        helper.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_graph_delete_without_a_recorded_storage_intent(self, service, mock_container):
        config = mock_container.config_service.return_value
        config.set_config = AsyncMock(side_effect=lambda key, _v: "storageRelease" not in key)
        helper = self._helper([])

        assert await self._delete_with_helper(service, helper) is False

        service.graph_provider.delete_connector_instance.assert_not_called()
        helper.release_connector_storage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_failure_reverts_status(self, service):
        """Failed graph DB delete reverts connector status."""
        service.graph_provider.get_document = AsyncMock(return_value={"_key": "c1", "status": "DELETING"})
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": False,
            "error": "test failure",
        })

        with patch("app.connectors.services.event_service.reindex_task_manager") as mock_rtm:
            mock_rtm.cancel_by_prefix = AsyncMock()

            result = await service._handle_delete("gmail", {
                "orgId": "org1",
                "connectorId": "c1",
                "previousIsActive": True,
            })

        assert result is False
        # Should have tried to revert status
        service.graph_provider.batch_upsert_nodes.assert_awaited()
