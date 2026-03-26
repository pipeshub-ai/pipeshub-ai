"""Tests for app.connectors.sources.microsoft.outlook.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.sources.microsoft.outlook.connector import (
    STANDARD_OUTLOOK_FOLDERS,
    THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH,
    OutlookConnector,
    OutlookCredentials,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


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
    data_entities_processor.on_user_group_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_conversation_index = AsyncMock(return_value=None)
    mock_tx.batch_create_edges = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return OutlookConnector(logger, dep, dsp, cs, "conn-outlook-1")


def _make_graph_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


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

    @pytest.mark.asyncio
    async def test_returns_false_on_api_exception(self):
        connector = _make_connector()
        connector.credentials = OutlookCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1"
        )
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(side_effect=Exception("Network error"))

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

    @pytest.mark.asyncio
    async def test_cache_exception_handled(self):
        connector = _make_connector()
        connector._get_all_users_external = AsyncMock(side_effect=Exception("API error"))
        await connector._populate_user_cache()
        # Should not raise


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


# ===========================================================================
# OutlookConnector._get_all_users_external
# ===========================================================================


class TestGetAllUsersExternal:

    @pytest.mark.asyncio
    async def test_single_page_of_users(self):
        connector = _make_connector()
        mock_user = MagicMock()
        mock_user.display_name = "Alice Smith"
        mock_user.given_name = "Alice"
        mock_user.surname = "Smith"
        mock_user.mail = "alice@example.com"
        mock_user.user_principal_name = "alice@example.com"
        mock_user.id = "user-1"

        mock_data = MagicMock()
        mock_data.value = [mock_user]
        mock_data.odata_next_link = None

        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        users = await connector._get_all_users_external()
        assert len(users) == 1
        assert users[0].full_name == "Alice Smith"
        assert users[0].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector = _make_connector()
        connector.external_users_client = None
        result = await connector._get_all_users_external()
        assert result == []

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(
            return_value=_make_graph_response(success=False, error="Error")
        )
        result = await connector._get_all_users_external()
        assert result == []


# ===========================================================================
# OutlookConnector._get_all_microsoft_365_groups
# ===========================================================================


class TestGetAllMicrosoft365Groups:

    @pytest.mark.asyncio
    async def test_filters_unified_mail_enabled_groups(self):
        connector = _make_connector()

        group1 = MagicMock()
        group1.group_types = ["Unified"]
        group1.mail_enabled = True
        group1.mailEnabled = True
        group1.id = "g1"

        group2 = MagicMock()
        group2.group_types = ["DynamicMembership"]
        group2.mail_enabled = False
        group2.mailEnabled = False
        group2.id = "g2"

        mock_data = MagicMock()
        mock_data.value = [group1, group2]
        mock_data.odata_next_link = None

        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_groups = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        groups = await connector._get_all_microsoft_365_groups()
        # Only group1 (Unified + mail_enabled) should pass
        assert len(groups) == 1

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = None
        result = await connector._get_all_microsoft_365_groups()
        assert result == []


# ===========================================================================
# OutlookConnector._get_group_members
# ===========================================================================


class TestGetGroupMembers:

    @pytest.mark.asyncio
    async def test_fetches_members(self):
        connector = _make_connector()

        member = MagicMock()
        member.mail = "alice@example.com"
        member.display_name = "Alice"
        member.id = "m1"

        mock_data = MagicMock()
        mock_data.value = [member]
        mock_data.odata_next_link = None

        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_transitive_members = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        members = await connector._get_group_members("g1")
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_transitive_members = AsyncMock(
            return_value=_make_graph_response(success=False, error="Forbidden")
        )
        result = await connector._get_group_members("g1")
        assert result == []


# ===========================================================================
# OutlookConnector._get_user_groups
# ===========================================================================


class TestGetUserGroups:

    @pytest.mark.asyncio
    async def test_returns_groups(self):
        connector = _make_connector()
        mock_data = MagicMock()
        mock_data.value = [{"id": "g1", "displayName": "Group 1"}]

        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_member_of = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        groups = await connector._get_user_groups("user-1")
        assert len(groups) == 1

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = None
        result = await connector._get_user_groups("user-1")
        assert result == []


# ===========================================================================
# OutlookConnector._transform_group_to_record_group
# ===========================================================================


class TestTransformGroupToRecordGroup:

    def test_successful_transform(self):
        connector = _make_connector()
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Engineering"
        group.mail = "eng@example.com"
        group.created_date_time = None

        result = connector._transform_group_to_record_group(group)
        assert result is not None
        assert result.name == "Engineering"
        assert result.external_group_id == "g1"
        assert result.group_type == RecordGroupType.GROUP_MAILBOX
        assert "eng@example.com" in result.description

    def test_no_group_id_returns_none(self):
        connector = _make_connector()
        group = MagicMock(spec=[])  # No attributes
        result = connector._transform_group_to_record_group(group)
        assert result is None


# ===========================================================================
# OutlookConnector._determine_folder_filter_strategy
# ===========================================================================


class TestDetermineFolderFilterStrategy:

    def test_scenario1_no_selection_custom_enabled(self):
        """Nothing selected + custom enabled -> sync all."""
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()

        folder_names, mode = connector._determine_folder_filter_strategy()
        assert folder_names is None
        assert mode is None

    def test_scenario2_no_selection_custom_disabled(self):
        """Nothing selected + custom disabled -> sync only standard."""
        connector = _make_connector()
        mock_filters = MagicMock()

        # No folder selection
        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = True

        # Custom folders disabled
        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = False

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.FOLDERS:
                return folders_filter
            elif key == SyncFilterKey.CUSTOM_FOLDERS:
                return custom_filter
            return None

        mock_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_filters

        folder_names, mode = connector._determine_folder_filter_strategy()
        assert folder_names == STANDARD_OUTLOOK_FOLDERS
        assert mode == "include"

    def test_scenario3_selected_folders_no_custom(self):
        """Selected standard folders + custom disabled -> include only selected."""
        connector = _make_connector()
        mock_filters = MagicMock()

        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = ["Inbox", "Sent Items"]

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = False

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.FOLDERS:
                return folders_filter
            elif key == SyncFilterKey.CUSTOM_FOLDERS:
                return custom_filter
            return None

        mock_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_filters

        folder_names, mode = connector._determine_folder_filter_strategy()
        assert folder_names == ["Inbox", "Sent Items"]
        assert mode == "include"


# ===========================================================================
# OutlookConnector._sync_group_conversations
# ===========================================================================


class TestSyncGroupConversations:

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._sync_group_conversations([MagicMock()])

    @pytest.mark.asyncio
    async def test_empty_groups_returns(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        await connector._sync_group_conversations([])
        # Should not raise

    @pytest.mark.asyncio
    async def test_syncs_group_conversations(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector._sync_single_group_conversations = AsyncMock(return_value=5)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        await connector._sync_group_conversations([group])
        connector._sync_single_group_conversations.assert_awaited_once()


# ===========================================================================
# OutlookConnector._get_group_threads
# ===========================================================================


class TestGetGroupThreads:

    @pytest.mark.asyncio
    async def test_get_threads_success(self):
        connector = _make_connector()
        mock_data = MagicMock()
        mock_data.value = [{"id": "thread-1", "topic": "Test"}]
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_list_threads = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        threads = await connector._get_group_threads("g1")
        assert len(threads) == 1

    @pytest.mark.asyncio
    async def test_get_threads_with_timestamp_filter(self):
        connector = _make_connector()
        mock_data = MagicMock()
        mock_data.value = [{"id": "thread-1"}]
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_list_threads = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        threads = await connector._get_group_threads("g1", "2024-01-01T00:00:00Z")
        assert len(threads) == 1
        # Verify filter was passed
        call_kwargs = connector.external_outlook_client.groups_list_threads.call_args[1]
        assert call_kwargs.get("filter") is not None

    @pytest.mark.asyncio
    async def test_get_threads_api_failure(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_list_threads = AsyncMock(
            return_value=_make_graph_response(success=False, error="Forbidden")
        )
        threads = await connector._get_group_threads("g1")
        assert threads == []


# ===========================================================================
# OutlookConnector._get_thread_posts
# ===========================================================================


class TestGetThreadPosts:

    @pytest.mark.asyncio
    async def test_get_posts_success(self):
        connector = _make_connector()
        mock_data = MagicMock()
        mock_data.value = [{"id": "post-1"}, {"id": "post-2"}]
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_list_posts = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        posts = await connector._get_thread_posts("g1", "thread-1")
        assert len(posts) == 2

    @pytest.mark.asyncio
    async def test_get_posts_failure(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_list_posts = AsyncMock(
            return_value=_make_graph_response(success=False, error="Not found")
        )
        posts = await connector._get_thread_posts("g1", "thread-1")
        assert posts == []


# ===========================================================================
# OutlookConnector._download_group_post_attachment
# ===========================================================================


class TestDownloadGroupPostAttachment:

    @pytest.mark.asyncio
    async def test_download_success(self):
        import base64
        connector = _make_connector()
        content = b"Hello PDF"
        b64_content = base64.b64encode(content).decode()

        mock_data = MagicMock()
        mock_data.content_bytes = b64_content
        mock_data.contentBytes = None

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_posts_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._download_group_post_attachment("g1", "t1", "p1", "a1")
        assert result == content

    @pytest.mark.asyncio
    async def test_download_failure_returns_empty_bytes(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_posts_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=False)
        )
        result = await connector._download_group_post_attachment("g1", "t1", "p1", "a1")
        assert result == b''


# ===========================================================================
# OutlookConnector._find_parent_by_conversation_index_from_db
# ===========================================================================


class TestFindParentByConversationIndex:

    @pytest.mark.asyncio
    async def test_no_conversation_index(self):
        connector = _make_connector()
        user = MagicMock()
        result = await connector._find_parent_by_conversation_index_from_db("", "thread-1", "org-1", user)
        assert result is None

    @pytest.mark.asyncio
    async def test_root_message_returns_none(self):
        """Root messages (22 bytes or less) have no parent."""
        import base64
        connector = _make_connector()
        user = MagicMock()
        # 22 bytes = root message
        root_index = base64.b64encode(b"A" * 22).decode()
        result = await connector._find_parent_by_conversation_index_from_db(root_index, "thread-1", "org-1", user)
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_parent_in_db(self):
        """Non-root messages search for parent in DB."""
        import base64
        connector = _make_connector()
        user = MagicMock()
        user.user_id = "u1"

        # 27 bytes = non-root (22 header + 5 child)
        child_index = base64.b64encode(b"A" * 27).decode()

        # Mock DB to return parent record
        mock_parent = MagicMock()
        mock_parent.id = "parent-record-id"

        mock_tx = MagicMock()
        mock_tx.get_record_by_conversation_index = AsyncMock(return_value=mock_parent)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        result = await connector._find_parent_by_conversation_index_from_db(child_index, "thread-1", "org-1", user)
        assert result == "parent-record-id"


# ===========================================================================
# OutlookConnector._create_all_thread_edges_for_user
# ===========================================================================


class TestCreateAllThreadEdges:

    @pytest.mark.asyncio
    async def test_no_records_returns_zero(self):
        connector = _make_connector()
        user = MagicMock(email="u@example.com")
        result = await connector._create_all_thread_edges_for_user("org-1", user, [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_creates_edges_for_records_with_parents(self):
        connector = _make_connector()
        user = MagicMock(email="u@example.com")

        record = MagicMock()
        record.conversation_index = "some_index"
        record.thread_id = "thread-1"
        record.id = "record-1"

        connector._find_parent_by_conversation_index_from_db = AsyncMock(return_value="parent-id")

        result = await connector._create_all_thread_edges_for_user("org-1", user, [record])
        assert result == 1


# ===========================================================================
# OutlookConnector._get_child_folders_recursive
# ===========================================================================


class TestGetChildFoldersRecursive:

    @pytest.mark.asyncio
    async def test_no_children_returns_empty(self):
        connector = _make_connector()
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"
        folder.child_folder_count = 0

        result = await connector._get_child_folders_recursive("user-1", folder)
        assert result == []

    @pytest.mark.asyncio
    async def test_recursive_fetch(self):
        connector = _make_connector()

        parent = MagicMock()
        parent.id = "f1"
        parent.display_name = "Parent"
        parent.child_folder_count = 1

        child = MagicMock()
        child.id = "f2"
        child.display_name = "Child"
        child.child_folder_count = 0

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_mail_folders_list_child_folders = AsyncMock(
            return_value=_make_graph_response(success=True, data={"value": [child]})
        )

        result = await connector._get_child_folders_recursive("user-1", parent)
        assert len(result) == 1


# ===========================================================================
# OutlookConnector._sync_user_groups (full flow)
# ===========================================================================


class TestSyncUserGroupsFull:

    @pytest.mark.asyncio
    async def test_handles_deleted_group(self):
        """Groups marked as deleted trigger on_user_group_deleted."""
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector._user_cache = {}

        deleted_group = MagicMock()
        deleted_group.id = "g1"
        deleted_group.display_name = "Deleted Group"
        deleted_group.additional_data = {"@removed": {"reason": "deleted"}}
        deleted_group.group_types = ["Unified"]
        deleted_group.mail_enabled = True
        deleted_group.mailEnabled = True

        connector._get_all_microsoft_365_groups = AsyncMock(return_value=[deleted_group])

        await connector._sync_user_groups()
        connector.data_entities_processor.on_user_group_deleted.assert_awaited_once()


# ===========================================================================
# Deep Sync: _sync_single_group_conversations
# ===========================================================================


class TestSyncSingleGroupConversations:

    @pytest.mark.asyncio
    async def test_no_threads_returns_zero(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.group_conversations_sync_point.update_sync_point = AsyncMock()

        connector._get_group_threads = AsyncMock(return_value=[])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        result = await connector._sync_single_group_conversations(group)
        assert result == 0

    @pytest.mark.asyncio
    async def test_threads_with_posts_processed(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.group_conversations_sync_point.update_sync_point = AsyncMock()

        thread = MagicMock()
        thread.id = "thread-1"
        connector._get_group_threads = AsyncMock(return_value=[thread])
        connector._process_group_thread = AsyncMock(return_value=3)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        result = await connector._sync_single_group_conversations(group)
        assert result == 3
        connector.group_conversations_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_threads_with_last_sync_timestamp(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(
            return_value={'last_sync_timestamp': '2024-06-01T12:00:00Z'}
        )
        connector.group_conversations_sync_point.update_sync_point = AsyncMock()

        connector._get_group_threads = AsyncMock(return_value=[])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        result = await connector._sync_single_group_conversations(group)
        assert result == 0

    @pytest.mark.asyncio
    async def test_thread_error_continues(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.group_conversations_sync_point.update_sync_point = AsyncMock()

        thread1 = MagicMock()
        thread1.id = "t1"
        thread2 = MagicMock()
        thread2.id = "t2"
        connector._get_group_threads = AsyncMock(return_value=[thread1, thread2])
        connector._process_group_thread = AsyncMock(side_effect=[Exception("API fail"), 2])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        result = await connector._sync_single_group_conversations(group)
        assert result == 2

    @pytest.mark.asyncio
    async def test_invalid_timestamp_falls_back_to_full_sync(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(
            return_value={'last_sync_timestamp': 'not-a-date'}
        )
        connector.group_conversations_sync_point.update_sync_point = AsyncMock()

        connector._get_group_threads = AsyncMock(return_value=[])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK,
            connector_id="conn-1",
            source_user_group_id="g1",
            name="Engineering",
        )

        result = await connector._sync_single_group_conversations(group)
        # Should call without filter since timestamp was invalid
        connector._get_group_threads.assert_awaited_once_with("g1", None)


# ===========================================================================
# Deep Sync: _process_group_thread
# ===========================================================================


class TestProcessGroupThread:

    @pytest.mark.asyncio
    async def test_no_thread_id_returns_zero(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        thread = MagicMock(spec=[])  # No attributes
        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._process_group_thread("org-1", group, thread)
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_posts_returns_zero(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        thread = MagicMock()
        thread.id = "thread-1"
        connector._get_thread_posts = AsyncMock(return_value=[])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._process_group_thread("org-1", group, thread)
        assert result == 0

    @pytest.mark.asyncio
    async def test_posts_filtered_by_timestamp(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        thread = MagicMock()
        thread.id = "thread-1"
        thread.topic = "Test Topic"

        # 2 posts: one old, one new
        old_post = MagicMock()
        old_post.id = "post-old"
        old_post.received_date_time = "2024-01-01T00:00:00Z"
        old_post.has_attachments = False

        new_post = MagicMock()
        new_post.id = "post-new"
        new_post.received_date_time = "2024-07-01T00:00:00Z"
        new_post.has_attachments = False

        connector._get_thread_posts = AsyncMock(return_value=[old_post, new_post])

        # Mock _process_group_post to return a valid update
        mock_record = MagicMock()
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_group_post = AsyncMock(return_value=update)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        # With last_sync_timestamp = 2024-06-01, only new_post should be processed
        result = await connector._process_group_thread(
            "org-1", group, thread, "2024-06-01T00:00:00Z"
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_posts_with_attachments_processed(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        thread = MagicMock()
        thread.id = "thread-1"
        thread.topic = "Topic"

        post = MagicMock()
        post.id = "post-1"
        post.received_date_time = None
        post.has_attachments = True
        post.conversation_thread_id = "thread-1"

        connector._get_thread_posts = AsyncMock(return_value=[post])

        mock_record = MagicMock()
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_group_post = AsyncMock(return_value=update)

        attachment_record = MagicMock()
        connector._process_group_post_attachments = AsyncMock(
            return_value=[(attachment_record, [])]
        )

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._process_group_thread("org-1", group, thread)
        assert result == 2  # 1 post + 1 attachment

    @pytest.mark.asyncio
    async def test_post_processing_error_continues(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        thread = MagicMock()
        thread.id = "thread-1"
        thread.topic = "Topic"

        post1 = MagicMock()
        post1.id = "post-bad"
        post1.received_date_time = None
        post1.has_attachments = False

        post2 = MagicMock()
        post2.id = "post-good"
        post2.received_date_time = None
        post2.has_attachments = False

        connector._get_thread_posts = AsyncMock(return_value=[post1, post2])

        good_update = MagicMock()
        good_update.record = MagicMock()
        good_update.new_permissions = []
        connector._process_group_post = AsyncMock(
            side_effect=[Exception("Error"), good_update]
        )

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._process_group_thread("org-1", group, thread)
        assert result == 1


# ===========================================================================
# Deep Sync: _process_users and _process_user_emails
# ===========================================================================


class TestProcessUsersDeep:

    @pytest.mark.asyncio
    async def test_process_users_yields_per_user(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        user1 = MagicMock()
        user1.email = "u1@test.com"
        user1.source_user_id = "su1"

        connector._process_user_emails = AsyncMock(return_value="Processed 5 items across 2 folders")

        results = []
        async for status in connector._process_users("org-1", [user1]):
            results.append(status)

        assert len(results) == 1
        assert "5 items" in results[0]

    @pytest.mark.asyncio
    async def test_process_users_error_yields_failure(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        user1 = MagicMock()
        user1.email = "u1@test.com"
        user1.source_user_id = "su1"

        connector._process_user_emails = AsyncMock(side_effect=Exception("Timeout"))

        results = []
        async for status in connector._process_users("org-1", [user1]):
            results.append(status)

        assert len(results) == 1
        assert "Failed" in results[0]

    @pytest.mark.asyncio
    async def test_process_user_emails_no_folders(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        user = MagicMock()
        user.email = "u@test.com"
        user.source_user_id = "su1"

        connector._sync_user_folders = AsyncMock(return_value=[])

        result = await connector._process_user_emails("org-1", user)
        assert "No folders" in result

    @pytest.mark.asyncio
    async def test_process_user_emails_with_folders_and_messages(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        user = MagicMock()
        user.email = "u@test.com"
        user.source_user_id = "su1"

        folder = MagicMock()
        folder.id = "folder-1"
        folder.display_name = "Inbox"

        connector._sync_user_folders = AsyncMock(return_value=[folder])
        connector._process_single_folder_messages = AsyncMock(return_value=(5, [MagicMock()]))
        connector._create_all_thread_edges_for_user = AsyncMock(return_value=2)

        result = await connector._process_user_emails("org-1", user)
        assert "5" in result


# ===========================================================================
# Deep Sync: _process_single_folder_messages
# ===========================================================================


class TestProcessSingleFolderMessages:

    @pytest.mark.asyncio
    async def test_no_messages_returns_zero(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        connector.email_delta_sync_point = MagicMock()
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.email_delta_sync_point.update_sync_point = AsyncMock()

        connector._get_all_messages_delta_external = AsyncMock(return_value={
            'messages': [],
            'delta_link': None,
        })

        user = MagicMock()
        user.email = "u@test.com"
        user.source_user_id = "su1"
        user.user_id = "uid1"

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"

        count, records = await connector._process_single_folder_messages("org-1", user, folder)
        assert count == 0
        assert records == []

    @pytest.mark.asyncio
    async def test_messages_batched_and_processed(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.email_delta_sync_point = MagicMock()
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.email_delta_sync_point.update_sync_point = AsyncMock()

        msg1 = MagicMock()
        msg1.id = "m1"
        msg1.has_attachments = False

        connector._get_all_messages_delta_external = AsyncMock(return_value={
            'messages': [msg1],
            'delta_link': 'https://graph.microsoft.com/v1.0/delta',
        })

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.record_type = RecordType.MAIL
        update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[], external_record_id="m1",
        )
        connector._process_single_message = AsyncMock(return_value=[update])

        user = MagicMock()
        user.email = "u@test.com"
        user.source_user_id = "su1"
        user.user_id = "uid1"

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"

        count, records = await connector._process_single_folder_messages("org-1", user, folder)
        assert count == 1
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_deleted_message_processed(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()

        connector.email_delta_sync_point = MagicMock()
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.email_delta_sync_point.update_sync_point = AsyncMock()

        msg = MagicMock()
        msg.id = "m-del"
        msg.additional_data = {"@removed": {"reason": "deleted"}}

        connector._get_all_messages_delta_external = AsyncMock(return_value={
            'messages': [msg],
            'delta_link': None,
        })

        connector._process_single_message = AsyncMock(return_value=[])

        user = MagicMock()
        user.email = "u@test.com"
        user.source_user_id = "su1"
        user.user_id = "uid1"

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"

        count, records = await connector._process_single_folder_messages("org-1", user, folder)
        assert count == 0


# ===========================================================================
# Deep Sync: _process_single_message
# ===========================================================================


class TestProcessSingleMessage:

    @pytest.mark.asyncio
    async def test_deleted_message(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        msg = MagicMock()
        msg.id = "m-del"
        msg.additional_data = {"@removed": {"reason": "deleted"}}

        mock_tx = MagicMock()
        mock_tx.delete_record_by_external_id = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        user = MagicMock()
        user.email = "u@test.com"
        user.user_id = "uid1"

        updates = await connector._process_single_message("org-1", user, msg, "f1", "Inbox")
        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_new_message_without_attachments(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        msg = MagicMock()
        msg.id = "m1"
        msg.additional_data = {}
        msg.has_attachments = False

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[], external_record_id="m1",
        )
        connector._process_single_email_with_folder = AsyncMock(return_value=mock_update)

        user = MagicMock()
        user.email = "u@test.com"
        user.user_id = "uid1"

        updates = await connector._process_single_message("org-1", user, msg, "f1", "Inbox")
        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_new_message_with_attachments(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        msg = MagicMock()
        msg.id = "m1"
        msg.additional_data = {}
        msg.has_attachments = True
        msg.web_link = "https://outlook.com/m1"

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[], external_record_id="m1",
        )
        connector._process_single_email_with_folder = AsyncMock(return_value=mock_update)

        att_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=[], external_record_id="att-1",
        )
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._process_email_attachments_with_folder = AsyncMock(return_value=[att_update])

        user = MagicMock()
        user.email = "u@test.com"
        user.user_id = "uid1"
        user.source_user_id = "su1"

        updates = await connector._process_single_message("org-1", user, msg, "f1", "Inbox")
        assert len(updates) == 2

    @pytest.mark.asyncio
    async def test_message_error_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        msg = MagicMock()
        msg.id = "m-err"
        msg.additional_data = {}
        msg.has_attachments = False

        connector._process_single_email_with_folder = AsyncMock(side_effect=Exception("Fail"))

        user = MagicMock()
        user.email = "u@test.com"
        user.user_id = "uid1"

        updates = await connector._process_single_message("org-1", user, msg, "f1", "Inbox")
        assert len(updates) == 0


# ===========================================================================
# Deep Sync: _process_single_email_with_folder
# ===========================================================================


class TestProcessSingleEmailWithFolder:

    @pytest.mark.asyncio
    async def test_new_email_record_created(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        msg = MagicMock()
        msg.id = "m1"
        msg.subject = "Test Subject"
        msg.e_tag = "etag-1"
        msg.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        msg.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        msg.web_link = "https://outlook.com/m1"
        msg.from_ = None
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.conversation_id = "conv-1"
        msg.internet_message_id = "imid-1"
        msg.conversation_index = "ci-1"

        connector._get_existing_record = AsyncMock(return_value=None)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._extract_email_from_recipient = MagicMock(return_value="sender@test.com")

        result = await connector._process_single_email_with_folder(
            "org-1", "user@test.com", msg, "folder-1", "Inbox"
        )

        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "Test Subject"

    @pytest.mark.asyncio
    async def test_existing_email_content_changed(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        msg = MagicMock()
        msg.id = "m2"
        msg.subject = "Updated Subject"
        msg.e_tag = "new-etag"
        msg.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        msg.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        msg.web_link = "https://outlook.com/m2"
        msg.from_ = None
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.conversation_id = "conv-1"
        msg.internet_message_id = "imid-1"
        msg.conversation_index = "ci-1"

        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old-etag"
        existing.external_record_group_id = "folder-1"
        existing.version = 1

        connector._get_existing_record = AsyncMock(return_value=existing)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._extract_email_from_recipient = MagicMock(return_value="sender@test.com")

        result = await connector._process_single_email_with_folder(
            "org-1", "user@test.com", msg, "folder-1", "Inbox"
        )

        assert result is not None
        assert result.is_updated is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_existing_email_moved_to_different_folder(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        msg = MagicMock()
        msg.id = "m3"
        msg.subject = "Moved Email"
        msg.e_tag = "same-etag"
        msg.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        msg.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        msg.web_link = "https://outlook.com/m3"
        msg.from_ = None
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.conversation_id = "conv-1"
        msg.internet_message_id = "imid-1"
        msg.conversation_index = "ci-1"

        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "same-etag"
        existing.external_record_group_id = "old-folder-id"
        existing.version = 1

        connector._get_existing_record = AsyncMock(return_value=existing)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._extract_email_from_recipient = MagicMock(return_value="sender@test.com")

        result = await connector._process_single_email_with_folder(
            "org-1", "user@test.com", msg, "new-folder-id", "Sent Items"
        )

        assert result is not None
        assert result.is_updated is True
        assert result.metadata_changed is True


# ===========================================================================
# Deep Sync: _sync_users
# ===========================================================================


class TestSyncUsersDeep:

    @pytest.mark.asyncio
    async def test_sync_users_filters_active(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()

        enterprise_user = AppUser(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_id="su1", email="active@test.com", full_name="Active User",
        )
        connector._get_all_users_external = AsyncMock(return_value=[enterprise_user])

        active_user = MagicMock()
        active_user.email = "active@test.com"
        active_user.source_user_id = None
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])
        connector._populate_user_cache = AsyncMock()

        users = await connector._sync_users()
        assert len(users) == 1
        assert users[0].source_user_id == "su1"
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()


# ===========================================================================
# Deep Sync: _sync_user_groups (full flow, add groups)
# ===========================================================================


class TestSyncUserGroupsDeepFlow:

    @pytest.mark.asyncio
    async def test_processes_normal_groups_with_members(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector._user_cache = {"alice@test.com": "su-alice"}

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.description = "Team group"
        group.additional_data = {}
        group.mail = "team@test.com"
        group.mail_nickname = "team"

        member = MagicMock()
        member.mail = "alice@test.com"
        member.display_name = "Alice"
        member.id = "m1"
        member.user_principal_name = "alice@test.com"

        connector._get_all_microsoft_365_groups = AsyncMock(return_value=[group])
        connector._get_group_members = AsyncMock(return_value=[member])

        await connector._sync_user_groups()

        connector.data_entities_processor.on_new_user_groups.assert_awaited()
        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_groups_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()

        connector._get_all_microsoft_365_groups = AsyncMock(return_value=[])

        result = await connector._sync_user_groups()
        assert result == []


# ===========================================================================
# Deep Sync: _extract_email_permissions
# ===========================================================================


class TestExtractEmailPermissions:

    @pytest.mark.asyncio
    async def test_owner_found_in_recipients(self):
        connector = _make_connector()

        msg = MagicMock()
        msg.to_recipients = [MagicMock()]
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.from_ = None

        connector._extract_email_from_recipient = MagicMock(return_value="owner@test.com")

        perms = await connector._extract_email_permissions(msg, "rec-1", "owner@test.com")
        assert any(p.type == PermissionType.OWNER for p in perms)

    @pytest.mark.asyncio
    async def test_owner_not_in_recipients_added(self):
        connector = _make_connector()

        msg = MagicMock()
        msg.to_recipients = [MagicMock()]
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.from_ = None

        connector._extract_email_from_recipient = MagicMock(return_value="other@test.com")

        perms = await connector._extract_email_permissions(msg, "rec-1", "owner@test.com")
        owner_perms = [p for p in perms if p.type == PermissionType.OWNER]
        assert len(owner_perms) == 1
        assert owner_perms[0].email == "owner@test.com"

    @pytest.mark.asyncio
    async def test_empty_recipients(self):
        connector = _make_connector()

        msg = MagicMock()
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.from_ = None

        perms = await connector._extract_email_permissions(msg, "rec-1", "owner@test.com")
        # Owner should be added
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER


# ===========================================================================
# Deep Sync: _determine_folder_filter_strategy additional scenarios
# ===========================================================================


class TestDetermineFolderFilterStrategyDeep:

    def test_scenario4_selected_folders_with_custom_enabled(self):
        """Selected standard folders + custom enabled -> exclude non-selected standard."""
        connector = _make_connector()
        mock_filters = MagicMock()

        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = ["Inbox"]

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = True

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.FOLDERS:
                return folders_filter
            elif key == SyncFilterKey.CUSTOM_FOLDERS:
                return custom_filter
            return None

        mock_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_filters

        folder_names, mode = connector._determine_folder_filter_strategy()
        assert mode == "exclude"
        assert "Inbox" not in folder_names  # Inbox is selected, so not excluded

    def test_scenario5_all_standard_with_custom(self):
        """All standard folders + custom enabled -> sync everything."""
        connector = _make_connector()
        mock_filters = MagicMock()

        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = list(STANDARD_OUTLOOK_FOLDERS)

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = True

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.FOLDERS:
                return folders_filter
            elif key == SyncFilterKey.CUSTOM_FOLDERS:
                return custom_filter
            return None

        mock_filters.get = MagicMock(side_effect=get_filter)
        connector.sync_filters = mock_filters

        folder_names, mode = connector._determine_folder_filter_strategy()
        assert folder_names is None
        assert mode is None


# ===========================================================================
# Deep Sync: _get_all_folders_for_user
# ===========================================================================


class TestGetAllFoldersForUser:

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_all_folders_for_user("user-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_includes_nested_folders(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        # Use a dict (not MagicMock) to match actual API data shape
        folder = {"id": "f1", "display_name": "Inbox", "child_folder_count": 0}

        connector.external_outlook_client.users_list_mail_folders = AsyncMock(
            return_value=_make_graph_response(success=True, data={"value": [folder]})
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[])

        result = await connector._get_all_folders_for_user("user-1")
        assert len(result) == 1
        assert result[0].get('_is_top_level') is True


# ===========================================================================
# Deep Sync: _transform_folder_to_record_group
# ===========================================================================


class TestTransformFolderToRecordGroup:

    def test_successful_transform(self):
        connector = _make_connector()

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"
        folder._is_top_level = True
        folder.parent_folder_id = "parent-id"

        user = MagicMock()
        user.email = "u@test.com"

        result = connector._transform_folder_to_record_group(folder, user)
        assert result is not None
        assert result.name == "Inbox"
        assert result.group_type == RecordGroupType.MAILBOX

    def test_no_folder_id_returns_none(self):
        connector = _make_connector()

        folder = MagicMock(spec=[])
        user = MagicMock()
        user.email = "u@test.com"

        result = connector._transform_folder_to_record_group(folder, user)
        assert result is None


# ===========================================================================
# Deep Sync: _get_existing_record
# ===========================================================================


class TestGetExistingRecord:

    @pytest.mark.asyncio
    async def test_returns_record(self):
        connector = _make_connector()
        mock_record = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=mock_record)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        result = await connector._get_existing_record("org-1", "ext-1")
        assert result == mock_record

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB error"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        result = await connector._get_existing_record("org-1", "ext-1")
        assert result is None


# ===========================================================================
# Deep Sync: _augment_email_html_with_metadata
# ===========================================================================


class TestAugmentEmailHtml:

    def test_adds_metadata_div(self):
        connector = _make_connector()

        record = MagicMock()
        record.from_email = "sender@test.com"
        record.to_emails = ["to@test.com"]
        record.cc_emails = []
        record.bcc_emails = []
        record.subject = "Test Subject"

        result = connector._augment_email_html_with_metadata("<p>Hello</p>", record)
        assert "email-metadata" in result
        assert "sender@test.com" in result
        assert "<p>Hello</p>" in result

    def test_no_metadata_returns_original(self):
        connector = _make_connector()

        record = MagicMock()
        record.from_email = None
        record.to_emails = []
        record.cc_emails = []
        record.bcc_emails = []
        record.subject = None

        result = connector._augment_email_html_with_metadata("<p>Hello</p>", record)
        assert result == "<p>Hello</p>"
