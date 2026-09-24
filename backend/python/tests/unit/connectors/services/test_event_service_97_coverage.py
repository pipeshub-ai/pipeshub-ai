"""
Tests to push app/connectors/services/event_service.py coverage above 97%.

Targets uncovered lines from the coverage report:
- Lines 209-211: _handle_init exception handler (with org_id variable reference)
- Lines 257-260: sync point deletion returns success=False
- Lines 270-272: sync edge deletion raises exception
- Lines 278-285: full sync prep general exception + revert lock + revert lock failure
- Lines 292-293: unlock failure after full sync prep
- Lines 301-302: normal sync status update failure
- Branch 418->372: batch paging loop with full batch requiring next iteration
- Lines 520-521: revert status failure after delete exception
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.services.event_service import EventService


def _spawn_raises(message):
    """Stand-in for start_if_idle failing during full-sync prep.

    Still closes the coroutine it was handed — start_if_idle owns it, and an
    unclosed one turns into a 'never awaited' warning that masks the assertion.
    """

    def _inner(key, coro):
        coro.close()
        raise Exception(message)

    return _inner




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
    gp.delete_connector_instance = AsyncMock(return_value={
        "success": True, "virtual_record_ids": [], "deleted_records_count": 0
    })
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


#: The coordinator installed for the current test, so a `with` block can
#: reach it without every test taking the fixture as a parameter.
_STUB = None


def _stub():
    return _STUB


@pytest.fixture(autouse=True)
def stub_lease_manager():
    """Grant the lease unconditionally.

    _handle_start_sync acquires before doing anything else, so without this every
    test here would exit at the lease and never reach the path it targets.
    """

    class _Stub:
        def __init__(self) -> None:
            self.spawn = AsyncMock(return_value=MagicMock(name="task"))
            self.is_running_here = MagicMock(return_value=False)
            self.is_running = AsyncMock(return_value=False)
            self.cancel_and_wait = AsyncMock()
            self.request_stop = AsyncMock(return_value=False)
            self.reports_liveness = False

        async def try_claim_org(self, org_id) -> bool:
            return True

        async def begin(self, connector_id, *, org_id=None, message_ts_ms=None):
            from app.connectors.core.sync.sync_coordinator import Admission, SyncLease

            return Admission.GRANTED, SyncLease(connector_id, "stub-token", 1)

        async def end(self, lease) -> bool:
            return True

        def running_count(self) -> int:
            return 0

    global _STUB
    stub = _Stub()
    _STUB = stub
    with patch(
        "app.connectors.services.event_service.get_coordinator",
        return_value=stub,
    ):
        yield stub


@pytest.fixture
def service(mock_logger, mock_container, mock_graph_provider):
    return EventService(mock_logger, mock_container, mock_graph_provider)


# ===========================================================================
# Lines 209-211: _handle_init exception handler
# ===========================================================================

class TestHandleInitException:
    """Cover the except block in _handle_init that logs org_id and exception."""

    @pytest.mark.asyncio
    async def test_init_general_exception(self, service):
        """Lines 209-211: exception during init logs error with org_id."""
        service.graph_provider.get_document = AsyncMock(
            return_value={"_key": "c1", "scope": "personal", "createdBy": "u1"}
        )
        with patch("app.connectors.services.event_service.ConnectorFactory") as mock_factory, \
             patch("app.connectors.services.event_service.GraphDataStore"):
            mock_factory.create_connector = AsyncMock(side_effect=Exception("unexpected error"))
            result = await service._handle_init("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is False
            # Verify the error was logged (the format uses %s for org_id and exception)
            service.logger.error.assert_called()


# ===========================================================================
# Lines 257-260: sync point deletion returns success=False
# ===========================================================================

class TestFullSyncSyncPointDeletionFailure:
    """Cover the path where delete_sync_points_by_connector_id returns success=False."""

    @pytest.mark.asyncio
    async def test_sync_points_delete_fails(self, service):
        """Lines 257: sync point deletion returns False => warning logged."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        # Sync points deletion returns failure
        service.graph_provider.delete_sync_points_by_connector_id = AsyncMock(
            return_value=(0, False)
        )

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_sync_points_delete_exception(self, service):
        """Lines 258-260: sync point deletion raises exception => error + warning logged."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        # Sync points deletion raises an exception
        service.graph_provider.delete_sync_points_by_connector_id = AsyncMock(
            side_effect=Exception("sync point DB error")
        )

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is True
            service.logger.warning.assert_any_call(
                "Continuing with sync despite sync point deletion failure"
            )


# ===========================================================================
# Lines 270-272: sync edge deletion raises exception
# ===========================================================================

class TestFullSyncEdgeDeletionException:
    """Cover the except block for delete_connector_sync_edges."""

    @pytest.mark.asyncio
    async def test_sync_edges_delete_exception(self, service):
        """Lines 271-272: sync edge deletion raises exception => error logged."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        # Sync edges deletion raises an exception
        service.graph_provider.delete_connector_sync_edges = AsyncMock(
            side_effect=Exception("edge deletion error")
        )

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is True

    @pytest.mark.asyncio
    async def test_sync_edges_delete_returns_false(self, service):
        """Lines 269-270: sync edge deletion returns success=False => warning logged."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        # Sync edges returns failure
        service.graph_provider.delete_connector_sync_edges = AsyncMock(
            return_value=(0, False)
        )

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is True


# ===========================================================================
# Lines 278-285: full sync prep general exception + revert lock
# ===========================================================================

class TestFullSyncPrepException:
    """Cover the outer except block during full sync prep and revert lock."""

    @pytest.mark.asyncio
    async def test_full_sync_prep_fails_and_reverts(self, service):
        """Lines 278-285: the sync spawn raises => revert lock."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        call_count = [0]

        async def update_status_side_effect(connector_id, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: set FULL_SYNCING + lock (succeeds)
                return None
            elif call_count[0] == 2:
                # Second call: revert to IDLE (succeeds)
                return None
            return None

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=update_status_side_effect), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            # the spawn raises during prep => the lock must be reverted
            mock_stm.side_effect = _spawn_raises("task manager error")
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is False

    @pytest.mark.asyncio
    async def test_full_sync_prep_fails_revert_also_fails(self, service):
        """Lines 283-284: revert lock also fails after prep failure."""
        mock_conn = AsyncMock()

        call_count = [0]

        async def update_status_side_effect(connector_id, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # Initial lock succeeds
            else:
                raise Exception("revert failed")  # Revert fails

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=update_status_side_effect), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.side_effect = _spawn_raises("task error")
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            assert result is False


