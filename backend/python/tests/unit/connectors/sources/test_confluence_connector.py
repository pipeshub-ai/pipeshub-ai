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
import uuid
from fastapi import HTTPException
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.connectors.core.registry.filters import FilterCollection, FilterOperator, SyncFilterKey
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    PSEUDO_USER_GROUP_PREFIX,
    ConfluenceConnector,
)


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
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-conf-1", "team", "test-user")


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
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
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
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
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
            "auth": {"authType": "OAUTH", "baseUrl": "https://acme.atlassian.net"},
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

# =============================================================================
# Merged from test_confluence_connector_coverage.py
# =============================================================================

# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps_cov():
    logger = logging.getLogger("test.confluence.coverage")
    dep = MagicMock()
    dep.org_id = "org-cov-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.migrate_group_to_user_by_external_id = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _conn():
    logger, dep, dsp, cs = _make_mock_deps_cov()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-cov-1", "team", "test-user-id")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


# ===========================================================================
# _parse_confluence_datetime
# ===========================================================================


class TestParseConfluenceDatetime:
    def test_valid_z_suffix(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(ts, int)
        assert ts > 0

    def test_valid_offset(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526+00:00")
        assert isinstance(ts, int)
        assert ts > 0

    def test_invalid_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("not-a-date")
        assert ts is None

    def test_empty_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("")
        assert ts is None


# ===========================================================================
# _transform_to_app_user
# ===========================================================================


class TestTransformToAppUser:
    def test_creates_app_user(self):
        c = _conn()
        user_data = {
            "accountId": "acc-1",
            "email": "alice@example.com",
            "displayName": "Alice Smith",
            "lastModified": "2025-01-01T00:00:00.000Z",
        }
        result = c._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "alice@example.com"
        assert result.full_name == "Alice Smith"
        assert result.source_user_id == "acc-1"

    def test_missing_account_id(self):
        c = _conn()
        result = c._transform_to_app_user({"email": "bob@example.com"})
        assert result is None

    def test_missing_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-2"})
        assert result is None

    def test_whitespace_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-3", "email": "  "})
        assert result is None

    def test_no_last_modified(self):
        c = _conn()
        result = c._transform_to_app_user({
            "accountId": "acc-4", "email": "test@test.com",
            "displayName": "Test User"
        })
        assert result is not None
        assert result.source_updated_at is None


# ===========================================================================
# _transform_to_user_group
# ===========================================================================


class TestTransformToUserGroup:
    def test_creates_group(self):
        c = _conn()
        result = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert result is not None
        assert result.name == "devs"
        assert result.source_user_group_id == "g1"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_user_group({"id": "g1"}) is None

    def test_both_missing(self):
        c = _conn()
        assert c._transform_to_user_group({}) is None


# ===========================================================================
# _transform_to_space_record_group
# ===========================================================================


class TestTransformToSpaceRecordGroup:
    def test_full_space(self):
        c = _conn()
        data = {
            "id": "100",
            "name": "Engineering",
            "key": "ENG",
            "description": "Eng space",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "_links": {"webui": "/spaces/ENG"},
        }
        result = c._transform_to_space_record_group(data, "https://wiki.example.com")
        assert result is not None
        assert result.name == "Engineering"
        assert result.short_name == "ENG"
        assert result.web_url == "https://wiki.example.com/spaces/ENG"

    def test_no_base_url(self):
        c = _conn()
        data = {"id": "101", "name": "Test", "key": "TST"}
        result = c._transform_to_space_record_group(data, None)
        assert result is not None
        assert result.web_url is None

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_space_record_group({"name": "T"}, None) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_space_record_group({"id": "1"}, None) is None


# ===========================================================================
# _map_confluence_permission
# ===========================================================================


class TestMapConfluencePermission:
    def test_administer(self):
        c = _conn()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _conn()
        assert c._map_confluence_permission("read", "page") == PermissionType.READ

    def test_delete_space(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_comment(self):
        c = _conn()
        assert c._map_confluence_permission("create", "comment") == PermissionType.COMMENT

    def test_delete_comment(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "comment") == PermissionType.COMMENT

    def test_create_page(self):
        c = _conn()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_delete_blogpost(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "blogpost") == PermissionType.WRITE

    def test_archive_attachment(self):
        c = _conn()
        assert c._map_confluence_permission("archive", "attachment") == PermissionType.WRITE

    def test_restrict_content(self):
        c = _conn()
        assert c._map_confluence_permission("restrict_content", "space") == PermissionType.OTHER

    def test_export(self):
        c = _conn()
        assert c._map_confluence_permission("export", "space") == PermissionType.OTHER


# ===========================================================================
# _map_page_permission
# ===========================================================================


class TestMapPagePermission:
    def test_read(self):
        c = _conn()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _conn()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _conn()
        assert c._map_page_permission("delete") == PermissionType.OTHER


# ===========================================================================
# _construct_web_url
# ===========================================================================


class TestConstructWebUrl:
    def test_v2_with_base_url(self):
        c = _conn()
        links = {"webui": "/spaces/ENG/pages/123"}
        url = c._construct_web_url(links, "https://wiki.example.com")
        assert url == "https://wiki.example.com/spaces/ENG/pages/123"

    def test_v1_with_self_link(self):
        c = _conn()
        links = {
            "webui": "/spaces/ENG/pages/123",
            "self": "https://company.atlassian.net/wiki/rest/api/content/123",
        }
        url = c._construct_web_url(links, None)
        assert url == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_no_webui(self):
        c = _conn()
        url = c._construct_web_url({}, "https://base.com")
        assert url is None

    def test_no_base_no_self(self):
        c = _conn()
        links = {"webui": "/some/path"}
        url = c._construct_web_url(links, None)
        assert url is None

    def test_self_link_without_wiki(self):
        c = _conn()
        links = {"webui": "/path", "self": "https://other.com/api/content/1"}
        url = c._construct_web_url(links, None)
        assert url is None


# ===========================================================================
# _transform_to_webpage_record
# ===========================================================================


class TestTransformToWebpageRecord:
    def _page_data(self, **overrides):
        data = {
            "id": "page-1",
            "title": "Test Page",
            "space": {"id": 100},
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 5},
            },
            "ancestors": [{"id": "parent-1"}],
            "_links": {
                "webui": "/spaces/ENG/pages/1",
                "self": "https://company.atlassian.net/wiki/rest/api/content/1",
            },
        }
        data.update(overrides)
        return data

    def test_v1_format(self):
        c = _conn()
        result = c._transform_to_webpage_record(
            self._page_data(), RecordType.CONFLUENCE_PAGE
        )
        assert result is not None
        assert result.record_name == "Test Page"
        assert result.external_record_id == "page-1"
        assert result.parent_external_record_id == "parent-1"
        assert result.external_revision_id == "5"
        assert result.version == 0

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "page-v2",
            "title": "V2 Page",
            "spaceId": 200,
            "parentId": "parent-v2",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 3},
            "_links": {"webui": "/p", "base": "https://wiki.com"},
        }
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert result is not None
        assert result.external_record_group_id == "200"
        assert result.parent_external_record_id == "parent-v2"
        assert result.record_type == RecordType.CONFLUENCE_BLOGPOST

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_webpage_record({"title": "t"}, RecordType.CONFLUENCE_PAGE) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_webpage_record({"id": "1"}, RecordType.CONFLUENCE_PAGE) is None

    def test_no_space(self):
        c = _conn()
        data = {"id": "1", "title": "T"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = self._page_data()  # version=5
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result is not None
        assert result.id == "existing-id"
        assert result.version == 3  # bumped from 2

    def test_existing_record_no_version_change(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "5"  # same as data
        data = self._page_data()
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result.version == 2  # unchanged

    def test_no_ancestors_no_parent(self):
        c = _conn()
        data = self._page_data()
        data.pop("ancestors", None)
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert result is not None
        assert result.parent_external_record_id is None


# ===========================================================================
# _transform_to_attachment_file_record
# ===========================================================================


class TestTransformToAttachmentFileRecord:
    def _attachment_data(self, **overrides):
        data = {
            "id": "att-1",
            "title": "report.pdf",
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 2},
            },
            "extensions": {"fileSize": 12345, "mediaType": "application/pdf"},
            "_links": {
                "webui": "/att/report.pdf",
                "self": "https://company.atlassian.net/wiki/rest/api/content/att-1",
            },
        }
        data.update(overrides)
        return data

    def test_basic_attachment(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "parent-1", "space-1"
        )
        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.extension == "pdf"
        assert result.size_in_bytes == 12345
        assert result.is_file is True
        assert result.is_dependent_node is True

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "att-v2",
            "title": "image.png",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 1},
            "fileSize": 5000,
            "mediaType": "image/png",
            "_links": {"webui": "/att", "base": "https://wiki.com"},
        }
        result = c._transform_to_attachment_file_record(data, "p1", "s1")
        assert result is not None
        assert result.extension == "png"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"title": "file.txt"}, "p", "s"
        ) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"id": "att-1"}, "p", "s"
        ) is None

    def test_filename_with_query_params(self):
        c = _conn()
        data = self._attachment_data(title="file.pdf?version=1")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.record_name == "file.pdf"

    def test_no_extension(self):
        c = _conn()
        data = self._attachment_data(title="README")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.extension is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "ex-att"
        existing.version = 1
        existing.external_revision_id = "1"  # old version
        data = self._attachment_data()  # version=2
        result = c._transform_to_attachment_file_record(
            data, "p", "s", existing_record=existing
        )
        assert result.id == "ex-att"
        assert result.version == 2

    def test_parent_node_id(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "p1", "s1", parent_node_id="node-123"
        )
        assert result.parent_node_id == "node-123"

    def test_mime_type_from_metadata(self):
        c = _conn()
        data = {
            "id": "att-m",
            "title": "file.bin",
            "metadata": {"mediaType": "application/octet-stream"},
            "_links": {},
        }
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result is not None


