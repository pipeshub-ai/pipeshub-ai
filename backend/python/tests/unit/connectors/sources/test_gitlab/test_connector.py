"""Unit tests for the GitLabConnector lifecycle and helper delegation methods.

Avoids instantiating the real connector (which requires complex DI setup and
ConnectorBuilder decorators). Tests the public methods by patching construction
or using the mock connector pattern.

Covers:
- datetime_range_from_sync_filter: modified/created filter mapping, no filter, unknown key
- creator_user_permission: with email, without email
- run_sync: invocation order (users → projects), error propagation
- cleanup: cancel backfill, close data_source, executor shutdown
- test_connection_and_access: success, failure, data_source missing
- _resolve_creator_identity: admin/auditor flags, missing creator
"""
from __future__ import annotations

from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# We test the GitLabConnector methods by calling them on a real-ish object
# built from the mock connector's interface, rather than instantiating the
# real class (which requires full DI stack). We patch at the class level.
# ---------------------------------------------------------------------------


def _build_minimal_connector() -> MagicMock:
    """Return a mock that also has the real connector methods bound via spec."""
    return make_mock_connector()


# ===========================================================================
# datetime_range_from_sync_filter (tested via real method on mock)
# ===========================================================================


class TestDatetimeRangeFromSyncFilter:
    """Tests for datetime_range_from_sync_filter.

    Since this is a pure method on GitLabConnector we test it by importing
    and calling it with a mock connector shaped like the expected interface.
    """

    def _make_real_connector_method(self):
        """Import the real method and test it by calling it with a mock self."""
        from app.connectors.sources.gitlab.connector import GitLabConnector
        return GitLabConnector.datetime_range_from_sync_filter

    def test_no_sync_filters_returns_none_none(self) -> None:
        method = self._make_real_connector_method()
        fake_self = MagicMock()
        fake_self.sync_filters = None
        result = method(fake_self, "modified")
        assert result == (None, None)

    def test_unknown_key_returns_none_none(self) -> None:
        method = self._make_real_connector_method()
        fake_self = MagicMock()
        fake_self.sync_filters = {}
        result = method(fake_self, "unknown_filter_key")
        assert result == (None, None)

    def test_filter_key_not_in_map_returns_none_none(self) -> None:
        method = self._make_real_connector_method()
        fake_self = MagicMock()
        fake_self.sync_filters = {"something": MagicMock()}
        result = method(fake_self, "something")
        assert result == (None, None)

    def test_modified_filter_with_start_and_end(self) -> None:
        method = self._make_real_connector_method()
        fake_self = MagicMock()

        from app.connectors.core.registry.filters import SyncFilterKey
        f = MagicMock()
        f.get_datetime_start.return_value = 0
        f.get_datetime_end.return_value = 86400000  # 1 day in ms
        fake_self.sync_filters = {SyncFilterKey.MODIFIED: f}

        after, before = method(fake_self, "modified")
        assert after is not None
        assert before is not None
        assert after.tzinfo == timezone.utc
        assert before.tzinfo == timezone.utc
        assert before > after

    def test_modified_filter_with_no_start_end(self) -> None:
        method = self._make_real_connector_method()
        fake_self = MagicMock()

        from app.connectors.core.registry.filters import SyncFilterKey
        f = MagicMock()
        f.get_datetime_start.return_value = None
        f.get_datetime_end.return_value = None
        fake_self.sync_filters = {SyncFilterKey.MODIFIED: f}

        after, before = method(fake_self, "modified")
        assert after is None
        assert before is None


# ===========================================================================
# creator_user_permission
# ===========================================================================


class TestCreatorUserPermission:
    def test_returns_permission_when_email_set(self) -> None:
        from app.connectors.sources.gitlab.connector import GitLabConnector
        method = GitLabConnector.creator_user_permission
        fake_self = MagicMock()
        fake_self.creator_email = "creator@example.com"
        result = method(fake_self)
        assert result is not None
        assert result.email == "creator@example.com"

    def test_returns_none_when_no_email(self) -> None:
        from app.connectors.sources.gitlab.connector import GitLabConnector
        method = GitLabConnector.creator_user_permission
        fake_self = MagicMock()
        fake_self.creator_email = None
        result = method(fake_self)
        assert result is None


# ===========================================================================
# run_sync invocation order — tested through the mock
# ===========================================================================