# ===========================================================================
# Lines 292-293: unlock failure after successful full sync prep
# ===========================================================================

class TestFullSyncUnlockFailure:
    """Cover the except block for the final unlock after full sync prep."""

    @pytest.mark.asyncio
    async def test_unlock_after_prep_fails(self, service):
        """Lines 292-293: unlock fails after successful prep => non-fatal, logged."""
        mock_conn = AsyncMock()

        call_count = [0]

        async def update_status_side_effect(connector_id, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # Initial lock succeeds
            elif call_count[0] == 2:
                # Final unlock fails
                raise Exception("unlock error")
            return None

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=update_status_side_effect), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": True
            })
            # Should still succeed despite unlock failure
            assert result is True


# ===========================================================================
# Lines 301-302: normal sync status update failure
# ===========================================================================

class TestNormalSyncStatusFailure:
    """Cover the except block for status update in normal (non-full) sync."""

    @pytest.mark.asyncio
    async def test_normal_sync_status_update_fails(self, service):
        """Lines 301-302: status update fails in normal sync => non-fatal."""
        mock_conn = AsyncMock()
        # Avoid default AsyncMock doc: MagicMock.get() is truthy and would force full-sync path
        service.graph_provider.get_document = AsyncMock(return_value=None)

        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=mock_conn), \
             patch.object(service, "_get_connector", return_value=mock_conn), \
             patch.object(service, "_update_app_status", new_callable=AsyncMock, side_effect=Exception("status write failed")), \
             patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.start_sync = AsyncMock()
            mock_stm.side_effect = _spawned
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1", "fullSync": False
            })
            # Should still succeed
            assert result is True


# ===========================================================================
# Branch 418->372: batch paging with full batch (100 records)
# ===========================================================================

