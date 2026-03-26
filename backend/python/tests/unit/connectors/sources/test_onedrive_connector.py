"""Tests for app.connectors.sources.microsoft.onedrive.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.onedrive.connector import (
    OneDriveConnector,
    OneDriveCredentials,
    OneDriveSubscriptionManager,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_logger():
    return logging.getLogger("test.onedrive")


def _make_mock_deps():
    """Create mocked dependencies for the OneDrive connector."""
    logger = _make_mock_logger()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-123"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_record_metadata_update = AsyncMock()
    data_entities_processor.on_record_content_update = AsyncMock()
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.on_user_group_deleted = AsyncMock(return_value=True)
    data_entities_processor.on_user_group_member_removed = AsyncMock(return_value=True)
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.reindex_existing_records = AsyncMock()

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    """Create an OneDrive connector with mocked dependencies."""
    logger, dep, dsp, cs = _make_mock_deps()
    connector = OneDriveConnector(logger, dep, dsp, cs, "conn-onedrive-1")
    return connector


def _make_drive_item(
    item_id="item-1",
    name="document.pdf",
    is_folder=False,
    is_deleted=False,
    is_shared=False,
    e_tag="etag-1",
    c_tag="ctag-1",
    size=1024,
    mime_type="application/pdf",
    quick_xor_hash="hash123",
    web_url="https://onedrive.example.com/doc",
    drive_id="drive-1",
    parent_id="parent-1",
    parent_path="/root:/Documents",
    created=None,
    modified=None,
):
    """Create a mock DriveItem for testing."""
    now = datetime.now(timezone.utc)
    created = created or now
    modified = modified or now

    item = MagicMock()
    item.id = item_id
    item.name = name
    item.e_tag = e_tag
    item.c_tag = c_tag
    item.size = size
    item.web_url = web_url
    item.created_date_time = created
    item.last_modified_date_time = modified

    if is_folder:
        item.folder = MagicMock()
        item.file = None
    else:
        item.folder = None
        item.file = MagicMock()
        item.file.mime_type = mime_type
        item.file.hashes = MagicMock()
        item.file.hashes.quick_xor_hash = quick_xor_hash
        item.file.hashes.crc32_hash = None
        item.file.hashes.sha1_hash = None
        item.file.hashes.sha256_hash = None

    if is_deleted:
        item.deleted = MagicMock()
    else:
        item.deleted = None

    if is_shared:
        item.shared = MagicMock()
    else:
        item.shared = None

    item.parent_reference = MagicMock()
    item.parent_reference.drive_id = drive_id
    item.parent_reference.id = parent_id
    item.parent_reference.path = parent_path

    return item


# ===========================================================================
# OneDriveCredentials
# ===========================================================================


class TestOneDriveCredentials:

    def test_default_admin_consent(self):
        creds = OneDriveCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1"
        )
        assert creds.has_admin_consent is False

    def test_with_admin_consent(self):
        creds = OneDriveCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1",
            has_admin_consent=True,
        )
        assert creds.has_admin_consent is True


# ===========================================================================
# OneDriveConnector.init
# ===========================================================================


class TestOneDriveConnectorInit:

    @pytest.mark.asyncio
    async def test_init_returns_false_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)

        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_raises_on_missing_credentials(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value={"auth": {"tenantId": "t1"}}
        )

        with pytest.raises(ValueError, match="Incomplete OneDrive credentials"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "tenant-id",
                "clientId": "client-id",
                "clientSecret": "secret",
                "hasAdminConsent": True,
            }
        })

        with patch("app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.onedrive.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.onedrive.connector.MSGraphClient"):
            mock_cred_instance = AsyncMock()
            mock_cred_instance.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_instance

            result = await connector.init()
            assert result is True


# ===========================================================================
# OneDriveConnector._process_delta_item
# ===========================================================================


class TestProcessDeltaItem:

    @pytest.mark.asyncio
    async def test_deleted_item_returns_deleted_update(self):
        connector = _make_connector()
        item = _make_drive_item(is_deleted=True, item_id="deleted-1")

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.is_deleted is True
        assert result.external_record_id == "deleted-1"
        assert result.record is None

    @pytest.mark.asyncio
    async def test_new_file_item(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed.url")
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(name="report.docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.is_new is True
        assert result.is_deleted is False
        assert result.record is not None
        assert result.record.record_name == "report.docx"
        assert result.record.is_file is True

    @pytest.mark.asyncio
    async def test_folder_item(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(name="MyFolder", is_folder=True)

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.is_new is True
        assert result.record.is_file is False
        assert result.record.mime_type == MimeTypes.FOLDER.value

    @pytest.mark.asyncio
    async def test_file_without_extension_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(name="NOEXTENSION")

        result = await connector._process_delta_item(item)
        # File without extension returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_error_in_processing_returns_none(self):
        connector = _make_connector()
        # No msgraph_client set - will cause an error
        item = _make_drive_item()
        result = await connector._process_delta_item(item)
        assert result is None


# ===========================================================================
# OneDriveConnector._convert_to_permissions
# ===========================================================================


class TestConvertToPermissions:

    @pytest.mark.asyncio
    async def test_empty_permissions_list(self):
        connector = _make_connector()
        permissions = await connector._convert_to_permissions([])
        assert permissions == []

    @pytest.mark.asyncio
    async def test_user_permission_via_granted_to_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        user = MagicMock()
        user.id = "user-ext-1"
        user.additional_data = {"email": "user@example.com"}
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = user
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None

        permissions = await connector._convert_to_permissions([perm])
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.USER
        assert permissions[0].email == "user@example.com"
        assert permissions[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_group_permission_via_granted_to_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["write"]
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = None
        group = MagicMock()
        group.id = "group-ext-1"
        group.additional_data = {"email": "group@example.com"}
        perm.granted_to_v2.group = group
        perm.granted_to_identities_v2 = None
        perm.link = None

        permissions = await connector._convert_to_permissions([perm])
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.GROUP
        assert permissions[0].type == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_anonymous_link_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        link = MagicMock()
        link.scope = "anonymous"
        link.type = "read"
        perm.link = link

        permissions = await connector._convert_to_permissions([perm])
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ANYONE_WITH_LINK

    @pytest.mark.asyncio
    async def test_organization_link_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        link = MagicMock()
        link.scope = "organization"
        link.type = "edit"
        perm.link = link

        permissions = await connector._convert_to_permissions([perm])
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG


# ===========================================================================
# OneDriveConnector._permissions_equal
# ===========================================================================


class TestPermissionsEqual:

    def test_equal_permissions(self):
        connector = _make_connector()
        perms = [
            Permission(external_id="u1", email="a@b.com", type=PermissionType.READ, entity_type=EntityType.USER),
        ]
        assert connector._permissions_equal(perms, list(perms)) is True

    def test_unequal_permissions_different_length(self):
        connector = _make_connector()
        p1 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        p2 = []
        assert connector._permissions_equal(p1, p2) is False


# ===========================================================================
# OneDriveConnector._pass_date_filters / _pass_extension_filter
# ===========================================================================


class TestDateFilters:

    def test_folders_always_pass(self):
        connector = _make_connector()
        item = _make_drive_item(is_folder=True)
        assert connector._pass_date_filters(item) is True

    def test_no_filters_passes(self):
        connector = _make_connector()
        item = _make_drive_item()
        assert connector._pass_date_filters(item) is True


class TestExtensionFilter:

    def test_folders_always_pass(self):
        connector = _make_connector()
        item = _make_drive_item(is_folder=True)
        assert connector._pass_extension_filter(item) is True

    def test_no_filter_passes(self):
        connector = _make_connector()
        item = _make_drive_item(name="file.txt")
        assert connector._pass_extension_filter(item) is True


# ===========================================================================
# OneDriveConnector._parse_datetime
# ===========================================================================


class TestParseDatetime:

    def test_none_returns_none(self):
        connector = _make_connector()
        assert connector._parse_datetime(None) is None

    def test_datetime_object(self):
        connector = _make_connector()
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert isinstance(result, int)
        assert result > 0

    def test_iso_string(self):
        connector = _make_connector()
        result = connector._parse_datetime("2024-01-15T12:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid_string_returns_none(self):
        connector = _make_connector()
        result = connector._parse_datetime("not-a-date")
        assert result is None


# ===========================================================================
# OneDriveConnector._handle_record_updates
# ===========================================================================


class TestHandleRecordUpdates:

    @pytest.mark.asyncio
    async def test_handle_deletion(self):
        connector = _make_connector()

        mock_record = MagicMock()
        mock_record.id = "record-1"
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=mock_record)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="ext-deleted",
        )

        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_metadata_update(self):
        connector = _make_connector()
        mock_record = MagicMock()
        mock_record.record_name = "updated-file.pdf"

        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )

        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once_with(mock_record)

    @pytest.mark.asyncio
    async def test_handle_content_update(self):
        connector = _make_connector()
        mock_record = MagicMock()

        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=True,
            permissions_changed=False,
        )

        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once_with(mock_record)

    @pytest.mark.asyncio
    async def test_handle_permissions_update(self):
        connector = _make_connector()
        mock_record = MagicMock()
        new_perms = [Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)]

        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=True,
            new_permissions=new_perms,
        )

        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once_with(
            mock_record, new_perms
        )


# ===========================================================================
# OneDriveConnector.handle_group_create / handle_delete_group
# ===========================================================================


class TestGroupHandling:

    @pytest.mark.asyncio
    async def test_handle_group_create_success(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        member = MagicMock()
        member.id = "user-1"
        member.odata_type = "#microsoft.graph.user"
        member.mail = "user@example.com"
        member.user_principal_name = "user@example.com"
        member.display_name = "Test User"
        member.created_date_time = None
        member.additional_data = {}
        connector.msgraph_client.get_group_members = AsyncMock(return_value=[member])

        group = MagicMock()
        group.id = "grp-1"
        group.display_name = "Test Group"
        group.description = "Test"
        mock_dt = MagicMock()
        mock_dt.timestamp = MagicMock(return_value=1700000000)
        group.created_date_time = mock_dt

        result = await connector.handle_group_create(group)
        assert result is True
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_group_create_failure(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("API error"))

        group = MagicMock()
        group.id = "grp-fail"
        group.display_name = "Fail Group"

        result = await connector.handle_group_create(group)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_delete_group_success(self):
        connector = _make_connector()
        result = await connector.handle_delete_group("grp-to-delete")
        assert result is True
        connector.data_entities_processor.on_user_group_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_delete_group_failure(self):
        connector = _make_connector()
        connector.data_entities_processor.on_user_group_deleted = AsyncMock(return_value=False)
        result = await connector.handle_delete_group("grp-fail")
        assert result is False


# ===========================================================================
# OneDriveConnector.cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_closes_credential(self):
        connector = _make_connector()
        mock_credential = AsyncMock()
        mock_credential.close = AsyncMock()
        connector.credential = mock_credential
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()

        await connector.cleanup()

        mock_credential.close.assert_awaited_once()
        assert connector.client is None
        assert connector.msgraph_client is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_no_credential(self):
        connector = _make_connector()
        # No credential set - should not raise
        await connector.cleanup()


# ===========================================================================
# OneDriveConnector._user_has_onedrive
# ===========================================================================


class TestUserHasOneDrive:

    @pytest.mark.asyncio
    async def test_user_with_drive(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_drive = AsyncMock(return_value=MagicMock())

        result = await connector._user_has_onedrive("user-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_user_without_drive_resource_not_found(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        error = Exception("resourcenotfound")
        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=error)

        result = await connector._user_has_onedrive("user-no-drive")
        assert result is False


# ===========================================================================
# OneDriveSubscriptionManager
# ===========================================================================


class TestOneDriveSubscriptionManager:

    @pytest.mark.asyncio
    async def test_create_subscription_success(self):
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.id = "sub-123"
        mock_client.client.subscriptions.post = AsyncMock(return_value=mock_result)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock()
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)

        sub_id = await manager.create_subscription("user-1", "https://webhook.url")
        assert sub_id == "sub-123"
        assert manager.subscriptions["user-1"] == "sub-123"

    @pytest.mark.asyncio
    async def test_create_subscription_failure(self):
        mock_client = MagicMock()
        mock_client.client.subscriptions.post = AsyncMock(side_effect=Exception("API Error"))
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock()
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)

        sub_id = await manager.create_subscription("user-1", "https://webhook.url")
        assert sub_id is None

    @pytest.mark.asyncio
    async def test_delete_subscription_success(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.delete = AsyncMock()
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock()
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        manager.subscriptions["user-1"] = "sub-123"

        result = await manager.delete_subscription("sub-123")
        assert result is True
        assert "user-1" not in manager.subscriptions


# ===========================================================================
# Deep Sync: run_sync
# ===========================================================================


class TestRunSyncDeep:

    @pytest.mark.asyncio
    async def test_run_sync_full_workflow(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock(token="t"))
        connector.config = {"credentials": {"auth": {"tenantId": "t", "clientId": "c", "clientSecret": "s"}}}
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(return_value=[
            MagicMock(email="user@test.com", source_user_id="su1"),
        ])

        with patch("app.connectors.sources.microsoft.onedrive.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(MagicMock(), MagicMock())):
            connector._sync_user_groups = AsyncMock()
            connector._process_users_in_batches = AsyncMock()
            connector._detect_and_handle_permission_changes = AsyncMock()

            await connector.run_sync()

        connector.data_entities_processor.on_new_app_users.assert_called_once()
        connector._sync_user_groups.assert_awaited_once()
        connector._process_users_in_batches.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("Auth fail"))
        connector.config = {"credentials": {"auth": {}}}

        with pytest.raises(Exception):
            await connector.run_sync()


# ===========================================================================
# Deep Sync: _run_sync_with_yield (drive delta loop)
# ===========================================================================


class TestRunSyncWithYield:

    @pytest.mark.asyncio
    async def test_first_sync_creates_record_group(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        drive = MagicMock()
        drive.id = "drive-1"
        drive.web_url = "https://onedrive.com/u1"
        connector.msgraph_client.get_user_drive = AsyncMock(return_value=drive)
        connector.msgraph_client.get_user_info = AsyncMock(return_value={
            'display_name': 'Test User',
            'email': 'user@test.com',
        })

        # Return empty delta
        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            'drive_items': [],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta',
        })

        await connector._run_sync_with_yield("user-1")

        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delta_loop_processes_new_items(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/delta'}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        item = _make_drive_item(name="report.pdf")
        rec_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )

        # Mock _process_delta_items_generator
        async def fake_gen(items):
            for _ in items:
                yield (rec_update.record, [], rec_update)

        connector._process_delta_items_generator = fake_gen

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            'drive_items': [item],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        await connector._run_sync_with_yield("user-1")

        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_delta_loop_handles_deletions(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/delta'}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        del_update = RecordUpdate(
            record=None, external_record_id="del-1",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )

        async def fake_gen(items):
            yield (None, [], del_update)

        connector._process_delta_items_generator = fake_gen
        connector._handle_record_updates = AsyncMock()

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            'drive_items': [_make_drive_item(is_deleted=True)],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        await connector._run_sync_with_yield("user-1")

        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delta_loop_handles_updates(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/delta'}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        upd_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )

        async def fake_gen(items):
            yield (upd_update.record, [], upd_update)

        connector._process_delta_items_generator = fake_gen
        connector._handle_record_updates = AsyncMock()

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            'drive_items': [_make_drive_item()],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        await connector._run_sync_with_yield("user-1")

        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delta_loop_pagination(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/delta'}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        new_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )

        async def fake_gen(items):
            for _ in items:
                yield (new_update.record, [], new_update)

        connector._process_delta_items_generator = fake_gen

        page1 = {
            'drive_items': [_make_drive_item(item_id="i1", name="a.pdf")],
            'next_link': 'https://graph.microsoft.com/v1.0/next-page',
            'delta_link': None,
        }
        page2 = {
            'drive_items': [_make_drive_item(item_id="i2", name="b.pdf")],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        }
        connector.msgraph_client.get_delta_response = AsyncMock(side_effect=[page1, page2])

        await connector._run_sync_with_yield("user-1")

        assert connector.msgraph_client.get_delta_response.await_count == 2

    @pytest.mark.asyncio
    async def test_delta_loop_batching(self):
        connector = _make_connector()
        connector.batch_size = 2
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/delta'}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        new_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )

        async def fake_gen(items):
            for _ in items:
                yield (MagicMock(), [], new_update)

        connector._process_delta_items_generator = fake_gen

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            'drive_items': [_make_drive_item(item_id=f"i{i}", name=f"f{i}.pdf") for i in range(5)],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        await connector._run_sync_with_yield("user-1")

        # 5 items, batch_size=2 -> 3 batches: 2, 2, 1
        assert connector.data_entities_processor.on_new_records.await_count == 3


# ===========================================================================
# Deep Sync: _process_delta_items_generator
# ===========================================================================


class TestProcessDeltaItemsGenerator:

    @pytest.mark.asyncio
    async def test_deleted_item_yields_none_record(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()

        del_update = RecordUpdate(
            record=None, external_record_id="del-1",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        connector._process_delta_item = AsyncMock(return_value=del_update)

        results = []
        async for r in connector._process_delta_items_generator([_make_drive_item(is_deleted=True)]):
            results.append(r)

        assert len(results) == 1
        assert results[0][0] is None

    @pytest.mark.asyncio
    async def test_new_file_yields_record(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_record = MagicMock()
        mock_record.is_shared = False
        mock_record.indexing_status = None
        new_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        connector._process_delta_item = AsyncMock(return_value=new_update)

        results = []
        async for r in connector._process_delta_items_generator([_make_drive_item()]):
            results.append(r)

        assert len(results) == 1
        assert results[0][0] == mock_record

    @pytest.mark.asyncio
    async def test_files_indexing_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        mock_record = MagicMock()
        mock_record.is_shared = False
        mock_record.indexing_status = None
        new_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        connector._process_delta_item = AsyncMock(return_value=new_update)

        results = []
        async for r in connector._process_delta_items_generator([_make_drive_item()]):
            results.append(r)

        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_processing_error_continues(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_record = MagicMock()
        mock_record.is_shared = False
        mock_record.indexing_status = None
        good_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )

        connector._process_delta_item = AsyncMock(side_effect=[Exception("err"), good_update])

        results = []
        async for r in connector._process_delta_items_generator([_make_drive_item(), _make_drive_item()]):
            results.append(r)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_none_update_skipped(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()

        connector._process_delta_item = AsyncMock(return_value=None)

        results = []
        async for r in connector._process_delta_items_generator([_make_drive_item()]):
            results.append(r)

        assert len(results) == 0


# ===========================================================================
# Deep Sync: _process_users_in_batches
# ===========================================================================


class TestProcessUsersInBatches:

    @pytest.mark.asyncio
    async def test_filters_active_users_with_onedrive(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.max_concurrent_batches = 10

        active_user = MagicMock()
        active_user.email = "user@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])

        user_with_drive = MagicMock()
        user_with_drive.email = "user@test.com"
        user_with_drive.source_user_id = "su1"

        user_no_drive = MagicMock()
        user_no_drive.email = "nodrive@test.com"
        user_no_drive.source_user_id = "su2"

        connector._user_has_onedrive = AsyncMock(side_effect=[True, False])
        connector._run_sync_with_yield = AsyncMock()

        await connector._process_users_in_batches([user_with_drive, user_no_drive])

        connector._run_sync_with_yield.assert_awaited_once_with("su1")

    @pytest.mark.asyncio
    async def test_no_active_users_skips(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        connector._run_sync_with_yield = AsyncMock()

        await connector._process_users_in_batches([MagicMock(email="u@t.com")])

        connector._run_sync_with_yield.assert_not_awaited()


# ===========================================================================
# Deep Sync: _sync_user_groups (initial and delta)
# ===========================================================================


class TestSyncUserGroupsDeep:

    @pytest.mark.asyncio
    async def test_initial_full_sync(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector._get_initial_delta_link = AsyncMock(
            return_value="https://graph.microsoft.com/v1.0/groups/delta?token=abc"
        )
        connector._perform_initial_full_sync = AsyncMock()

        await connector._sync_user_groups()

        connector._get_initial_delta_link.assert_awaited_once()
        connector._perform_initial_full_sync.assert_awaited_once()
        connector.user_group_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_delta_sync(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(
            return_value={'deltaLink': 'https://graph.microsoft.com/v1.0/groups/delta?token=xyz'}
        )
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector._perform_delta_sync = AsyncMock()

        await connector._sync_user_groups()

        connector._perform_delta_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initial_sync_no_delta_link_obtained(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector._get_initial_delta_link = AsyncMock(return_value=None)
        connector._perform_initial_full_sync = AsyncMock()

        await connector._sync_user_groups()

        # Should not save sync point when no delta link
        connector.user_group_sync_point.update_sync_point.assert_not_awaited()


# ===========================================================================
# Deep Sync: _get_initial_delta_link
# ===========================================================================


class TestGetInitialDeltaLink:

    @pytest.mark.asyncio
    async def test_obtains_delta_link(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            'groups': [],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/groups/delta?token=final',
        })

        result = await connector._get_initial_delta_link()
        assert result is not None
        assert 'delta?token=final' in result

    @pytest.mark.asyncio
    async def test_follows_next_links(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {'groups': [MagicMock()], 'next_link': 'https://graph.microsoft.com/v1.0/next', 'delta_link': None},
            {'groups': [], 'next_link': None, 'delta_link': 'https://graph.microsoft.com/v1.0/delta-final'},
        ])

        result = await connector._get_initial_delta_link()
        assert result is not None
        assert connector.msgraph_client.get_groups_delta_response.await_count == 2

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=Exception("API error"))

        result = await connector._get_initial_delta_link()
        assert result is None


# ===========================================================================
# Deep Sync: _perform_initial_full_sync
# ===========================================================================


class TestPerformInitialFullSync:

    @pytest.mark.asyncio
    async def test_processes_all_groups(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        group1 = MagicMock()
        group1.id = "g1"
        group1.display_name = "Group 1"
        group2 = MagicMock()
        group2.id = "g2"
        group2.display_name = "Group 2"

        connector.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group1, group2])

        user_group = AppUserGroup(
            source_user_group_id="g1", app_name=Connectors.ONEDRIVE,
            connector_id="conn-1", name="Group 1",
        )
        connector._process_single_group = AsyncMock(return_value=(user_group, []))

        await connector._perform_initial_full_sync()

        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_group_errors(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Fail Group"

        connector.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group])
        connector._process_single_group = AsyncMock(side_effect=Exception("API fail"))

        # Should not raise - exceptions handled
        await connector._perform_initial_full_sync()

        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()


# ===========================================================================
# Deep Sync: _perform_delta_sync
# ===========================================================================


class TestPerformDeltaSync:

    @pytest.mark.asyncio
    async def test_handles_group_deletion(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        deleted_group = MagicMock()
        deleted_group.id = "g-del"
        deleted_group.additional_data = {"@removed": {"reason": "deleted"}}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            'groups': [deleted_group],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        connector.handle_delete_group = AsyncMock(return_value=True)

        await connector._perform_delta_sync(
            "https://graph.microsoft.com/v1.0/groups/delta?token=xyz",
            "sync-key-1"
        )

        connector.handle_delete_group.assert_awaited_once_with("g-del")

    @pytest.mark.asyncio
    async def test_handles_group_add_update(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        new_group = MagicMock()
        new_group.id = "g-new"
        new_group.display_name = "New Group"
        new_group.additional_data = {}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            'groups': [new_group],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        connector.handle_group_create = AsyncMock(return_value=True)

        await connector._perform_delta_sync(
            "https://graph.microsoft.com/v1.0/groups/delta?token=xyz",
            "sync-key-1"
        )

        connector.handle_group_create.assert_awaited_once_with(new_group)

    @pytest.mark.asyncio
    async def test_handles_member_changes(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group"
        group.additional_data = {
            'members@delta': [{'id': 'user-1'}, {'id': 'user-2', '@removed': {'reason': 'deleted'}}]
        }

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            'groups': [group],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final',
        })

        connector.handle_group_create = AsyncMock(return_value=True)
        connector._process_member_change = AsyncMock()

        await connector._perform_delta_sync(
            "https://graph.microsoft.com/v1.0/groups/delta?token=xyz",
            "sync-key-1"
        )

        assert connector._process_member_change.await_count == 2

    @pytest.mark.asyncio
    async def test_delta_pagination(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        page1_group = MagicMock()
        page1_group.id = "g1"
        page1_group.display_name = "G1"
        page1_group.additional_data = {}

        page2_group = MagicMock()
        page2_group.id = "g2"
        page2_group.display_name = "G2"
        page2_group.additional_data = {}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {'groups': [page1_group], 'next_link': 'https://next', 'delta_link': None},
            {'groups': [page2_group], 'next_link': None, 'delta_link': 'https://final'},
        ])

        connector.handle_group_create = AsyncMock(return_value=True)

        await connector._perform_delta_sync("https://start", "sync-key-1")

        assert connector.handle_group_create.await_count == 2


# ===========================================================================
# Deep Sync: _process_member_change
# ===========================================================================


class TestProcessMemberChange:

    @pytest.mark.asyncio
    async def test_member_removal(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        member_change = {'id': 'user-1', '@removed': {'reason': 'deleted'}}

        await connector._process_member_change("g1", member_change)

        connector.data_entities_processor.on_user_group_member_removed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_member_addition(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        member_change = {'id': 'user-1'}

        await connector._process_member_change("g1", member_change)

        # Member addition is just logged, no explicit processor call
        connector.data_entities_processor.on_user_group_member_removed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_email_skips(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value=None)

        member_change = {'id': 'user-1', '@removed': {'reason': 'deleted'}}

        await connector._process_member_change("g1", member_change)

        connector.data_entities_processor.on_user_group_member_removed.assert_not_awaited()


# ===========================================================================
# Deep Sync: _process_single_group
# ===========================================================================


class TestProcessSingleGroup:

    @pytest.mark.asyncio
    async def test_group_with_user_members(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        member = MagicMock()
        member.id = "m1"
        member.odata_type = "#microsoft.graph.user"
        member.mail = "user@test.com"
        member.user_principal_name = "user@test.com"
        member.display_name = "Test User"
        member.created_date_time = None
        member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[member])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.description = "Team desc"
        group.created_date_time = MagicMock()
        group.created_date_time.timestamp = MagicMock(return_value=1700000000)

        result = await connector._process_single_group(group)
        assert result is not None
        user_group, app_users = result
        assert user_group.name == "Team"
        assert len(app_users) == 1

    @pytest.mark.asyncio
    async def test_group_with_nested_group(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        nested_group = MagicMock()
        nested_group.id = "ng1"
        nested_group.odata_type = "#microsoft.graph.group"
        nested_group.display_name = "Nested Group"
        nested_group.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[nested_group])

        nested_user = AppUser(
            app_name=Connectors.ONEDRIVE, connector_id="conn-1",
            source_user_id="nu1", email="nested@test.com", full_name="Nested User",
        )
        connector._get_users_from_nested_group = AsyncMock(return_value=[nested_user])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Parent Group"
        group.description = None
        group.created_date_time = None

        result = await connector._process_single_group(group)
        assert result is not None
        _, app_users = result
        assert len(app_users) == 1
        assert app_users[0].email == "nested@test.com"

    @pytest.mark.asyncio
    async def test_group_error_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("API error"))

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Fail Group"

        result = await connector._process_single_group(group)
        assert result is None


# ===========================================================================
# Deep Sync: _reinitialize_credential_if_needed
# ===========================================================================


class TestReinitializeCredential:

    @pytest.mark.asyncio
    async def test_valid_credential_no_reinit(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock(token="valid"))
        connector.config = {"credentials": {"auth": {"tenantId": "t", "clientId": "c", "clientSecret": "s"}}}

        await connector._reinitialize_credential_if_needed()

        # Credential should not have been replaced
        connector.credential.get_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expired_credential_reinitializes(self):
        connector = _make_connector()
        old_cred = AsyncMock()
        old_cred.get_token = AsyncMock(side_effect=Exception("transport closed"))
        old_cred.close = AsyncMock()
        connector.credential = old_cred
        connector.config = {"credentials": {"auth": {"tenantId": "t", "clientId": "c", "clientSecret": "s"}}}

        with patch("app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential") as mock_cls, \
             patch("app.connectors.sources.microsoft.onedrive.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.onedrive.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock(return_value=MagicMock(token="new"))
            mock_cls.return_value = new_cred

            await connector._reinitialize_credential_if_needed()

            assert connector.credential == new_cred

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        connector = _make_connector()
        old_cred = AsyncMock()
        old_cred.get_token = AsyncMock(side_effect=Exception("expired"))
        old_cred.close = AsyncMock()
        connector.credential = old_cred
        connector.config = {"credentials": {"auth": {}}}

        with pytest.raises(ValueError, match="credentials not found"):
            await connector._reinitialize_credential_if_needed()


# ===========================================================================
# Deep Sync: _update_folder_children_permissions
# ===========================================================================


class TestUpdateFolderChildrenPermissions:

    @pytest.mark.asyncio
    async def test_updates_children_recursively(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        child_file = MagicMock()
        child_file.id = "child-file-1"
        child_file.folder = None

        child_folder = MagicMock()
        child_folder.id = "child-folder-1"
        child_folder.folder = MagicMock()

        connector.msgraph_client.list_folder_children = AsyncMock(
            side_effect=[[child_file, child_folder], []]
        )
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])
        connector._convert_to_permissions = AsyncMock(return_value=[])

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=MagicMock())
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._update_folder_children_permissions("drive-1", "folder-1")

        assert connector.msgraph_client.list_folder_children.await_count == 2

    @pytest.mark.asyncio
    async def test_child_error_continues(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        child = MagicMock()
        child.id = "child-1"
        child.folder = None

        connector.msgraph_client.list_folder_children = AsyncMock(return_value=[child])
        connector.msgraph_client.get_file_permission = AsyncMock(side_effect=Exception("API error"))

        # Should not raise
        await connector._update_folder_children_permissions("drive-1", "folder-1")


# ===========================================================================
# Deep Sync: _create_app_user_from_member
# ===========================================================================


class TestCreateAppUserFromMember:

    def test_valid_member(self):
        connector = _make_connector()

        member = MagicMock()
        member.id = "m1"
        member.mail = "user@test.com"
        member.display_name = "User"
        member.created_date_time = None

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.email == "user@test.com"

    def test_no_email_returns_none(self):
        connector = _make_connector()

        member = MagicMock()
        member.id = "m1"
        member.mail = None
        member.user_principal_name = None

        result = connector._create_app_user_from_member(member)
        assert result is None

    def test_falls_back_to_upn(self):
        connector = _make_connector()

        member = MagicMock()
        member.id = "m1"
        member.mail = None
        member.user_principal_name = "user@test.onmicrosoft.com"
        member.display_name = "User"
        member.created_date_time = None

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.email == "user@test.onmicrosoft.com"


# ===========================================================================
# Deep Sync: _get_users_from_nested_group
# ===========================================================================


class TestGetUsersFromNestedGroup:

    @pytest.mark.asyncio
    async def test_fetches_nested_users(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        nested_member = MagicMock()
        nested_member.id = "nm1"
        nested_member.odata_type = "#microsoft.graph.user"
        nested_member.mail = "nested@test.com"
        nested_member.display_name = "Nested"
        nested_member.created_date_time = None
        nested_member.user_principal_name = "nested@test.com"
        nested_member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[nested_member])

        nested_group = MagicMock()
        nested_group.id = "ng1"
        nested_group.display_name = "Nested Group"

        users = await connector._get_users_from_nested_group(nested_group)
        assert len(users) == 1
        assert users[0].email == "nested@test.com"

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("API error"))

        nested_group = MagicMock()
        nested_group.id = "ng1"
        nested_group.display_name = "Fail Group"

        users = await connector._get_users_from_nested_group(nested_group)
        assert users == []


# ===========================================================================
# Deep Sync: reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records_noop(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector = _make_connector()
        connector.msgraph_client = None

        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])