# ===========================================================================
# _transform_to_comment_record
# ===========================================================================


class TestTransformToCommentRecord:
    def _comment_data(self, **overrides):
        data = {
            "id": "cmt-1",
            "title": "A comment",
            "version": {"authorId": "user-1", "createdAt": "2025-01-01T00:00:00.000Z", "number": 1},
            "_links": {"webui": "/comment", "self": "https://company.atlassian.net/wiki/rest/api/content/cmt-1"},
        }
        data.update(overrides)
        return data

    def test_footer_comment(self):
        c = _conn()
        result = c._transform_to_comment_record(
            self._comment_data(), "page-1", "space-1", "footer", None
        )
        assert result is not None
        assert result.record_type == RecordType.COMMENT

    def test_inline_comment(self):
        c = _conn()
        data = self._comment_data()
        data["resolutionStatus"] = True
        data["properties"] = {"inlineOriginalSelection": "some text"}
        result = c._transform_to_comment_record(
            data, "page-1", "space-1", "inline", None
        )
        assert result is not None
        assert result.record_type == RecordType.INLINE_COMMENT
        assert result.resolution_status == "resolved"
        assert result.comment_selection == "some text"

    def test_inline_unresolved(self):
        c = _conn()
        data = self._comment_data()
        data["resolutionStatus"] = False
        result = c._transform_to_comment_record(
            data, "page-1", "space-1", "inline", None
        )
        assert result.resolution_status == "open"

    def test_reply_comment(self):
        c = _conn()
        result = c._transform_to_comment_record(
            self._comment_data(), "page-1", "space-1", "footer", "parent-cmt"
        )
        assert result.parent_external_record_id == "parent-cmt"
        assert result.parent_record_type == RecordType.COMMENT

    def test_missing_id(self):
        c = _conn()
        data = self._comment_data()
        data["id"] = None
        assert c._transform_to_comment_record(data, "p", "s", "footer", None) is None

    def test_missing_author(self):
        c = _conn()
        data = self._comment_data()
        data["version"] = {"number": 1}
        assert c._transform_to_comment_record(data, "p", "s", "footer", None) is None

    def test_with_base_url(self):
        c = _conn()
        data = self._comment_data()
        result = c._transform_to_comment_record(
            data, "page-1", "space-1", "footer", None, base_url="https://wiki.com"
        )
        assert result.weburl == "https://wiki.com/comment"

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "ex-cmt"
        existing.version = 0
        existing.external_revision_id = "0"  # different from 1
        result = c._transform_to_comment_record(
            self._comment_data(), "p", "s", "footer", None, existing_record=existing
        )
        assert result.id == "ex-cmt"
        assert result.version == 1

    def test_existing_record_no_change(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "ex-cmt"
        existing.version = 3
        existing.external_revision_id = "1"  # same version
        result = c._transform_to_comment_record(
            self._comment_data(), "p", "s", "footer", None, existing_record=existing
        )
        assert result.version == 3


# ===========================================================================
# _create_permission_from_principal
# ===========================================================================


class TestCreatePermissionFromPrincipal:
    @pytest.mark.asyncio
    async def test_user_found(self):
        c = _conn()
        mock_user = MagicMock()
        mock_user.email = "alice@test.com"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=mock_user)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "alice@test.com"
        assert perm.entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=False
        )
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_create_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        pseudo_group = MagicMock()
        pseudo_group.source_user_group_id = "acc-1"
        c._create_pseudo_group = AsyncMock(return_value=pseudo_group)

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_user_not_found_existing_pseudo(self):
        c = _conn()
        pseudo = MagicMock()
        pseudo.source_user_group_id = "acc-1"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=pseudo)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        c = _conn()
        mock_group = MagicMock()
        mock_group.source_user_group_id = "grp-1"
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=mock_group)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP
        assert perm.external_id == "grp-1"

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-x", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_principal_type(self):
        c = _conn()
        perm = await c._create_permission_from_principal("role", "r1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_store_provider.transaction.side_effect = Exception("DB error")
        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is None


# ===========================================================================
# _create_pseudo_group
# ===========================================================================


class TestCreatePseudoGroup:
    @pytest.mark.asyncio
    async def test_creates_and_saves(self):
        c = _conn()
        result = await c._create_pseudo_group("acc-no-email")
        assert result is not None
        assert result.name == f"{PSEUDO_USER_GROUP_PREFIX} acc-no-email"
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("fail"))
        result = await c._create_pseudo_group("acc-fail")
        assert result is None


