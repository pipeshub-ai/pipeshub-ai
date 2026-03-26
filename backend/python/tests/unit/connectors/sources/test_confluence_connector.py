"""Tests for app.connectors.sources.atlassian.confluence_cloud.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Optional

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    CONTENT_EXPAND_PARAMS,
    PSEUDO_USER_GROUP_PREFIX,
    TIME_OFFSET_HOURS,
    ConfluenceConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.confluence")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-conf-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-conf-1")


def _make_mock_response(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


# ===========================================================================
# Constants
# ===========================================================================


class TestConfluenceConstants:

    def test_time_offset_hours(self):
        assert TIME_OFFSET_HOURS == 24

    def test_content_expand_params(self):
        assert "ancestors" in CONTENT_EXPAND_PARAMS
        assert "history.lastUpdated" in CONTENT_EXPAND_PARAMS
        assert "space" in CONTENT_EXPAND_PARAMS

    def test_pseudo_user_group_prefix(self):
        assert PSEUDO_USER_GROUP_PREFIX == "[Pseudo-User]"


# ===========================================================================
# ConfluenceConnector.__init__
# ===========================================================================


class TestConfluenceConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_id == "conn-conf-1"
        assert connector.external_client is None
        assert connector.data_source is None

    def test_sync_points_initialized(self):
        connector = _make_connector()
        assert connector.pages_sync_point is not None
        assert connector.audit_log_sync_point is not None


# ===========================================================================
# ConfluenceConnector.init
# ===========================================================================


class TestConfluenceConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient") as mock_client:
            mock_client.build_from_services = AsyncMock(return_value=MagicMock())
            result = await connector.init()

        assert result is True
        assert connector.external_client is not None
        assert connector.data_source is not None

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient") as mock_client:
            mock_client.build_from_services = AsyncMock(side_effect=Exception("Auth failed"))
            result = await connector.init()

        assert result is False


# ===========================================================================
# ConfluenceConnector._get_fresh_datasource
# ===========================================================================


class TestGetFreshDatasource:

    @pytest.mark.asyncio
    async def test_raises_when_client_not_initialized(self):
        connector = _make_connector()
        connector.external_client = None

        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_api_token_returns_existing_datasource(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource") as mock_ds:
            result = await connector._get_fresh_datasource()
            mock_ds.assert_called_once_with(connector.external_client)

    @pytest.mark.asyncio
    async def test_oauth_updates_token_when_changed(self):
        connector = _make_connector()
        mock_internal_client = MagicMock()
        mock_internal_client.get_token = MagicMock(return_value="old-token")
        mock_internal_client.set_token = MagicMock()

        mock_ext_client = MagicMock()
        mock_ext_client.get_client = MagicMock(return_value=mock_internal_client)
        connector.external_client = mock_ext_client

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"):
            await connector._get_fresh_datasource()

        mock_internal_client.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_oauth_skips_update_when_token_unchanged(self):
        connector = _make_connector()
        mock_internal_client = MagicMock()
        mock_internal_client.get_token = MagicMock(return_value="same-token")
        mock_internal_client.set_token = MagicMock()

        mock_ext_client = MagicMock()
        mock_ext_client.get_client = MagicMock(return_value=mock_internal_client)
        connector.external_client = mock_ext_client

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "same-token"},
        })

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"):
            await connector._get_fresh_datasource()

        mock_internal_client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_raises_when_no_token(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })

        with pytest.raises(Exception, match="No OAuth access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_raises_when_no_config(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()


# ===========================================================================
# ConfluenceConnector.test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_returns_false_when_client_not_initialized(self):
        connector = _make_connector()
        connector.external_client = None

        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        connector = _make_connector()
        connector.external_client = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_make_mock_response(200, {"results": []}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self):
        connector = _make_connector()
        connector.external_client = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_make_mock_response(401, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("Network error"))

        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# ConfluenceConnector._sync_users
# ===========================================================================


class TestSyncUsers:

    @pytest.mark.asyncio
    async def test_sync_users_single_page(self):
        connector = _make_connector()
        mock_ds = MagicMock()

        user_response = {
            "results": [
                {
                    "user": {
                        "accountId": "user-1",
                        "publicName": "User One",
                        "displayName": "User One",
                    },
                    "email": "user1@example.com",
                }
            ],
        }
        mock_ds.search_users = AsyncMock(return_value=_make_mock_response(200, user_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_app_user = MagicMock(return_value=AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
            source_user_id="user-1",
            email="user1@example.com",
            full_name="User One",
        ))

        await connector._sync_users()

        connector.data_entities_processor.on_new_app_users.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_skips_without_email(self):
        connector = _make_connector()
        mock_ds = MagicMock()

        user_response = {
            "results": [
                {
                    "user": {"accountId": "user-no-email", "displayName": "No Email"},
                    "email": "",
                }
            ],
        }
        mock_ds.search_users = AsyncMock(return_value=_make_mock_response(200, user_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()

        # on_new_app_users should not be called since no users with email
        connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_handles_api_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_users = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Should not raise, just stop
        await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_migrates_pseudo_group_permissions(self):
        """When user has email with @, migrates pseudo-group permissions."""
        connector = _make_connector()
        mock_ds = MagicMock()

        user_response = {
            "results": [
                {
                    "user": {"accountId": "user-1", "displayName": "Alice"},
                    "email": "alice@example.com",
                }
            ],
        }
        mock_ds.search_users = AsyncMock(return_value=_make_mock_response(200, user_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_app_user = MagicMock(return_value=AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
            source_user_id="user-1",
            email="alice@example.com",
            full_name="Alice",
        ))

        await connector._sync_users()

        connector.data_entities_processor.migrate_group_to_user_by_external_id.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_users_pagination(self):
        """Tests pagination by returning exactly batch_size on first call."""
        connector = _make_connector()
        mock_ds = MagicMock()

        # First page: 100 users (exact batch_size triggers next page)
        page1_users = [
            {"user": {"accountId": f"u{i}", "displayName": f"User {i}"}, "email": f"u{i}@example.com"}
            for i in range(100)
        ]
        # Second page: less than 100 (stops)
        page2_users = [
            {"user": {"accountId": "u100", "displayName": "User 100"}, "email": "u100@example.com"}
        ]

        call_count = 0

        async def mock_search_users(cql=None, start=0, limit=100):
            nonlocal call_count
            call_count += 1
            if start == 0:
                return _make_mock_response(200, {"results": page1_users})
            else:
                return _make_mock_response(200, {"results": page2_users})

        mock_ds.search_users = AsyncMock(side_effect=mock_search_users)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_app_user = MagicMock(return_value=AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
            source_user_id="u1",
            email="u1@example.com",
            full_name="User",
        ))

        await connector._sync_users()
        assert call_count == 2


# ===========================================================================
# ConfluenceConnector._sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_sync_groups_success(self):
        connector = _make_connector()
        mock_ds = MagicMock()

        groups_response = {
            "results": [
                {"id": "grp-1", "name": "confluence-users"},
            ],
            "size": 1,
        }
        mock_ds.get_groups = AsyncMock(return_value=_make_mock_response(200, groups_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_group_members = AsyncMock(return_value=["user1@example.com"])
        connector._transform_to_user_group = MagicMock(return_value=AppUserGroup(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
            source_user_group_id="grp-1",
            name="confluence-users",
        ))
        connector._get_app_users_by_emails = AsyncMock(return_value=[])

        await connector._sync_user_groups()

        connector.data_entities_processor.on_new_user_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_groups_handles_api_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_groups = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Should not raise
        await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_groups_skips_without_id(self):
        """Groups without id or name are skipped."""
        connector = _make_connector()
        mock_ds = MagicMock()
        groups_response = {
            "results": [
                {"id": None, "name": ""},
                {"id": "grp-2", "name": "valid-group"},
            ],
            "size": 2,
        }
        mock_ds.get_groups = AsyncMock(return_value=_make_mock_response(200, groups_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_group_members = AsyncMock(return_value=[])
        connector._transform_to_user_group = MagicMock(return_value=AppUserGroup(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
            source_user_group_id="grp-2",
            name="valid-group",
        ))
        connector._get_app_users_by_emails = AsyncMock(return_value=[])

        await connector._sync_user_groups()
        # Only one group should be processed
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()


# ===========================================================================
# ConfluenceConnector._sync_spaces
# ===========================================================================


class TestSyncSpaces:

    @pytest.mark.asyncio
    async def test_sync_spaces_success(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        mock_ds = MagicMock()
        spaces_response = {
            "results": [
                {"id": "space-1", "key": "ENG", "name": "Engineering", "type": "global"},
            ],
            "_links": {"base": "https://company.atlassian.net/wiki"},
        }
        mock_ds.get_spaces = AsyncMock(return_value=_make_mock_response(200, spaces_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_space_permissions = AsyncMock(return_value=[])
        connector._transform_to_space_record_group = MagicMock(return_value=RecordGroup(
            external_group_id="space-1",
            name="Engineering",
            short_name="ENG",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
        ))

        spaces = await connector._sync_spaces()
        assert len(spaces) == 1
        assert spaces[0].name == "Engineering"

        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_spaces_handles_api_failure(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        spaces = await connector._sync_spaces()
        assert spaces == []

    @pytest.mark.asyncio
    async def test_sync_spaces_with_exclusion_filter(self):
        """Tests NOT_IN space filter (client-side exclusion)."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection, FilterOperator
        connector.sync_filters = MagicMock()
        connector.indexing_filters = FilterCollection()

        # Set up space_keys filter with NOT_IN
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.NOT_IN
        space_filter.get_value.return_value = ["PRIVATE"]

        from app.connectors.core.registry.filters import SyncFilterKey
        connector.sync_filters.get = MagicMock(side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None)

        mock_ds = MagicMock()
        spaces_response = {
            "results": [
                {"id": "1", "key": "ENG", "name": "Engineering"},
                {"id": "2", "key": "PRIVATE", "name": "Private Space"},
            ],
            "_links": {"base": "https://company.atlassian.net/wiki"},
        }
        mock_ds.get_spaces = AsyncMock(return_value=_make_mock_response(200, spaces_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._fetch_space_permissions = AsyncMock(return_value=[])
        connector._transform_to_space_record_group = MagicMock(return_value=RecordGroup(
            external_group_id="1",
            name="Engineering",
            short_name="ENG",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE,
            connector_id="conn-conf-1",
        ))

        spaces = await connector._sync_spaces()
        # PRIVATE should be excluded
        assert len(spaces) == 1


# ===========================================================================
# ConfluenceConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.external_client = None
        connector.data_source = None

        with pytest.raises(Exception, match="not initialized"):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_calls_all_steps(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            mock_space = MagicMock()
            mock_space.short_name = "ENG"
            mock_space.name = "Engineering"

            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_spaces = AsyncMock(return_value=[mock_space])
            connector._sync_content = AsyncMock()
            connector._sync_permission_changes_from_audit_log = AsyncMock()

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_spaces.assert_awaited_once()
            # Two calls to _sync_content: one for pages, one for blogposts
            assert connector._sync_content.await_count == 2


# ===========================================================================
# ConfluenceConnector._extract_cursor_from_next_link
# ===========================================================================


class TestExtractCursorFromNextLink:

    def test_extracts_cursor(self):
        connector = _make_connector()
        next_link = "/wiki/api/v2/spaces?cursor=abc123&limit=20"
        result = connector._extract_cursor_from_next_link(next_link)
        assert result == "abc123"

    def test_returns_none_when_no_cursor(self):
        connector = _make_connector()
        next_link = "/wiki/api/v2/spaces?limit=20"
        result = connector._extract_cursor_from_next_link(next_link)
        assert result is None

    def test_returns_none_for_empty(self):
        connector = _make_connector()
        result = connector._extract_cursor_from_next_link("")
        assert result is None


# ===========================================================================
# ConfluenceConnector._sync_permission_changes_from_audit_log
# ===========================================================================


class TestSyncPermissionChangesFromAuditLog:

    @pytest.mark.asyncio
    async def test_first_run_initializes_checkpoint(self):
        """First run (no sync point) initializes checkpoint and skips."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.audit_log_sync_point.update_sync_point = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited_once()
        # Verify checkpoint was set (not None)
        call_args = connector.audit_log_sync_point.update_sync_point.call_args[0]
        assert "last_sync_time_ms" in call_args[1]

    @pytest.mark.asyncio
    async def test_subsequent_run_no_changes(self):
        """Subsequent run with no permission changes still updates checkpoint."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=[])

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_subsequent_run_with_changes(self):
        """Subsequent run finds permission changes and syncs them."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=["Page Title"])
        connector._sync_content_permissions_by_titles = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector._sync_content_permissions_by_titles.assert_awaited_once_with(["Page Title"])
        connector.audit_log_sync_point.update_sync_point.assert_awaited()


# ===========================================================================
# ConfluenceConnector._extract_content_title_from_audit_record
# ===========================================================================


class TestExtractContentTitleFromAuditRecord:

    def test_permission_change_with_page_and_space(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Page", "name": "My Page"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result == "My Page"

    def test_permission_change_with_blog_and_space(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Blog", "name": "My Blog"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result == "My Blog"

    def test_non_permission_category_returns_none(self):
        connector = _make_connector()
        record = {
            "category": "Security",
            "associatedObjects": [
                {"objectType": "Page", "name": "Test"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_space_returns_none(self):
        """Permission change without Space is a global change, not content-level."""
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Page", "name": "Test"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_content_returns_none(self):
        """Permission change with Space but no Page/Blog is space-level."""
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None


# ===========================================================================
# ConfluenceConnector._fetch_permission_audit_logs
# ===========================================================================


class TestFetchPermissionAuditLogs:

    @pytest.mark.asyncio
    async def test_fetches_and_extracts_titles(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_audit_logs = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {
                    "category": "Permissions",
                    "associatedObjects": [
                        {"objectType": "Page", "name": "Restricted Page"},
                        {"objectType": "Space", "name": "ENG"},
                    ],
                },
                {
                    "category": "Security",
                    "associatedObjects": [],
                },
            ],
            "size": 2,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        titles = await connector._fetch_permission_audit_logs(1000, 2000)
        assert "Restricted Page" in titles

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_audit_logs = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        titles = await connector._fetch_permission_audit_logs(1000, 2000)
        assert titles == []


# ===========================================================================
# ConfluenceConnector._fetch_space_permissions
# ===========================================================================


class TestFetchSpacePermissions:

    @pytest.mark.asyncio
    async def test_fetches_permissions(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_space_permissions_assignments = AsyncMock(return_value=_make_mock_response(200, {
            "results": [{"id": "perm-1"}],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_space_permission = AsyncMock(return_value=Permission(
            entity_type=EntityType.USER,
            type=PermissionType.READ,
            email="user@example.com",
        ))

        permissions = await connector._fetch_space_permissions("space-1", "Engineering")
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_space_permissions_assignments = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_space_permissions("space-1", "Engineering")
        assert permissions == []


# ===========================================================================
# ConfluenceConnector._fetch_page_permissions
# ===========================================================================


class TestFetchPagePermissions:

    @pytest.mark.asyncio
    async def test_fetches_page_permissions(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_permissions_v1 = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"operation": "read", "restrictions": {"user": {"results": []}, "group": {"results": []}}},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_page_restriction_to_permissions = AsyncMock(return_value=[])

        permissions = await connector._fetch_page_permissions("page-1")
        assert permissions == []
        connector._transform_page_restriction_to_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_permissions_v1 = AsyncMock(return_value=_make_mock_response(403, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_page_permissions("page-1")
        assert permissions == []


# ===========================================================================
# ConfluenceConnector._sync_content_permissions_by_titles
# ===========================================================================


class TestSyncContentPermissionsByTitles:

    @pytest.mark.asyncio
    async def test_empty_titles_no_op(self):
        connector = _make_connector()
        await connector._sync_content_permissions_by_titles([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_items_not_in_db(self):
        """Items not in DB are skipped (respects sync filters)."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_content_by_titles = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "page-1", "title": "Test Page", "type": "page"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        # Record not found in DB
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        await connector._sync_content_permissions_by_titles(["Test Page"])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_records_permissions(self):
        """Items found in DB have their permissions refreshed."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_content_by_titles = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "page-1", "title": "Test Page", "type": "page"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Record exists in DB
        existing = MagicMock()
        existing.id = "existing-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        mock_record = MagicMock()
        mock_record.inherit_permissions = True
        connector._transform_to_webpage_record = MagicMock(return_value=mock_record)
        connector._fetch_page_permissions = AsyncMock(return_value=[
            Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="alice@example.com")
        ])

        await connector._sync_content_permissions_by_titles(["Test Page"])
        connector.data_entities_processor.on_new_records.assert_awaited()


# ===========================================================================
# ConfluenceConnector._fetch_comments_recursive
# ===========================================================================


class TestFetchCommentsRecursive:

    @pytest.mark.asyncio
    async def test_fetches_page_footer_comments(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_footer_comments = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "comment-1", "body": {"storage": {"value": "Hello"}}, "version": {"number": 1}},
            ],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_comment_record = MagicMock(return_value=MagicMock())
        connector._fetch_comment_children_recursive = AsyncMock(return_value=[])

        comments = await connector._fetch_comments_recursive(
            "12345", "Test Page", "footer", [], "space-1", "page"
        )
        assert len(comments) == 1

    @pytest.mark.asyncio
    async def test_fetches_blogpost_inline_comments(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_blog_post_inline_comments = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "comment-1", "body": {"storage": {"value": "Inline comment"}}},
            ],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_comment_record = MagicMock(return_value=MagicMock())
        connector._fetch_comment_children_recursive = AsyncMock(return_value=[])

        comments = await connector._fetch_comments_recursive(
            "67890", "Test Blog", "inline", [], "space-1", "blogpost"
        )
        assert len(comments) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_footer_comments = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        comments = await connector._fetch_comments_recursive(
            "12345", "Test", "footer", [], None, "page"
        )
        assert comments == []


# ===========================================================================
# ConfluenceConnector._fetch_comment_children_recursive
# ===========================================================================


class TestFetchCommentChildrenRecursive:

    @pytest.mark.asyncio
    async def test_fetches_footer_comment_children(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_children = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "child-1", "body": {"storage": {"value": "Reply"}}},
            ],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_comment_record = MagicMock(return_value=MagicMock())

        children = await connector._fetch_comment_children_recursive(
            "11111", "footer", "12345", "space-1", []
        )
        assert len(children) == 1

    @pytest.mark.asyncio
    async def test_fetches_inline_comment_children(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_inline_comment_children = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "child-1"},
            ],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_to_comment_record = MagicMock(return_value=MagicMock())

        children = await connector._fetch_comment_children_recursive(
            "11111", "inline", "12345", "space-1", []
        )
        assert len(children) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_children = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        children = await connector._fetch_comment_children_recursive(
            "11111", "footer", "12345", "space-1", []
        )
        assert children == []
