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
