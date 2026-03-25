"""Tests for app.connectors.sources.microsoft.outlook.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.microsoft.outlook.connector import (
    STANDARD_OUTLOOK_FOLDERS,
    THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH,
    OutlookConnector,
    OutlookCredentials,
)
from app.models.entities import AppUser, AppUserGroup


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.outlook")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-outlook-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return OutlookConnector(logger, dep, dsp, cs, "conn-outlook-1")


# ===========================================================================
# Constants
# ===========================================================================


class TestOutlookConstants:

    def test_standard_folders_not_empty(self):
        assert len(STANDARD_OUTLOOK_FOLDERS) > 0
        assert "Inbox" in STANDARD_OUTLOOK_FOLDERS
        assert "Sent Items" in STANDARD_OUTLOOK_FOLDERS
        assert "Drafts" in STANDARD_OUTLOOK_FOLDERS

    def test_thread_root_conversation_index_length(self):
        assert THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH == 22


# ===========================================================================
# OutlookCredentials
# ===========================================================================


class TestOutlookCredentials:

    def test_default_admin_consent(self):
        creds = OutlookCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1"
        )
        assert creds.has_admin_consent is False

    def test_with_admin_consent(self):
        creds = OutlookCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1",
            has_admin_consent=True,
        )
        assert creds.has_admin_consent is True


# ===========================================================================
# OutlookConnector.__init__
# ===========================================================================


class TestOutlookConnectorInit:

    def test_connector_initializes_with_correct_name(self):
        connector = _make_connector()
        assert connector.connector_name == Connectors.OUTLOOK
        assert connector.connector_id == "conn-outlook-1"

    def test_connector_has_sync_points(self):
        connector = _make_connector()
        assert connector.email_delta_sync_point is not None
        assert connector.group_conversations_sync_point is not None

    def test_connector_has_empty_caches(self):
        connector = _make_connector()
        assert connector._user_cache == {}
        assert connector._user_cache_timestamp is None
        assert connector._group_cache == {}


# ===========================================================================
# OutlookConnector.init (initialization)
# ===========================================================================


class TestOutlookConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "tenant-1",
                "clientId": "client-1",
                "clientSecret": "secret-1",
                "hasAdminConsent": True,
            }
        })

        with patch("app.connectors.sources.microsoft.outlook.connector.ExternalMSGraphClient") as mock_ext_client, \
             patch("app.connectors.sources.microsoft.outlook.connector.OutlookCalendarContactsDataSource"), \
             patch("app.connectors.sources.microsoft.outlook.connector.UsersGroupsDataSource"), \
             patch("app.connectors.sources.microsoft.outlook.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (MagicMock(), MagicMock())
            mock_ext_client.build_with_config = MagicMock()

            # Mock test_connection_and_access
            connector.test_connection_and_access = AsyncMock(return_value=True)

            result = await connector.init()
            assert result is True

    @pytest.mark.asyncio
    async def test_init_failure_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)

        result = await connector.init()
        assert result is False


# ===========================================================================
# OutlookConnector._get_credentials
# ===========================================================================


class TestGetCredentials:

    @pytest.mark.asyncio
    async def test_get_credentials_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t1",
                "clientId": "c1",
                "clientSecret": "s1",
                "hasAdminConsent": True,
            }
        })

        creds = await connector._get_credentials("conn-outlook-1")
        assert creds.tenant_id == "t1"
        assert creds.client_id == "c1"
        assert creds.client_secret == "s1"
        assert creds.has_admin_consent is True

    @pytest.mark.asyncio
    async def test_get_credentials_no_config_raises(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await connector._get_credentials("conn-outlook-1")


# ===========================================================================
# OutlookConnector.test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_returns_false_when_clients_not_initialized(self):
        connector = _make_connector()
        connector.external_outlook_client = None
        connector.external_users_client = None
        connector.credentials = None

        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_credentials_incomplete(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector.credentials = OutlookCredentials(
            tenant_id="", client_id="c1", client_secret="s1"
        )

        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_successful_api_call(self):
        connector = _make_connector()
        connector.credentials = OutlookCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1"
        )
        connector.external_outlook_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(return_value=mock_response)

        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self):
        connector = _make_connector()
        connector.credentials = OutlookCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1"
        )
        connector.external_outlook_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Auth failed"
        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(return_value=mock_response)

        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# OutlookConnector._populate_user_cache
# ===========================================================================


class TestPopulateUserCache:

    @pytest.mark.asyncio
    async def test_populates_cache(self):
        connector = _make_connector()
        user1 = AppUser(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_id="su1",
            email="user1@example.com",
            full_name="User One",
        )
        connector._get_all_users_external = AsyncMock(return_value=[user1])

        await connector._populate_user_cache()
        assert "user1@example.com" in connector._user_cache
        assert connector._user_cache["user1@example.com"] == "su1"

    @pytest.mark.asyncio
    async def test_cache_is_reused_within_ttl(self):
        connector = _make_connector()
        connector._user_cache = {"cached@example.com": "cached-id"}
        connector._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())
        connector._get_all_users_external = AsyncMock()

        await connector._populate_user_cache()
        # Should NOT call external API since cache is still valid
        connector._get_all_users_external.assert_not_awaited()


# ===========================================================================
# OutlookConnector._get_user_id_from_email
# ===========================================================================


class TestGetUserIdFromEmail:

    @pytest.mark.asyncio
    async def test_returns_user_id_from_cache(self):
        connector = _make_connector()
        connector._user_cache = {"test@example.com": "uid-1"}
        connector._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())

        connector._get_all_users_external = AsyncMock(return_value=[])

        result = await connector._get_user_id_from_email("test@example.com")
        assert result == "uid-1"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_email(self):
        connector = _make_connector()
        connector._user_cache = {}
        connector._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())
        connector._get_all_users_external = AsyncMock(return_value=[])

        result = await connector._get_user_id_from_email("unknown@example.com")
        assert result is None


# ===========================================================================
# OutlookConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_raises_when_clients_not_initialized(self):
        connector = _make_connector()
        connector.external_outlook_client = None
        connector.external_users_client = None

        with pytest.raises(Exception, match="not initialized"):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_calls_sync_steps(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()

        mock_users = [MagicMock(email="user@example.com", source_user_id="su1")]
        connector._sync_users = AsyncMock(return_value=mock_users)
        connector._sync_user_groups = AsyncMock(return_value=[])
        connector._sync_group_conversations = AsyncMock()

        async def mock_process_users(*args, **kwargs):
            return
            yield  # noqa: E275 - Make it an async generator

        connector._process_users = mock_process_users

        await connector.run_sync()

        connector._sync_users.assert_awaited_once()
        connector._sync_user_groups.assert_awaited_once()
        connector._sync_group_conversations.assert_awaited_once()


# ===========================================================================
# OutlookConnector._safe_get_attr helper
# ===========================================================================


class TestSafeGetAttr:

    def test_existing_attr(self):
        connector = _make_connector()
        obj = MagicMock()
        obj.some_field = "value"
        result = connector._safe_get_attr(obj, "some_field")
        assert result == "value"

    def test_missing_attr_returns_default(self):
        connector = _make_connector()
        obj = MagicMock(spec=[])
        result = connector._safe_get_attr(obj, "missing_field", "default_val")
        assert result == "default_val"