# ===========================================================================
# _transform_space_permission
# ===========================================================================


class TestTransformSpacePermission:
    @pytest.mark.asyncio
    async def test_valid_permission(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        perm_data = {
            "principal": {"type": "user", "id": "acc-1"},
            "operation": {"key": "read", "targetType": "space"},
        }
        result = await c._transform_space_permission(perm_data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_principal(self):
        c = _conn()
        result = await c._transform_space_permission({"operation": {"key": "read"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_operation(self):
        c = _conn()
        result = await c._transform_space_permission(
            {"principal": {"type": "user", "id": "1"}}
        )
        assert result is None


# ===========================================================================
# _transform_page_restriction_to_permissions
# ===========================================================================


class TestTransformPageRestrictionToPermissions:
    @pytest.mark.asyncio
    async def test_processes_users_and_groups(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        restriction = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "acc-1"}]},
                "group": {"results": [{"id": "grp-1"}]},
            },
        }
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _conn()
        perms = await c._transform_page_restriction_to_permissions({})
        assert perms == []

    @pytest.mark.asyncio
    async def test_empty_restrictions(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(return_value=None)
        restriction = {"operation": "read", "restrictions": {}}
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert perms == []


# ===========================================================================
# _process_webpage_with_update
# ===========================================================================


class TestProcessWebpageWithUpdate:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _conn()
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.is_new is True
        assert result.record is mock_record

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = None
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 2},  # changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.is_new is False
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.record is None

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "ancestors": [{"id": "new-parent"}],  # parent changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.metadata_changed is True


# ===========================================================================
# _fetch_group_members
# ===========================================================================


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [
                {"email": "a@t.com", "displayName": "A"},
                {"email": "", "displayName": "NoEmail"},
            ],
            "size": 2,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "Group 1")
        assert emails == ["a@t.com"]

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "G")
        assert emails == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        emails = await c._fetch_group_members("g1", "G")
        assert emails == []