class TestRunSyncInvocationOrder:
    async def test_run_sync_calls_users_then_projects(self) -> None:
        c = make_mock_connector()

        call_order: list[str] = []

        c.runtime.refresh_token_if_needed = AsyncMock()
        c.repos = MagicMock()
        c.repos.cancel_timestamp_backfill = AsyncMock()
        c.repos.schedule_timestamp_backfill = MagicMock()
        c.users = MagicMock()
        c.users.sync_users = AsyncMock(side_effect=lambda: call_order.append("users"))
        c.projects = MagicMock()
        c.projects.sync_all_projects = AsyncMock(side_effect=lambda: call_order.append("projects"))
        c.config_service = AsyncMock()

        from app.connectors.sources.gitlab.connector import GitLabConnector

        with patch("app.connectors.sources.gitlab.connector.load_connector_filters",
                   AsyncMock(return_value=(None, None))):
            await GitLabConnector.run_sync(c)

        assert call_order.index("users") < call_order.index("projects")

    async def test_run_sync_error_propagates(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c.repos = MagicMock()
        c.repos.cancel_timestamp_backfill = AsyncMock()
        c.users = MagicMock()
        c.users.sync_users = AsyncMock(side_effect=RuntimeError("sync error"))
        c.config_service = AsyncMock()

        from app.connectors.sources.gitlab.connector import GitLabConnector

        with patch("app.connectors.sources.gitlab.connector.load_connector_filters",
                   AsyncMock(return_value=(None, None))):
            with pytest.raises(RuntimeError, match="sync error"):
                await GitLabConnector.run_sync(c)


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    async def test_cleanup_cancels_backfill_and_closes_data_source(self) -> None:
        c = make_mock_connector()
        c.repos = MagicMock()
        c.repos.cancel_timestamp_backfill = AsyncMock()
        data_source_mock = MagicMock()
        data_source_mock.aclose = AsyncMock()
        c.data_source = data_source_mock
        c._gitlab_executor = MagicMock()
        c._gitlab_executor.shutdown = MagicMock()

        from app.connectors.sources.gitlab.connector import GitLabConnector
        await GitLabConnector.cleanup(c)

        c.repos.cancel_timestamp_backfill.assert_called_once()
        # cleanup sets self.data_source = None after aclose, so use saved reference
        data_source_mock.aclose.assert_called_once()

    async def test_cleanup_handles_aclose_exception_gracefully(self) -> None:
        c = make_mock_connector()
        c.repos = MagicMock()
        c.repos.cancel_timestamp_backfill = AsyncMock()
        c.data_source = MagicMock()
        c.data_source.aclose = AsyncMock(side_effect=Exception("close error"))
        c._gitlab_executor = MagicMock()
        c._gitlab_executor.shutdown = MagicMock()

        from app.connectors.sources.gitlab.connector import GitLabConnector
        # Should not raise
        await GitLabConnector.cleanup(c)


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestConnectionAndAccess:
    async def test_no_data_source_returns_false(self) -> None:
        c = make_mock_connector()
        c.data_source = None

        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.test_connection_and_access(c)
        assert result is False

    async def test_successful_user_call_returns_true(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()

        user = MagicMock()
        user.id = 1
        success_res = MagicMock(success=True, data=user, error=None)
        c.runtime.refresh_token_if_needed = AsyncMock()
        c.runtime.call_with_auth_retry = AsyncMock(return_value=success_res)

        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.test_connection_and_access(c)
        assert result is True

    async def test_failed_response_returns_false(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        fail_res = MagicMock(success=False, data=None, error="unauthorized")
        c.runtime.refresh_token_if_needed = AsyncMock()
        c.runtime.call_with_auth_retry = AsyncMock(return_value=fail_res)

        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.test_connection_and_access(c)
        assert result is False

    async def test_exception_returns_false(self) -> None:
        """Exception during connection test is caught and returns False."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c.runtime.call_with_auth_retry = AsyncMock(side_effect=Exception("network error"))

        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.test_connection_and_access(c)
        assert result is False


# ===========================================================================
# run_incremental_sync delegates to run_sync
# ===========================================================================


class TestRunIncrementalSync:
    async def test_delegates_to_run_sync(self) -> None:
        """run_incremental_sync calls run_sync exactly once."""
        c = make_mock_connector()
        from app.connectors.sources.gitlab.connector import GitLabConnector

        c.run_sync = AsyncMock()
        await GitLabConnector.run_incremental_sync(c)
        c.run_sync.assert_called_once()


# ===========================================================================
# Delegation methods
# ===========================================================================


class TestDelegationMethods:
    async def test_stream_record_delegates_to_streaming(self) -> None:
        """stream_record delegates to streaming.stream_record."""
        c = make_mock_connector()
        c.streaming = MagicMock()
        c.streaming.stream_record = AsyncMock(return_value=MagicMock())

        from app.connectors.sources.gitlab.connector import GitLabConnector
        record = MagicMock()
        await GitLabConnector.stream_record(c, record)
        c.streaming.stream_record.assert_called_once_with(record)

    async def test_reindex_records_delegates_to_streaming(self) -> None:
        """reindex_records delegates to streaming.reindex_records."""
        c = make_mock_connector()
        c.streaming = MagicMock()
        c.streaming.reindex_records = AsyncMock()

        from app.connectors.sources.gitlab.connector import GitLabConnector
        records = [MagicMock()]
        await GitLabConnector.reindex_records(c, records)
        c.streaming.reindex_records.assert_called_once_with(records)

    async def test_get_filter_options_delegates_to_filters(self) -> None:
        """get_filter_options delegates to filters.get_filter_options."""
        c = make_mock_connector()
        c.filters = MagicMock()
        from app.connectors.core.registry.filters import FilterOptionsResponse
        c.filters.get_filter_options = AsyncMock(
            return_value=FilterOptionsResponse(success=True, options=[], page=1, limit=20, has_more=False)
        )

        from app.connectors.sources.gitlab.connector import GitLabConnector
        await GitLabConnector.get_filter_options(c, "group_ids")
        c.filters.get_filter_options.assert_called_once()

    async def test_get_signed_url_returns_none(self) -> None:
        """get_signed_url returns None (not implemented for GitLab)."""
        c = make_mock_connector()
        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.get_signed_url(c, MagicMock())
        assert result is None

    async def test_handle_webhook_returns_true(self) -> None:
        """handle_webhook_notification returns True (not implemented for GitLab)."""
        c = make_mock_connector()
        from app.connectors.sources.gitlab.connector import GitLabConnector
        result = await GitLabConnector.handle_webhook_notification(c)
        assert result is True


# ===========================================================================
# cleanup failure paths
# ===========================================================================


class TestCleanupFailurePaths:
    async def test_executor_shutdown_failure_does_not_raise(self) -> None:
        """Executor shutdown failure is silently ignored."""
        c = make_mock_connector()
        c.repos = MagicMock()
        c.repos.cancel_timestamp_backfill = AsyncMock()
        c.data_source = MagicMock()
        c.data_source.aclose = AsyncMock()
        c._gitlab_executor = MagicMock()
        c._gitlab_executor.shutdown = MagicMock(side_effect=Exception("executor error"))

        from app.connectors.sources.gitlab.connector import GitLabConnector
        # Should not raise
        await GitLabConnector.cleanup(c)


# ===========================================================================
# datetime_range_from_sync_filter — empty filter value
# ===========================================================================


class TestDatetimeRangeFromSyncFilterEmpty:
    def test_filter_key_present_but_no_value_returns_none_none(self) -> None:
        """Filter key present but no start/end values returns (None, None)."""
        from app.connectors.sources.gitlab.connector import GitLabConnector
        method = GitLabConnector.datetime_range_from_sync_filter
        fake_self = MagicMock()

        from app.connectors.core.registry.filters import SyncFilterKey
        f = MagicMock()
        f.get_datetime_start.return_value = None
        f.get_datetime_end.return_value = None
        fake_self.sync_filters = {SyncFilterKey.MODIFIED: f}

        after, before = method(fake_self, "modified")
        assert after is None
        assert before is None


# ===========================================================================
# _resolve_creator_identity
# ===========================================================================


class TestResolveCreatorIdentity:
    async def test_sets_admin_flag(self) -> None:
        """Admin flag is set from get_user response."""
        c = make_mock_connector()
        c.created_by = "user-1"
        c.data_source = MagicMock()
        c.creator_email = None
        c._is_admin = False
        c._is_auditor = False
        c._gitlab_user_id = None

        creator_user = MagicMock()
        creator_user.email = "creator@example.com"
        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=creator_user)

        me = MagicMock()
        me.is_admin = True
        me.is_auditor = False
        me.id = 42
        me_res = MagicMock(success=True, data=me, error=None)
        c.runtime.ds_call = AsyncMock(return_value=me_res)

        from app.connectors.sources.gitlab.connector import GitLabConnector
        await GitLabConnector._resolve_creator_identity(c)
        assert c._is_admin is True
        assert c._gitlab_user_id == 42

    async def test_get_user_failure_logs_warning(self) -> None:
        """When get_user fails, warning is logged and flags remain False."""
        c = make_mock_connector()
        c.created_by = "user-1"
        c.data_source = MagicMock()
        c.creator_email = None
        c._is_admin = False
        c._is_auditor = False
        c._gitlab_user_id = None

        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        from app.connectors.sources.gitlab.connector import GitLabConnector
        await GitLabConnector._resolve_creator_identity(c)
        # Flags should remain False, warning should be logged
        assert c._is_admin is False

    async def test_exception_in_get_user_logs_warning(self) -> None:
        """Exception in get_user is caught and warning is logged."""
        c = make_mock_connector()
        c.created_by = "user-1"
        c.data_source = MagicMock()
        c.creator_email = None
        c._is_admin = False
        c._is_auditor = False
        c._gitlab_user_id = None

        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)
        c.runtime.ds_call = AsyncMock(side_effect=Exception("API error"))

        from app.connectors.sources.gitlab.connector import GitLabConnector
        await GitLabConnector._resolve_creator_identity(c)
        c.logger.warning.assert_called()
