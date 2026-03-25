"""Tests for app.connectors.sources.atlassian.confluence_cloud.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
