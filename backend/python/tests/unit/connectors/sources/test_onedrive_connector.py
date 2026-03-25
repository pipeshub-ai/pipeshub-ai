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
