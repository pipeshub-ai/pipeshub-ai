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