# ===========================================================================
# _get_app_users_by_emails
# ===========================================================================


class TestGetAppUsersByEmails:
    @pytest.mark.asyncio
    async def test_empty_emails(self):
        c = _conn()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _conn()
        u1 = MagicMock()
        u1.email = "a@t.com"
        u2 = MagicMock()
        u2.email = "b@t.com"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1
        assert result[0].email == "a@t.com"

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert result == []


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _conn()
        result = await c.get_signed_url(MagicMock())
        assert result == ""


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _conn()
        await c.cleanup()  # should not raise


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _conn()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_no_op(self):
        c = _conn()
        await c.handle_webhook_notification({})


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _conn()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _conn()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _conn()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        with pytest.raises(ValueError, match="Unsupported"):
            await c.get_filter_options("unknown_key")


# ===========================================================================
# _get_space_options
# ===========================================================================


class TestGetSpaceOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].id == "ENG"

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "HR", "name": "Human Resources"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "HR", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_next_cursor(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "A", "name": "A"}],
            "_links": {"next": "/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "abc"

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_space_options(1, 20, None, None)


# ===========================================================================
# _get_page_options / _get_blogpost_options
# ===========================================================================


class TestGetPageOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "Match", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "Match", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_page_options(1, 20, None, None)


class TestGetBlogpostOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Blog 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Hit", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Hit", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_blogpost_options(1, 20, None, None)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "Test"
        c._fetch_page_content = AsyncMock(return_value="<p>Hello</p>")
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_comment(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.external_record_id = "c1"
        record.record_name = "Comment"
        c._fetch_comment_content = AsyncMock(return_value="<p>Comment</p>")
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_inline_comment(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.INLINE_COMMENT
        record.external_record_id = "ic1"
        record.record_name = "Inline"
        c._fetch_comment_content = AsyncMock(return_value="<p>Inline</p>")
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        async def mock_fetch(r):
            yield b"data"

        c._fetch_attachment_content = mock_fetch
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.create_stream_record_response") as mock_create:
            mock_create.return_value = MagicMock()
            result = await c.stream_record(record)
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_exception(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "T"
        c._fetch_page_content = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# _fetch_page_content
# ===========================================================================


class TestFetchPageContent:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<h1>Content</h1>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert result == "<h1>Content</h1>"

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert result == "<p>Blog</p>"

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _conn()
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.TICKET)
        assert exc_info.value.status_code == 400


# ===========================================================================
# _fetch_comment_content
# ===========================================================================


class TestFetchCommentContent:
    @pytest.mark.asyncio
    async def test_footer_comment(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_by_id = AsyncMock(return_value=_resp(200, {
            "body": {"storage": {"value": "<p>Footer</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.external_record_id = "123"
        result = await c._fetch_comment_content(record)
        assert result == "<p>Footer</p>"

    @pytest.mark.asyncio
    async def test_inline_comment(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_inline_comment_by_id = AsyncMock(return_value=_resp(200, {
            "body": {"storage": {"value": "<p>Inline</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.INLINE_COMMENT
        record.external_record_id = "456"
        result = await c._fetch_comment_content(record)
        assert result == "<p>Inline</p>"

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_by_id = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.external_record_id = "789"
        result = await c._fetch_comment_content(record)
        assert "No content" in result

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_by_id = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.external_record_id = "101"
        with pytest.raises(HTTPException):
            await c._fetch_comment_content(record)

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _conn()
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "102"
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_comment_content(record)
        assert exc_info.value.status_code == 400


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _conn()
        await c.reindex_records([])
        c.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _conn()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        r1 = MagicMock()
        r1.id = "r1"
        r2 = MagicMock()
        r2.id = "r2"

        mock_updated = (MagicMock(), [Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="x@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[mock_updated, None])

        await c.reindex_records([r1, r2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.on_updated_record_permissions.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_exception_skips(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))
        await c.reindex_records([MagicMock()])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_BLOGPOST
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_comment(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        c._check_and_fetch_updated_comment = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.id = "r1"
        c._check_and_fetch_updated_page = AsyncMock(side_effect=Exception("fail"))
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_page
# ===========================================================================


class TestCheckAndFetchUpdatedPage:
    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_change(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 5},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 6}, "space": {"id": 1}, "id": "p1", "title": "T",
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_wr = MagicMock()
        mock_wr.inherit_permissions = True
        c._transform_to_webpage_record = MagicMock(return_value=mock_wr)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is not None


# ===========================================================================
# _check_and_fetch_updated_attachment
# ===========================================================================


class TestCheckAndFetchUpdatedAttachment:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = None
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(200, {
            "version": {"number": 2},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        record.external_revision_id = "2"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None


# ===========================================================================
# create_connector (class method)
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_factory(self):
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.DataSourceEntitiesProcessor") as mock_dep:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance.org_id = "org-1"
            mock_dep.return_value = mock_instance

            logger = logging.getLogger("test")
            dsp = MagicMock()
            mock_tx = MagicMock()
            mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
            mock_tx.__aexit__ = AsyncMock(return_value=None)
            dsp.transaction.return_value = mock_tx
            cs = MagicMock()

            connector = await ConfluenceConnector.create_connector(
                logger, dsp, cs, "c1", "team", "test-user-id"
            )
            assert isinstance(connector, ConfluenceConnector)
            mock_instance.initialize.assert_awaited_once()


# ===========================================================================
# _extract_cursor_from_next_link
# ===========================================================================
class TestExtractCursorFromNextLinkCoverage:
    def test_valid_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20&cursor=eyJpZCI6Ijk5In0=")
        assert result == "eyJpZCI6Ijk5In0="

    def test_no_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20")
        assert result is None

    def test_empty_string(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("")
        assert result is None

    def test_none(self):
        c = _conn()
        result = c._extract_cursor_from_next_link(None)
        assert result is None


# ===========================================================================
# _extract_content_title_from_audit_record
# ===========================================================================
class TestExtractContentTitleFromAuditRecordCoverage:
    def test_non_permission_category(self):
        c = _conn()
        result = c._extract_content_title_from_audit_record({"category": "Security"})
        assert result is None

    def test_no_space(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Page", "name": "My Page"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_content(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Space", "name": "My Space"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_valid_page_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Page", "name": "My Page"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Page"

    def test_blog_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Blog", "name": "My Blog"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Blog"


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncCoverage:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_successful_sync(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._sync_users = AsyncMock()
        c._sync_user_groups = AsyncMock()

        space = MagicMock()
        space.short_name = "TEST"
        space.name = "Test Space"
        c._sync_spaces = AsyncMock(return_value=[space])
        c._sync_content = AsyncMock()
        c._sync_permission_changes_from_audit_log = AsyncMock()

        await c.run_sync()
        c._sync_users.assert_awaited_once()
        c._sync_user_groups.assert_awaited_once()
        assert c._sync_content.await_count == 2  # pages + blogposts


# ===========================================================================
# _sync_users
# ===========================================================================
class TestSyncUsersCoverage:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()


# ===========================================================================
# _sync_user_groups
# ===========================================================================
class TestSyncUserGroupsCoverage:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================
class TestFetchAttachmentContent:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = None
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        # should be an async generator even with error
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_client(self):
        c = _conn()
        c.data_source = None
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = "page-1"
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        assert result is not None


# ===========================================================================
# _map_confluence_permission edge cases
# ===========================================================================
class TestMapConfluencePermissionEdgeCases:
    def test_create_blogpost(self):
        c = _conn()
        result = c._map_confluence_permission("create", "blogpost")
        assert result == PermissionType.WRITE

    def test_create_attachment(self):
        c = _conn()
        result = c._map_confluence_permission("create", "attachment")
        assert result == PermissionType.WRITE

    def test_delete_page(self):
        c = _conn()
        result = c._map_confluence_permission("delete", "page")
        assert result == PermissionType.WRITE

    def test_archive_page(self):
        c = _conn()
        result = c._map_confluence_permission("archive", "page")
        assert result == PermissionType.WRITE

    def test_unknown_returns_other(self):
        c = _conn()
        result = c._map_confluence_permission("unknown_op", "unknown_target")
        assert result == PermissionType.OTHER


# ===========================================================================
# _map_page_permission edge cases
# ===========================================================================
class TestMapPagePermissionEdgeCases:
    def test_delete_permission(self):
        c = _conn()
        result = c._map_page_permission("delete")
        assert result == PermissionType.OTHER

    def test_administer_permission(self):
        c = _conn()
        result = c._map_page_permission("administer")
        assert result == PermissionType.OTHER


# ===========================================================================
# _process_webpage_with_update edge cases
# ===========================================================================
class TestProcessWebpageWithUpdateEdgeCases:
    @pytest.mark.asyncio
    async def test_no_change_skips(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "v10"
        existing.version = 10
        existing.record_name = "Same Title"
        content_data = {
            "id": "page-1",
            "title": "Same Title",
            "version": {"number": 10, "when": "2025-01-01T00:00:00Z"},
            "_links": {"webui": "/wiki/page"},
            "status": "current",
        }
        c._transform_to_webpage_record = MagicMock(return_value=MagicMock(
            external_revision_id="v10", version=10, record_name="Same Title"
        ))
        result = await c._process_webpage_with_update(
            content_data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_transform_returns_none(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {"id": "p1"}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result is not None
        assert result.record is None

# =============================================================================
# Merged from test_confluence_connector_full_coverage.py
# =============================================================================

def _make_mock_deps_fullcov():
    logger = logging.getLogger("test.confluence.full")
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.migrate_group_to_user_by_external_id = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs, mock_tx


def _c():
    logger, dep, dsp, cs, _ = _make_mock_deps_fullcov()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-1", "team", "test-user-id")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


class TestParseConfluenceDatetimeFullCoverage:
    def test_valid(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid(self):
        c = _c()
        assert c._parse_confluence_datetime("not-a-date") is None

    def test_with_timezone(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-01-01T00:00:00+00:00")
        assert isinstance(result, int)


class TestConstructWebUrlFullCoverage:
    def test_with_base_url(self):
        c = _c()
        links = {"webui": "/spaces/ENG/pages/123"}
        assert c._construct_web_url(links, "https://company.atlassian.net/wiki") == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_without_base_url_v1_fallback(self):
        c = _c()
        links = {"webui": "/pages/123", "self": "https://company.atlassian.net/wiki/rest/api/content/123"}
        result = c._construct_web_url(links)
        assert result == "https://company.atlassian.net/wiki/pages/123"

    def test_no_webui(self):
        c = _c()
        assert c._construct_web_url({}) is None

    def test_no_base_no_self(self):
        c = _c()
        links = {"webui": "/page"}
        assert c._construct_web_url(links) is None


class TestMapConfluencePermissionFullCoverage:
    def test_administer(self):
        c = _c()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _c()
        assert c._map_confluence_permission("read", "space") == PermissionType.READ

    def test_delete_space(self):
        c = _c()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_comment(self):
        c = _c()
        assert c._map_confluence_permission("create", "comment") == PermissionType.COMMENT

    def test_delete_comment(self):
        c = _c()
        assert c._map_confluence_permission("delete", "comment") == PermissionType.COMMENT

    def test_create_page(self):
        c = _c()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_archive_blogpost(self):
        c = _c()
        assert c._map_confluence_permission("archive", "blogpost") == PermissionType.WRITE

    def test_delete_attachment(self):
        c = _c()
        assert c._map_confluence_permission("delete", "attachment") == PermissionType.WRITE

    def test_export_fallback(self):
        c = _c()
        assert c._map_confluence_permission("export", "space") == PermissionType.OTHER


class TestMapPagePermissionFullCoverage:
    def test_read(self):
        c = _c()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _c()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _c()
        assert c._map_page_permission("magic") == PermissionType.OTHER


class TestTransformToAppUserFullCoverage:
    def test_valid(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@test.com", "displayName": "User"})
        assert user is not None
        assert user.email == "u@test.com"

    def test_no_account_id(self):
        c = _c()
        assert c._transform_to_app_user({"email": "u@test.com"}) is None

    def test_no_email(self):
        c = _c()
        assert c._transform_to_app_user({"accountId": "u1"}) is None

    def test_with_last_modified(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "displayName": "User One", "lastModified": "2025-01-01T00:00:00Z"})
        assert user is not None
        assert user.source_updated_at is not None

    def test_exception_returns_none(self):
        c = _c()
        with patch.object(c, "_parse_confluence_datetime", side_effect=Exception("fail")):
            assert c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "lastModified": "bad"}) is None


class TestTransformToUserGroupFullCoverage:
    def test_valid(self):
        c = _c()
        g = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert g is not None
        assert g.name == "devs"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_no_name(self):
        c = _c()
        assert c._transform_to_user_group({"id": "g1"}) is None


class TestTransformToSpaceRecordGroupFullCoverage:
    def test_valid_with_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG", "_links": {"webui": "/spaces/ENG"}, "createdAt": "2025-01-01T00:00:00Z"}
        rg = c._transform_to_space_record_group(data, "https://company.atlassian.net/wiki")
        assert rg is not None
        assert rg.name == "Engineering"
        assert "ENG" in rg.web_url

    def test_without_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG"}
        rg = c._transform_to_space_record_group(data)
        assert rg is not None
        assert rg.web_url is None

    def test_missing_id(self):
        c = _c()
        assert c._transform_to_space_record_group({"name": "Eng"}) is None

    def test_missing_name(self):
        c = _c()
        assert c._transform_to_space_record_group({"id": "s1"}) is None


class TestTransformToWebpageRecordFullCoverage:
    def test_v2_format(self):
        c = _c()
        data = {
            "id": "p1", "title": "Test Page", "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-01-02T00:00:00Z", "number": 3},
            "spaceId": 100, "parentId": "p0",
            "_links": {"webui": "/page/p1", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert rec is not None
        assert rec.record_name == "Test Page"
        assert rec.external_record_group_id == "100"
        assert rec.parent_external_record_id == "p0"

    def test_v1_format(self):
        c = _c()
        data = {
            "id": "p2", "title": "Blog",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 5}},
            "space": {"id": 200}, "ancestors": [{"id": "root"}, {"id": "parent1"}],
            "_links": {"webui": "/blog", "self": "https://c.atlassian.net/wiki/rest/api/content/p2"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert rec is not None
        assert rec.parent_external_record_id == "parent1"

    def test_no_space(self):
        c = _c()
        data = {"id": "p3", "title": "Orphan"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "3"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 3

    def test_existing_record_same_version(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 2


class TestTransformToAttachmentFileRecordFullCoverage:
    def test_v1_format(self):
        c = _c()
        data = {
            "id": "att-1", "title": "doc.pdf",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 2}},
            "extensions": {"fileSize": 1024, "mediaType": "application/pdf"},
            "_links": {"webui": "/att", "self": "https://c.atlassian.net/wiki/rest/api/content/att-1"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-1", "space-1")
        assert rec is not None
        assert rec.record_name == "doc.pdf"
        assert rec.extension == "pdf"
        assert rec.size_in_bytes == 1024

    def test_v2_format(self):
        c = _c()
        data = {
            "id": "att-2", "title": "img.png",
            "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-03-01T00:00:00Z", "number": 1},
            "fileSize": 2048, "mediaType": "image/png",
            "_links": {"webui": "/att2", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-2", "space-2")
        assert rec is not None
        assert rec.extension == "png"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"title": "f.txt"}, "p", "s") is None

    def test_no_title(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"id": "att-3"}, "p", "s") is None

    def test_query_params_in_title(self):
        c = _c()
        data = {"id": "att-4", "title": "image.jpg?version=1", "mediaType": "image/jpeg", "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec.record_name == "image.jpg"

    def test_unknown_media_type(self):
        c = _c()
        data = {"id": "att-5", "title": "f.xyz", "extensions": {"mediaType": "application/x-custom"}, "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec is not None


class TestTransformToCommentRecordFullCoverage:
    def test_valid_footer_comment(self):
        c = _c()
        data = {
            "id": "c1", "title": "Comment Title",
            "version": {"authorId": "user-1", "createdAt": "2025-01-01T00:00:00Z", "number": 1},
            "_links": {"webui": "/comment/c1"},
        }
        rec = c._transform_to_comment_record(data, "page-1", "space-1", "footer", None, base_url="https://c.atlassian.net/wiki")
        assert rec is not None
        assert rec.record_type == RecordType.COMMENT

    def test_inline_comment_with_resolution(self):
        c = _c()
        data = {
            "id": "c2", "title": "",
            "version": {"authorId": "user-2", "createdAt": "2025-01-01T00:00:00Z", "number": 1},
            "resolutionStatus": True,
            "properties": {"inlineOriginalSelection": "selected text"},
            "_links": {},
        }
        rec = c._transform_to_comment_record(data, "page-1", "space-1", "inline", None)
        assert rec is not None
        assert rec.record_type == RecordType.INLINE_COMMENT

    def test_no_author(self):
        c = _c()
        data = {"id": "c3", "version": {}, "_links": {}}
        assert c._transform_to_comment_record(data, "p", "s", "footer", None) is None

    def test_no_id(self):
        c = _c()
        assert c._transform_to_comment_record({}, "p", "s", "footer", None) is None

    def test_with_parent_comment(self):
        c = _c()
        data = {
            "id": "c4", "version": {"authorId": "u1", "number": 1}, "_links": {},
        }
        rec = c._transform_to_comment_record(data, "page-1", "space-1", "footer", "parent-c1")
        assert rec.parent_record_type == RecordType.COMMENT

    def test_existing_record_version(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-c"
        existing.version = 1
        existing.external_revision_id = "1"
        data = {
            "id": "c5", "version": {"authorId": "u1", "number": 2}, "_links": {},
        }
        rec = c._transform_to_comment_record(data, "p", "s", "footer", None, existing_record=existing)
        assert rec.version == 2


class TestProcessWebpageWithUpdateFullCoverage:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _c()
        data = {"id": "p1", "title": "Page", "version": {"number": 1}, "spaceId": 1, "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.is_new is True
        assert result.record is not None

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _c()
        existing = MagicMock()
        existing.id = "e1"
        existing.version = 1
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {"id": "p1", "title": "Page", "version": {"number": 2}, "spaceId": 1, "parentId": "new-parent", "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, existing, [])
        assert result.content_changed is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _c()
        result = await c._process_webpage_with_update({}, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.record is None


class TestCreatePermissionFromPrincipalFullCoverage:
    @pytest.mark.asyncio
    async def test_user_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps_fullcov()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_by_source_id = AsyncMock(return_value=MagicMock(email="u@t.com"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "u@t.com"

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _c()
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_with_pseudo_group(self):
        c = _c()
        c._create_pseudo_group = AsyncMock(return_value=MagicMock(source_user_group_id="u1"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ, create_pseudo_group_if_missing=True)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps_fullcov()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(source_user_group_id="g1"))
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _c()
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        c = _c()
        perm = await c._create_permission_from_principal("robot", "r1", PermissionType.READ)
        assert perm is None


class TestCreatePseudoGroupFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        result = await c._create_pseudo_group("account-123")
        assert result is not None
        assert PSEUDO_USER_GROUP_PREFIX in result.name
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _c()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("db fail"))
        result = await c._create_pseudo_group("account-123")
        assert result is None


class TestTransformPageRestrictionToPermissionsFullCoverage:
    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _c()
        result = await c._transform_page_restriction_to_permissions({})
        assert result == []

    @pytest.mark.asyncio
    async def test_users_and_groups(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "u1"}]},
                "group": {"results": [{"id": "g1"}]},
            },
        }
        result = await c._transform_page_restriction_to_permissions(data)
        assert len(result) == 2


class TestTransformSpacePermissionFullCoverage:
    @pytest.mark.asyncio
    async def test_valid(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {"principal": {"type": "user", "id": "u1"}, "operation": {"key": "read", "targetType": "space"}}
        result = await c._transform_space_permission(data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_fields(self):
        c = _c()
        assert await c._transform_space_permission({}) is None


class TestFetchGroupMembersFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "u@t.com", "displayName": "U"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "devs")
        assert "u@t.com" in emails

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == []

    @pytest.mark.asyncio
    async def test_skips_no_email(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "", "displayName": "NoEmail"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == []


class TestGetAppUsersByEmailsFullCoverage:
    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _c()
        u1 = MagicMock(email="a@t.com")
        u2 = MagicMock(email="b@t.com")
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_list(self):
        c = _c()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_error(self):
        c = _c()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        assert await c._get_app_users_by_emails(["a@t.com"]) == []


class TestGetSignedUrlFullCoverage:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _c()
        assert await c.get_signed_url(MagicMock()) == ""


class TestStreamRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_page_stream(self):
        c = _c()
        c._fetch_page_content = AsyncMock(return_value="<h1>Hello</h1>")
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.record_name = "Test"
        record.external_record_id = "p1"
        resp = await c.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_comment_stream(self):
        c = _c()
        c._fetch_comment_content = AsyncMock(return_value="<p>Comment</p>")
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.record_name = "Comment"
        record.external_record_id = "c1"
        resp = await c.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock()
        record.record_type = RecordType.OTHERS
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c.stream_record(record)


class TestFetchPageContentFullCoverage:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Content</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "<p>Content</p>" in result

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert "<p>Blog</p>" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result


class TestFetchCommentContentFullCoverage:
    @pytest.mark.asyncio
    async def test_footer_comment(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_footer_comment_by_id = AsyncMock(return_value=_resp(200, {
            "body": {"storage": {"value": "<p>Footer</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.COMMENT
        record.external_record_id = "101"
        result = await c._fetch_comment_content(record)
        assert "Footer" in result

    @pytest.mark.asyncio
    async def test_inline_comment(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_inline_comment_by_id = AsyncMock(return_value=_resp(200, {
            "body": {"storage": {"value": "<p>Inline</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.record_type = RecordType.INLINE_COMMENT
        record.external_record_id = "202"
        result = await c._fetch_comment_content(record)
        assert "Inline" in result


class TestReindexRecordsFullCoverage:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _c()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_with_updated_and_non_updated(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        rec1 = MagicMock(id="r1", record_type=RecordType.CONFLUENCE_PAGE)
        rec2 = MagicMock(id="r2", record_type=RecordType.CONFLUENCE_PAGE)

        updated = (MagicMock(), [Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[updated, None])

        await c.reindex_records([rec1, rec2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()


class TestCheckAndFetchUpdatedRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_dispatches_page(self):
        c = _c()
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_PAGE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_blogpost(self):
        c = _c()
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_BLOGPOST)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_comment(self):
        c = _c()
        c._check_and_fetch_updated_comment = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.COMMENT)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_file(self):
        c = _c()
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.FILE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock(record_type="UNKNOWN")
        result = await c._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedPageFullCoverage:
    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 5},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        record.id = "e1"
        record.version = 1
        record.parent_external_record_id = None

        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 3},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None


class TestRunIncrementalSyncFullCoverage:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _c()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


class TestCleanupFullCoverage:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _c()
        await c.cleanup()


class TestHandleWebhookNotificationFullCoverage:
    @pytest.mark.asyncio
    async def test_does_nothing(self):
        c = _c()
        await c.handle_webhook_notification({"type": "test"})


class TestGetFilterOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _c()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _c()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _c()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _c()
        with pytest.raises(ValueError):
            await c.get_filter_options("unknown_key")


class TestGetSpaceOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "ENG", "name": "Engineering"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "Eng", None)
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {"next": "/wiki/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert result.has_more is True


class TestGetPageOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "My Page", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "My", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert result.success is True


class TestGetBlogpostOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Blog", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Blog", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Post 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert result.success is True


class TestSyncSpacesIncludeFilter:
    @pytest.mark.asyncio
    async def test_in_filter(self):
        c = _c()
        c.indexing_filters = FilterCollection()
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.IN
        space_filter.get_value.return_value = ["ENG"]
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None)

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "1", "key": "ENG", "name": "Engineering"}],
            "_links": {"base": "https://c.atlassian.net/wiki"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=RecordGroup(
            external_group_id="1", name="Engineering", short_name="ENG",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE, connector_id="conn-1",
        ))

        spaces = await c._sync_spaces()
        assert len(spaces) == 1
        mock_ds.get_spaces.assert_awaited_once()
        call_kwargs = mock_ds.get_spaces.call_args
        assert call_kwargs[1].get("keys") == ["ENG"]