class TestReindexBatchPaging:
    """Cover the batch paging loop that iterates when batch_size records returned."""

    @staticmethod
    def _batch(n, start=0):
        # Ids must be sortable strings: the cursor is taken from records[-1].id.
        return [MagicMock(id=f"rec-{i:04d}", is_placeholder=False) for i in range(start, start + n)]

    @pytest.mark.asyncio
    async def test_multiple_batches_by_status(self, service):
        """A full batch keeps paging; a short batch ends the walk."""
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[self._batch(100), self._batch(50, start=100)]
        )
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        await service._run_reindex(
            connector=mock_conn, connector_name="gmail", connector_id="c1",
            org_id="org1", record_id=None, record_group_id=None, depth=0,
            user_key=None, status_filters=["FAILED"],
        )

        assert mock_conn.reindex_records.await_count == 2

    @pytest.mark.asyncio
    async def test_cursor_advances_past_processed_records(self, service):
        """Each batch must resume after the previous batch's last id.

        The cursor is what stops a record that flips back into the filter from being
        picked up twice in one run.
        """
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_status = AsyncMock(
            side_effect=[self._batch(100), self._batch(10, start=100)]
        )
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        await service._run_reindex(
            connector=mock_conn, connector_name="gmail", connector_id="c1",
            org_id="org1", record_id=None, record_group_id=None, depth=0,
            user_key=None, status_filters=["FAILED"],
        )

        calls = service.graph_provider.get_records_by_status.await_args_list
        assert calls[0].kwargs["after_key"] is None
        assert calls[1].kwargs["after_key"] == "rec-0099"
        # An actively-indexing record must never be republished.
        assert calls[0].kwargs["exclude_statuses"] == [ProgressStatus.IN_PROGRESS.value]

    @pytest.mark.asyncio
    async def test_multiple_batches_by_record_id(self, service):
        """Paging loop with record ID mode: multiple batches."""
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_parent_record = AsyncMock(
            side_effect=[self._batch(100), self._batch(30, start=100)]
        )
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        await service._run_reindex(
            connector=mock_conn, connector_name="gmail", connector_id="c1",
            org_id="org1", record_id="r1", record_group_id=None, depth=2,
            user_key=None, status_filters=None,
        )

        assert mock_conn.reindex_records.await_count == 2

    @pytest.mark.asyncio
    async def test_multiple_batches_by_record_group(self, service):
        """An empty batch ends the walk."""
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock()

        service.graph_provider.get_records_by_record_group = AsyncMock(
            side_effect=[self._batch(100), []]
        )
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        await service._run_reindex(
            connector=mock_conn, connector_name="gmail", connector_id="c1",
            org_id="org1", record_id=None, record_group_id="rg1", depth=1,
            user_key=None, status_filters=None,
        )

        assert mock_conn.reindex_records.await_count == 1

    @pytest.mark.asyncio
    async def test_reindex_exception_propagates_out_of_run(self, service):
        """_run_reindex does not swallow: the task manager logs the failure.

        _handle_reindex can no longer report this - it returns before the work starts.
        """
        mock_conn = AsyncMock()
        mock_conn.reindex_records = AsyncMock(side_effect=Exception("reindex boom"))

        service.graph_provider.get_records_by_status = AsyncMock(
            return_value=self._batch(1)
        )
        service.graph_provider.update_indexing_status_for_record_ids = AsyncMock()

        with pytest.raises(Exception, match="reindex boom"):
            await service._run_reindex(
                connector=mock_conn, connector_name="gmail", connector_id="c1",
                org_id="org1", record_id=None, record_group_id=None, depth=0,
                user_key=None, status_filters=["FAILED"],
            )


# ===========================================================================
# Lines 520-521: revert status failure after delete exception
# ===========================================================================

class TestDeleteRevertStatusFailure:
    """Cover the double-exception path in _handle_delete."""

    @pytest.mark.asyncio
    async def test_delete_fails_and_revert_also_fails(self, service):
        """Lines 520-521: delete fails then revert also fails."""
        service.graph_provider.delete_connector_instance = AsyncMock(
            return_value={"success": False, "error": "DB error"}
        )
        # Revert also fails
        service.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=Exception("revert failed")
        )

        with patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1", "previousIsActive": True
            })
            assert result is False
            # Verify double-failure error was logged
            service.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_delete_raises_and_revert_also_fails(self, service):
        """Delete raises an exception (not just returns success=False), revert fails too."""
        service.graph_provider.delete_connector_instance = AsyncMock(
            side_effect=Exception("unexpected DB crash")
        )
        service.graph_provider.batch_upsert_nodes = AsyncMock(
            side_effect=Exception("revert also crashed")
        )

        with patch.object(_stub(), "spawn", new_callable=AsyncMock) as mock_stm:
            mock_stm.cancel_sync = AsyncMock()
            result = await service._handle_delete("gmail", {
                "orgId": "org1", "connectorId": "c1", "previousIsActive": False
            })
            assert result is False


# ===========================================================================
# Additional edge cases for completeness
# ===========================================================================

class TestUpdateAppStatusNeitherStatusNorLocked:
    """Cover _update_app_status when neither status nor is_locked are passed."""

    @pytest.mark.asyncio
    async def test_neither_status_nor_locked(self, service):
        """Only id and timestamp in payload when both are None."""
        await service._update_app_status("conn1")
        call_args = service.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert "status" not in call_args
        assert "isLocked" not in call_args
        assert "updatedAtTimestamp" in call_args


class TestHandleDeleteMissingConnectorId:
    """Cover _handle_delete with missing connectorId."""

    @pytest.mark.asyncio
    async def test_missing_connector_id(self, service):
        result = await service._handle_delete("gmail", {"orgId": "org1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_org_id(self, service):
        result = await service._handle_delete("gmail", {"connectorId": "c1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_both(self, service):
        result = await service._handle_delete("gmail", {})
        assert result is False


class TestHandleStartSyncEnsureConnectorFails:
    """Ensure connector returns None but get_connector also returns None."""

    @pytest.mark.asyncio
    async def test_ensure_returns_none_then_get_returns_none(self, service):
        """Both _ensure_connector and _get_connector return None => False."""
        with patch.object(service, "_ensure_connector", new_callable=AsyncMock, return_value=None), \
             patch.object(service, "_get_connector", return_value=None):
            result = await service._handle_start_sync("gmail", {
                "orgId": "org1", "connectorId": "c1"
            })
            assert result is False
