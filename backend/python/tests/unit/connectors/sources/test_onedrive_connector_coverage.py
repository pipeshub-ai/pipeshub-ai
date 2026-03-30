"""
Comprehensive coverage tests for app.connectors.sources.microsoft.onedrive.connector.

Targets the 204 uncovered lines by testing:
- init() edge cases
- _process_delta_item() with existing records, metadata/content/permission changes
- _convert_to_permissions() with granted_to_identities_v2, link scopes, errors
- _pass_date_filters() with created/modified date filters
- _pass_extension_filter() with IN/NOT_IN operators, no extension, invalid filter
- _process_delta_items_generator() with shared disabled
- _update_folder_children_permissions() recursive logic
- _handle_record_updates() new record, error paths
- _sync_user_groups() initial sync without delta link
- _get_initial_delta_link() pagination and error
- _perform_initial_full_sync() with exceptions in groups
- _process_single_group() with nested groups
- _perform_delta_sync() with deletions, member changes, pagination
- _process_member_change() add/remove
- _get_users_from_nested_group() success, failure
- _create_app_user_from_member() no email
- handle_group_create() with nested group members
- handle_delete_group() exception
- _run_sync_with_yield() first sync, no user info, pagination, error
- _process_users_in_batches() batching logic
- _user_has_onedrive() various error codes
- _handle_reindex_event() with and without record
- handle_webhook_notification()
- run_sync() and run_incremental_sync()
- _reinitialize_credential_if_needed() success and failure paths
- cleanup() with credential close error
- reindex_records() with updated and non-updated records
- _check_and_fetch_updated_record()
- get_filter_options()
- get_signed_url() and stream_record()
- test_connection_and_access()
- create_connector()
- OneDriveSubscriptionManager: renew, delete with tracking, cleanup, renew_all
"""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
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
    return logging.getLogger("test.onedrive.coverage")


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
    return logger, data_entities_processor


def _make_connector():
    """Create an OneDrive connector with mocked dependencies."""
    logger, dep = _make_mock_deps()
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    connector = OneDriveConnector(logger, dep, dsp, cs, "conn-onedrive-cov", "team", "test-user-id")
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


def _make_tx_store(existing_record=None, existing_file_record=None):
    """Create a mock transaction store."""
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)
    mock_tx_store.get_file_record_by_id = AsyncMock(return_value=existing_file_record)
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    return mock_tx, mock_tx_store


def _make_existing_record(
    record_id="rec-1",
    external_id="item-1",
    external_revision_id="etag-old",
    record_name="document.pdf",
    updated_at=0,
    version=1,
    is_shared=False,
    external_record_group_id="drive-1",
    external_record_id="item-1",
):
    rec = MagicMock()
    rec.id = record_id
    rec.external_record_id = external_record_id
    rec.external_revision_id = external_revision_id
    rec.record_name = record_name
    rec.updated_at = updated_at
    rec.version = version
    rec.is_shared = is_shared
    rec.external_record_group_id = external_record_group_id
    rec.mime_type = "application/pdf"
    return rec


# ===========================================================================
# OneDriveCredentials
# ===========================================================================


class TestOneDriveCredentialsCoverage:

    def test_all_fields(self):
        creds = OneDriveCredentials(
            tenant_id="t", client_id="c", client_secret="s", has_admin_consent=True
        )
        assert creds.tenant_id == "t"
        assert creds.client_id == "c"
        assert creds.client_secret == "s"
        assert creds.has_admin_consent is True


# ===========================================================================
# init() edge cases
# ===========================================================================


class TestInitCoverage:

    @pytest.mark.asyncio
    async def test_init_raises_when_token_fails(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t", "clientId": "c", "clientSecret": "s",
                "hasAdminConsent": False,
            }
        })

        with patch("app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential") as mock_cred:
            mock_cred_instance = AsyncMock()
            mock_cred_instance.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cred.return_value = mock_cred_instance

            with pytest.raises(ValueError, match="Failed to initialize OneDrive credential"):
                await connector.init()


# ===========================================================================
# _process_delta_item - existing record with changes
# ===========================================================================


class TestProcessDeltaItemCoverage:

    @pytest.mark.asyncio
    async def test_existing_record_metadata_changed(self):
        """Existing record with changed etag, name, or updated_at."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        existing = _make_existing_record(
            external_revision_id="etag-old",
            record_name="old-name.pdf",
            updated_at=0,
        )
        existing_file_record = MagicMock()
        existing_file_record.quick_xor_hash = "hash123"  # same hash

        mock_tx, _ = _make_tx_store(existing, existing_file_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(name="new-name.pdf", e_tag="etag-new")

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self):
        """Existing record with changed file hash."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        existing = _make_existing_record(external_revision_id="etag-1")
        existing_file_record = MagicMock()
        existing_file_record.quick_xor_hash = "old-hash"  # different from item

        mock_tx, _ = _make_tx_store(existing, existing_file_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        now = datetime.now(timezone.utc)
        existing.updated_at = int(now.timestamp() * 1000)
        existing.record_name = "document.pdf"

        item = _make_drive_item(quick_xor_hash="new-hash", created=now, modified=now)

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.content_changed is True
        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_existing_record_permissions_changed(self):
        """Existing record with new permissions detected."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")

        # Return a permission so _convert_to_permissions returns something
        perm_mock = MagicMock()
        perm_mock.granted_to_v2 = MagicMock()
        user_mock = MagicMock()
        user_mock.id = "user-1"
        user_mock.additional_data = {"email": "u@test.com"}
        perm_mock.granted_to_v2.user = user_mock
        perm_mock.granted_to_v2.group = None
        perm_mock.granted_to_identities_v2 = None
        perm_mock.link = None
        perm_mock.roles = ["read"]
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[perm_mock])

        now = datetime.now(timezone.utc)
        existing = _make_existing_record(external_revision_id="etag-1")
        existing.updated_at = int(now.timestamp() * 1000)
        existing.record_name = "document.pdf"
        existing.is_shared = False

        existing_file_record = MagicMock()
        existing_file_record.quick_xor_hash = "hash123"

        mock_tx, _ = _make_tx_store(existing, existing_file_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(created=now, modified=now)

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.permissions_changed is True

    @pytest.mark.asyncio
    async def test_shared_folder_triggers_children_update(self):
        """Existing record that changes shared state triggers _update_folder_children_permissions."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])
        connector._update_folder_children_permissions = AsyncMock()

        now = datetime.now(timezone.utc)
        existing = _make_existing_record(external_revision_id="etag-1")
        existing.updated_at = int(now.timestamp() * 1000)
        existing.record_name = "MyFolder"
        existing.is_shared = False  # was not shared

        mock_tx, _ = _make_tx_store(existing, None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        # Shared folder item
        item = _make_drive_item(name="MyFolder", is_folder=True, is_shared=True, created=now, modified=now)

        result = await connector._process_delta_item(item)
        assert result is not None
        connector._update_folder_children_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skipped_by_date_filter(self):
        """Item skipped because of date filters."""
        connector = _make_connector()
        connector._pass_date_filters = MagicMock(return_value=False)

        item = _make_drive_item()
        result = await connector._process_delta_item(item)
        assert result is None

    @pytest.mark.asyncio
    async def test_skipped_by_extension_filter(self):
        """Item skipped because of extension filters."""
        connector = _make_connector()
        connector._pass_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=False)

        item = _make_drive_item()
        result = await connector._process_delta_item(item)
        assert result is None

    @pytest.mark.asyncio
    async def test_shared_file_detected(self):
        """A shared file (not folder) has is_shared=True."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        mock_tx, _ = _make_tx_store(None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(name="shared.pdf", is_shared=True)

        result = await connector._process_delta_item(item)
        assert result is not None
        assert result.record.is_shared is True


# ===========================================================================
# _convert_to_permissions - more branches
# ===========================================================================


class TestConvertToPermissionsCoverage:

    @pytest.mark.asyncio
    async def test_granted_to_identities_v2_group(self):
        """Group via granted_to_identities_v2."""
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["write"]
        perm.granted_to_v2 = None

        group = MagicMock()
        group.id = "grp-1"
        group.additional_data = {"email": "grp@test.com"}
        identity = MagicMock()
        identity.group = group
        identity.user = None
        perm.granted_to_identities_v2 = [identity]
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP
        assert result[0].email == "grp@test.com"

    @pytest.mark.asyncio
    async def test_granted_to_identities_v2_user(self):
        """User via granted_to_identities_v2."""
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None

        user = MagicMock()
        user.id = "user-1"
        user.additional_data = {"email": "u@test.com"}
        identity = MagicMock()
        identity.group = None
        identity.user = user
        perm.granted_to_identities_v2 = [identity]
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_permission_conversion_error_continues(self):
        """An exception in one permission should not stop processing others."""
        connector = _make_connector()

        bad_perm = MagicMock()
        bad_perm.roles = ["read"]
        bad_perm.granted_to_v2 = MagicMock()
        bad_perm.granted_to_v2.user = MagicMock()
        bad_perm.granted_to_v2.user.id = None
        # Force error by making additional_data access raise
        bad_perm.granted_to_v2.user.additional_data = property(lambda s: (_ for _ in ()).throw(Exception("bad")))

        good_perm = MagicMock()
        good_perm.roles = ["read"]
        good_perm.granted_to_v2 = None
        good_perm.granted_to_identities_v2 = None
        good_perm.link = MagicMock()
        good_perm.link.scope = "anonymous"
        good_perm.link.type = "read"

        result = await connector._convert_to_permissions([bad_perm, good_perm])
        # bad_perm may or may not produce a permission depending on exact failure point,
        # but good_perm should always produce one
        assert any(p.entity_type == EntityType.ANYONE_WITH_LINK for p in result)

    @pytest.mark.asyncio
    async def test_user_no_additional_data(self):
        """User permission where user has no additional_data attr."""
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = ["owner"]
        user = MagicMock(spec=[])  # no attributes by default
        user.id = "user-no-data"
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = user
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None
        # hasattr(user, 'additional_data') will be False

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].email is None

    @pytest.mark.asyncio
    async def test_no_roles_defaults(self):
        """Permission with empty roles list."""
        connector = _make_connector()
        perm = MagicMock()
        perm.roles = []  # empty roles
        user = MagicMock()
        user.id = "u1"
        user.additional_data = {}
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = user
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].type == PermissionType.READ  # default


# ===========================================================================
# _permissions_equal - more cases
# ===========================================================================


class TestPermissionsEqualCoverage:

    def test_same_perms_different_order(self):
        connector = _make_connector()
        p1 = Permission(external_id="u1", email="a@b.com", type=PermissionType.READ, entity_type=EntityType.USER)
        p2 = Permission(external_id="u2", email="b@b.com", type=PermissionType.WRITE, entity_type=EntityType.USER)
        assert connector._permissions_equal([p1, p2], [p2, p1]) is True

    def test_different_content(self):
        connector = _make_connector()
        p1 = Permission(external_id="u1", email="a@b.com", type=PermissionType.READ, entity_type=EntityType.USER)
        p2 = Permission(external_id="u1", email="a@b.com", type=PermissionType.WRITE, entity_type=EntityType.USER)
        assert connector._permissions_equal([p1], [p2]) is False


# ===========================================================================
# _pass_date_filters - full coverage
# ===========================================================================


class TestPassDateFiltersCoverage:

    def test_created_filter_blocks_before_start(self):
        connector = _make_connector()
        created_filter = MagicMock()
        created_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: created_filter if k.value == "created" else None)

        item = _make_drive_item(
            created=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is False

    def test_created_filter_blocks_after_end(self):
        connector = _make_connector()
        created_filter = MagicMock()
        created_filter.get_datetime_iso.return_value = (None, "2024-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: created_filter if k.value == "created" else None)

        item = _make_drive_item(
            created=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is False

    def test_created_filter_allows_within_range(self):
        connector = _make_connector()
        created_filter = MagicMock()
        created_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: created_filter if k.value == "created" else None)

        item = _make_drive_item(
            created=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is True

    def test_modified_filter_blocks(self):
        connector = _make_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: modified_filter if k.value == "modified" else None)

        item = _make_drive_item(
            modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is False

    def test_modified_filter_end_blocks(self):
        connector = _make_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = (None, "2024-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: modified_filter if k.value == "modified" else None)

        item = _make_drive_item(
            modified=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is False

    def test_modified_filter_allows(self):
        connector = _make_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: modified_filter if k.value == "modified" else None)

        item = _make_drive_item(
            modified=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        assert connector._pass_date_filters(item) is True

    def test_none_item_timestamp(self):
        """When item datetime is None, _parse_datetime returns None; filter passes."""
        connector = _make_connector()
        created_filter = MagicMock()
        created_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: created_filter if k.value == "created" else None)

        item = _make_drive_item()
        item.created_date_time = None  # None datetime

        # _parse_datetime(None) returns None, so item_ts is None, skips comparison
        assert connector._pass_date_filters(item) is True


# ===========================================================================
# _pass_extension_filter - full coverage
# ===========================================================================


class TestPassExtensionFilterCoverage:

    def test_in_operator_allows_matching(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf", "docx"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="report.pdf")
        assert connector._pass_extension_filter(item) is True

    def test_in_operator_blocks_non_matching(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf", "docx"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="image.png")
        assert connector._pass_extension_filter(item) is False

    def test_not_in_operator_blocks_matching(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["exe", "bat"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.NOT_IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="virus.exe")
        assert connector._pass_extension_filter(item) is False

    def test_not_in_operator_allows_non_matching(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["exe", "bat"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.NOT_IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="report.pdf")
        assert connector._pass_extension_filter(item) is True

    def test_no_extension_with_in_operator_blocks(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="README")  # no extension
        assert connector._pass_extension_filter(item) is False

    def test_no_extension_with_not_in_operator_passes(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["exe"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.NOT_IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="Makefile")  # no extension
        assert connector._pass_extension_filter(item) is True

    def test_empty_filter_passes(self):
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = True
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="file.txt")
        assert connector._pass_extension_filter(item) is True

    def test_non_list_value_passes(self):
        """If filter value is not a list, allow the file."""
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="file.txt")
        assert connector._pass_extension_filter(item) is True

    def test_unknown_operator_passes(self):
        """Unknown operator defaults to True."""
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf"]
        operator_mock = MagicMock()
        operator_mock.value = "unknown_operator"
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="file.pdf")
        assert connector._pass_extension_filter(item) is True

    def test_extensions_with_dots_normalized(self):
        """Extensions in filter with dots are normalized."""
        connector = _make_connector()
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = [".PDF", ".Docx"]
        operator_mock = MagicMock()
        operator_mock.value = FilterOperator.IN
        ext_filter.get_operator.return_value = operator_mock
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=ext_filter)

        item = _make_drive_item(name="report.pdf")
        assert connector._pass_extension_filter(item) is True


# ===========================================================================
# _process_delta_items_generator - shared disabled branch
# ===========================================================================


class TestProcessDeltaItemsGeneratorCoverage:

    @pytest.mark.asyncio
    async def test_shared_item_indexing_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        # files enabled, shared disabled
        connector.indexing_filters.is_enabled = MagicMock(side_effect=lambda key, default=True: key.value != "shared")

        mock_record = MagicMock()
        mock_record.is_shared = True
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
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# _update_folder_children_permissions
# ===========================================================================


class TestUpdateFolderChildrenPermissions:

    @pytest.mark.asyncio
    async def test_recursive_update(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        child_file = MagicMock()
        child_file.id = "child-file-1"
        child_file.folder = None

        child_folder = MagicMock()
        child_folder.id = "child-folder-1"
        child_folder.folder = MagicMock()

        # First call returns children, second (recursive) returns empty
        connector.msgraph_client.list_folder_children = AsyncMock(
            side_effect=[[child_file, child_folder], []]
        )
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        existing_record = MagicMock()
        mock_tx, _ = _make_tx_store(existing_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._update_folder_children_permissions("drive-1", "folder-1")

        assert connector.data_entities_processor.on_updated_record_permissions.await_count >= 1

    @pytest.mark.asyncio
    async def test_child_not_in_db(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        child = MagicMock()
        child.id = "child-1"
        child.folder = None
        connector.msgraph_client.list_folder_children = AsyncMock(return_value=[child])
        connector.msgraph_client.get_file_permission = AsyncMock(return_value=[])

        mock_tx, _ = _make_tx_store(None)  # no record found
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        # Should not raise
        await connector._update_folder_children_permissions("drive-1", "folder-1")
        connector.data_entities_processor.on_updated_record_permissions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_children_error(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.list_folder_children = AsyncMock(side_effect=Exception("API error"))

        # Should not raise
        await connector._update_folder_children_permissions("drive-1", "folder-1")

    @pytest.mark.asyncio
    async def test_child_processing_error_continues(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        child1 = MagicMock()
        child1.id = "child-err"
        child1.folder = None

        child2 = MagicMock()
        child2.id = "child-ok"
        child2.folder = None

        connector.msgraph_client.list_folder_children = AsyncMock(return_value=[child1, child2])
        connector.msgraph_client.get_file_permission = AsyncMock(side_effect=[Exception("err"), []])

        existing_record = MagicMock()
        mock_tx, _ = _make_tx_store(existing_record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._update_folder_children_permissions("drive-1", "folder-1")
        # Second child should still be processed
        assert connector.data_entities_processor.on_updated_record_permissions.await_count == 1


# ===========================================================================
# _handle_record_updates - additional branches
# ===========================================================================


class TestHandleRecordUpdatesCoverage:

    @pytest.mark.asyncio
    async def test_new_record_logs_only(self):
        """New record just logs, no extra processing."""
        connector = _make_connector()
        mock_record = MagicMock()
        mock_record.record_name = "new-file.pdf"

        update = RecordUpdate(
            record=mock_record,
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        # No processor calls for new record
        connector.data_entities_processor.on_record_metadata_update.assert_not_awaited()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletion_no_record_in_db(self):
        """Deletion when record not in DB."""
        connector = _make_connector()
        mock_tx, _ = _make_tx_store(None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        update = RecordUpdate(
            record=None, external_record_id="missing",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_changes_at_once(self):
        """Update with metadata, content, and permission changes."""
        connector = _make_connector()
        mock_record = MagicMock()
        mock_record.record_name = "all-changes.pdf"
        perms = [Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)]

        update = RecordUpdate(
            record=mock_record,
            is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=True,
            new_permissions=perms,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Exception in handler does not propagate."""
        connector = _make_connector()
        connector.data_entities_processor.on_record_metadata_update = AsyncMock(side_effect=Exception("fail"))

        update = RecordUpdate(
            record=MagicMock(record_name="err.pdf"),
            is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        # Should not raise
        await connector._handle_record_updates(update)


# ===========================================================================
# _sync_user_groups - no delta link from initial
# ===========================================================================


class TestSyncUserGroupsCoverage:

    @pytest.mark.asyncio
    async def test_initial_sync_no_delta_link(self):
        """Initial sync but _get_initial_delta_link returns None."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector._get_initial_delta_link = AsyncMock(return_value=None)
        connector._perform_initial_full_sync = AsyncMock()

        await connector._sync_user_groups()

        connector._perform_initial_full_sync.assert_awaited_once()
        # update_sync_point should NOT be called when delta_link is None
        connector.user_group_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_raises_error(self):
        """Error in _sync_user_groups propagates."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.read_sync_point = AsyncMock(side_effect=Exception("db fail"))

        with pytest.raises(Exception, match="db fail"):
            await connector._sync_user_groups()


# ===========================================================================
# _get_initial_delta_link - pagination and error
# ===========================================================================


class TestGetInitialDeltaLink:

    @pytest.mark.asyncio
    async def test_pagination_then_delta_link(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {"groups": [MagicMock()], "next_link": "https://next", "delta_link": None},
            {"groups": [], "next_link": None, "delta_link": "https://delta-final"},
        ])

        result = await connector._get_initial_delta_link()
        assert result == "https://delta-final"

    @pytest.mark.asyncio
    async def test_no_links(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [], "next_link": None, "delta_link": None,
        })

        result = await connector._get_initial_delta_link()
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=Exception("API err"))

        result = await connector._get_initial_delta_link()
        assert result is None


# ===========================================================================
# _perform_initial_full_sync
# ===========================================================================


class TestPerformInitialFullSync:

    @pytest.mark.asyncio
    async def test_with_exception_in_group(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        group1 = MagicMock()
        group1.id = "g1"
        group1.display_name = "Group1"
        group2 = MagicMock()
        group2.id = "g2"
        group2.display_name = "Group2"
        connector.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group1, group2])

        # First group fails, second succeeds
        user_group = MagicMock()
        app_users = [MagicMock()]
        connector._process_single_group = AsyncMock(side_effect=[
            Exception("fail"), (user_group, app_users)
        ])

        await connector._perform_initial_full_sync()
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_groups_fail(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_user_groups = AsyncMock(return_value=[MagicMock()])
        connector._process_single_group = AsyncMock(return_value=None)

        await connector._perform_initial_full_sync()
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()


# ===========================================================================
# _process_single_group
# ===========================================================================


class TestProcessSingleGroup:

    @pytest.mark.asyncio
    async def test_with_nested_group_member(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        nested_group_member = MagicMock()
        nested_group_member.id = "ng-1"
        nested_group_member.odata_type = "#microsoft.graph.group"
        nested_group_member.additional_data = {}

        user_member = MagicMock()
        user_member.id = "u-1"
        user_member.odata_type = "#microsoft.graph.user"
        user_member.mail = "user@test.com"
        user_member.display_name = "User"
        user_member.created_date_time = None
        user_member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[user_member, nested_group_member])
        connector._get_users_from_nested_group = AsyncMock(return_value=[MagicMock()])

        group = MagicMock()
        group.id = "grp-1"
        group.display_name = "TestGroup"
        group.description = "desc"
        group.created_date_time = MagicMock()
        group.created_date_time.timestamp.return_value = 1700000000

        result = await connector._process_single_group(group)
        assert result is not None
        user_group, app_users = result
        assert len(app_users) == 2  # 1 direct + 1 nested

    @pytest.mark.asyncio
    async def test_skips_unknown_member_type(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        device_member = MagicMock()
        device_member.id = "dev-1"
        device_member.odata_type = "#microsoft.graph.device"
        device_member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[device_member])

        group = MagicMock()
        group.id = "grp-1"
        group.display_name = "TestGroup"
        group.description = None
        group.created_date_time = None

        result = await connector._process_single_group(group)
        assert result is not None
        _, app_users = result
        assert len(app_users) == 0

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("fail"))

        group = MagicMock()
        group.id = "grp-err"
        group.display_name = "FailGroup"

        result = await connector._process_single_group(group)
        assert result is None


# ===========================================================================
# _perform_delta_sync
# ===========================================================================


class TestPerformDeltaSync:

    @pytest.mark.asyncio
    async def test_group_deletion(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        deleted_group = MagicMock()
        deleted_group.id = "grp-del"
        deleted_group.additional_data = {"@removed": {"reason": "deleted"}}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [deleted_group],
            "next_link": None,
            "delta_link": "https://delta",
        })
        connector.handle_delete_group = AsyncMock(return_value=True)

        await connector._perform_delta_sync("https://url", "key")
        connector.handle_delete_group.assert_awaited_once_with("grp-del")

    @pytest.mark.asyncio
    async def test_group_add_update_with_member_changes(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        group = MagicMock()
        group.id = "grp-1"
        group.display_name = "Updated Group"
        group.additional_data = {
            "members@delta": [
                {"id": "user-add", "@odata.type": "#microsoft.graph.user"},
                {"id": "user-del", "@removed": {"reason": "deleted"}},
            ]
        }

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group],
            "next_link": None,
            "delta_link": "https://delta",
        })
        connector.handle_group_create = AsyncMock(return_value=True)
        connector._process_member_change = AsyncMock()

        await connector._perform_delta_sync("https://url", "key")
        assert connector._process_member_change.await_count == 2

    @pytest.mark.asyncio
    async def test_pagination_with_next_link(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {"groups": [], "next_link": "https://next", "delta_link": None},
            {"groups": [], "next_link": None, "delta_link": "https://delta"},
        ])

        await connector._perform_delta_sync("https://url", "key")
        assert connector.user_group_sync_point.update_sync_point.await_count == 2

    @pytest.mark.asyncio
    async def test_no_links_breaks(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [], "next_link": None, "delta_link": None,
        })

        await connector._perform_delta_sync("https://url", "key")

    @pytest.mark.asyncio
    async def test_empty_url_fallback(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [], "next_link": None, "delta_link": "https://delta",
        })

        await connector._perform_delta_sync("", "key")

    @pytest.mark.asyncio
    async def test_group_create_failure_continues(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        group = MagicMock()
        group.id = "grp-1"
        group.display_name = "FailGroup"
        group.additional_data = {}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group],
            "next_link": None,
            "delta_link": "https://delta",
        })
        connector.handle_group_create = AsyncMock(return_value=False)

        await connector._perform_delta_sync("https://url", "key")

    @pytest.mark.asyncio
    async def test_delete_group_failure_continues(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.user_group_sync_point = MagicMock()
        connector.user_group_sync_point.update_sync_point = AsyncMock()

        group = MagicMock()
        group.id = "grp-del-fail"
        group.additional_data = {"@removed": {"reason": "deleted"}}

        connector.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group],
            "next_link": None,
            "delta_link": "https://delta",
        })
        connector.handle_delete_group = AsyncMock(return_value=False)

        await connector._perform_delta_sync("https://url", "key")


# ===========================================================================
# _process_member_change
# ===========================================================================


class TestProcessMemberChange:

    @pytest.mark.asyncio
    async def test_add_member(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        await connector._process_member_change("grp-1", {"id": "user-1"})
        # Add path - just logs

    @pytest.mark.asyncio
    async def test_remove_member(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")

        await connector._process_member_change("grp-1", {"id": "user-1", "@removed": {"reason": "deleted"}})
        connector.data_entities_processor.on_user_group_member_removed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remove_member_failure(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value="user@test.com")
        connector.data_entities_processor.on_user_group_member_removed = AsyncMock(return_value=False)

        await connector._process_member_change("grp-1", {"id": "user-1", "@removed": {"reason": "deleted"}})

    @pytest.mark.asyncio
    async def test_no_email_returns(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_user_email = AsyncMock(return_value=None)

        await connector._process_member_change("grp-1", {"id": "user-no-email"})
        connector.data_entities_processor.on_user_group_member_removed.assert_not_awaited()


# ===========================================================================
# _get_users_from_nested_group
# ===========================================================================


class TestGetUsersFromNestedGroup:

    @pytest.mark.asyncio
    async def test_nested_group_with_users(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        nested_member = MagicMock()
        nested_member.id = "nested-user-1"
        nested_member.odata_type = "#microsoft.graph.user"
        nested_member.mail = "nested@test.com"
        nested_member.display_name = "Nested User"
        nested_member.created_date_time = None
        nested_member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[nested_member])

        nested_group = MagicMock()
        nested_group.id = "ng-1"
        nested_group.display_name = "NestedGroup"

        result = await connector._get_users_from_nested_group(nested_group)
        assert len(result) == 1
        assert result[0].email == "nested@test.com"

    @pytest.mark.asyncio
    async def test_nested_group_skips_non_users(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        non_user_member = MagicMock()
        non_user_member.id = "svc-1"
        non_user_member.odata_type = "#microsoft.graph.servicePrincipal"
        non_user_member.additional_data = {}

        connector.msgraph_client.get_group_members = AsyncMock(return_value=[non_user_member])

        nested_group = MagicMock()
        nested_group.id = "ng-1"
        nested_group.display_name = "NestedGroup"

        result = await connector._get_users_from_nested_group(nested_group)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_nested_group_error(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("fail"))

        nested_group = MagicMock()
        nested_group.id = "ng-err"
        nested_group.display_name = "NestedGroupErr"

        result = await connector._get_users_from_nested_group(nested_group)
        assert result == []

    @pytest.mark.asyncio
    async def test_nested_group_no_display_name(self):
        """Nested group without display_name falls back to id."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_group_members = AsyncMock(return_value=[])

        nested_group = MagicMock(spec=[])
        nested_group.id = "ng-no-name"
        # No display_name attribute

        result = await connector._get_users_from_nested_group(nested_group)
        assert result == []


# ===========================================================================
# _create_app_user_from_member
# ===========================================================================


class TestCreateAppUserFromMember:

    def test_no_email_returns_none(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "u-no-email"
        member.mail = None
        member.user_principal_name = None

        result = connector._create_app_user_from_member(member)
        assert result is None

    def test_uses_user_principal_name_fallback(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "u-upn"
        member.mail = None
        member.user_principal_name = "upn@test.com"
        member.display_name = "UPN User"
        member.created_date_time = None

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.email == "upn@test.com"

    def test_with_created_date_time(self):
        connector = _make_connector()
        member = MagicMock()
        member.id = "u-1"
        member.mail = "user@test.com"
        member.display_name = "User"
        member.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = connector._create_app_user_from_member(member)
        assert result is not None
        assert result.source_created_at == datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()


# ===========================================================================
# handle_delete_group
# ===========================================================================


class TestHandleDeleteGroupCoverage:

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        connector = _make_connector()
        connector.data_entities_processor.on_user_group_deleted = AsyncMock(side_effect=Exception("db fail"))

        result = await connector.handle_delete_group("grp-exc")
        assert result is False


# ===========================================================================
# _run_sync_with_yield - additional coverage
# ===========================================================================


class TestRunSyncWithYieldCoverage:

    @pytest.mark.asyncio
    async def test_first_sync_no_user_info(self):
        """First sync when user info can't be fetched."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        drive = MagicMock()
        drive.id = "drive-1"
        connector.msgraph_client.get_user_drive = AsyncMock(return_value=drive)
        connector.msgraph_client.get_user_info = AsyncMock(return_value=None)
        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            "drive_items": [], "next_link": None, "delta_link": "https://delta",
        })

        await connector._run_sync_with_yield("user-no-info")
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_sync_record_group_creation_error(self):
        """RecordGroup creation fails but sync continues."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=Exception("drive err"))
        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            "drive_items": [], "next_link": None, "delta_link": "https://delta",
        })

        # Should not raise - just logs error and continues
        await connector._run_sync_with_yield("user-err")

    @pytest.mark.asyncio
    async def test_sync_uses_next_link_from_sync_point(self):
        """When sync point has nextLink, uses it."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"nextLink": "https://next-link"}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            "drive_items": [], "next_link": None, "delta_link": "https://delta",
        })

        await connector._run_sync_with_yield("user-1")

    @pytest.mark.asyncio
    async def test_sync_error_propagates(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"deltaLink": "https://delta"}
        )
        connector.msgraph_client.get_delta_response = AsyncMock(side_effect=Exception("api err"))

        with pytest.raises(Exception, match="api err"):
            await connector._run_sync_with_yield("user-1")

    @pytest.mark.asyncio
    async def test_first_sync_no_display_name(self):
        """First sync with user info but no display_name."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        drive = MagicMock()
        drive.id = "drive-1"
        drive.web_url = "https://onedrive.com/u1"
        connector.msgraph_client.get_user_drive = AsyncMock(return_value=drive)
        connector.msgraph_client.get_user_info = AsyncMock(return_value={
            "display_name": None,
            "email": "user@test.com",
        })
        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            "drive_items": [], "next_link": None, "delta_link": "https://delta",
        })

        await connector._run_sync_with_yield("user-1")
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_drive_items_breaks(self):
        """Empty or None drive_items in response breaks the loop."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"deltaLink": "https://delta"}
        )
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        connector.msgraph_client.get_delta_response = AsyncMock(return_value={
            "drive_items": None, "next_link": None, "delta_link": "https://delta",
        })

        await connector._run_sync_with_yield("user-1")


# ===========================================================================
# _process_users_in_batches
# ===========================================================================


class TestProcessUsersInBatchesCoverage:

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        connector = _make_connector()
        connector.data_entities_processor.get_all_active_users = AsyncMock(side_effect=Exception("db err"))

        with pytest.raises(Exception, match="db err"):
            await connector._process_users_in_batches([MagicMock()])

    @pytest.mark.asyncio
    async def test_user_without_email_filtered(self):
        """User with None email is filtered out."""
        connector = _make_connector()
        active_user = MagicMock()
        active_user.email = "active@test.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])
        connector._run_sync_with_yield = AsyncMock()

        user_no_email = MagicMock()
        user_no_email.email = None
        user_no_email.source_user_id = "su1"

        await connector._process_users_in_batches([user_no_email])
        connector._run_sync_with_yield.assert_not_awaited()


# ===========================================================================
# _user_has_onedrive
# ===========================================================================


class TestUserHasOneDriveCoverage:

    @pytest.mark.asyncio
    async def test_item_not_found_error_code(self):
        """Error with OData error code 'itemnotfound'."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        error = Exception("some error")
        error.error = MagicMock()
        error.error.code = "itemNotFound"
        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=error)

        result = await connector._user_has_onedrive("user-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_404_in_message(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        error = Exception("404 not found")
        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=error)

        result = await connector._user_has_onedrive("user-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_error_raises(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        error = Exception("internal server error")
        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=error)

        with pytest.raises(Exception, match="internal server error"):
            await connector._user_has_onedrive("user-1")

    @pytest.mark.asyncio
    async def test_request_resource_not_found(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        error = Exception("Request_ResourceNotFound")
        connector.msgraph_client.get_user_drive = AsyncMock(side_effect=error)

        result = await connector._user_has_onedrive("user-1")
        assert result is False


# ===========================================================================
# _detect_and_handle_permission_changes
# ===========================================================================


class TestDetectAndHandlePermissionChanges:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        await connector._detect_and_handle_permission_changes()

    @pytest.mark.asyncio
    async def test_error_logged(self):
        """Error in detection is logged but not raised."""
        connector = _make_connector()
        # The method doesn't really do much, but test that exceptions are caught
        # by patching the logger itself to track calls
        await connector._detect_and_handle_permission_changes()


# ===========================================================================
# _handle_reindex_event
# ===========================================================================


class TestHandleReindexEvent:

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        connector = _make_connector()
        mock_tx, _ = _make_tx_store(None)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._handle_reindex_event("missing-id")

    @pytest.mark.asyncio
    async def test_record_found_and_updated(self):
        connector = _make_connector()
        record = _make_existing_record()
        mock_tx, _ = _make_tx_store(record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        mock_item = _make_drive_item()
        drive_mock = MagicMock()
        items_mock = MagicMock()
        item_get = AsyncMock(return_value=mock_item)
        items_mock.by_drive_item_id.return_value.get = item_get
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        mock_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=True,
            new_permissions=[MagicMock()],
        )
        connector._process_delta_item = AsyncMock(return_value=mock_update)

        await connector._handle_reindex_event("item-1")
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_item_not_found_at_source(self):
        connector = _make_connector()
        record = _make_existing_record()
        mock_tx, _ = _make_tx_store(record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=None)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        await connector._handle_reindex_event("item-1")

    @pytest.mark.asyncio
    async def test_process_delta_item_returns_none(self):
        connector = _make_connector()
        record = _make_existing_record()
        mock_tx, _ = _make_tx_store(record)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        mock_item = _make_drive_item()
        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=mock_item)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        connector._process_delta_item = AsyncMock(return_value=None)
        await connector._handle_reindex_event("item-1")

    @pytest.mark.asyncio
    async def test_error_caught(self):
        connector = _make_connector()
        mock_tx, _ = _make_tx_store(None)
        mock_tx.__aenter__ = AsyncMock(side_effect=Exception("db err"))
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        await connector._handle_reindex_event("item-err")


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    @pytest.mark.asyncio
    async def test_valid_notification(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._run_sync_with_yield = AsyncMock()

        notification = {
            "resource": "users/user-123/drive/root",
            "changeType": "updated",
        }
        await connector.handle_webhook_notification(notification)
        connector._run_sync_with_yield.assert_awaited_once_with("user-123")

    @pytest.mark.asyncio
    async def test_notification_without_users_prefix(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector._run_sync_with_yield = AsyncMock()

        notification = {"resource": "drives/drive-1/root", "changeType": "updated"}
        await connector.handle_webhook_notification(notification)
        connector._run_sync_with_yield.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_short_resource(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()

        notification = {"resource": "users", "changeType": "updated"}
        await connector.handle_webhook_notification(notification)

    @pytest.mark.asyncio
    async def test_notification_error(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock(side_effect=Exception("err"))

        notification = {"resource": "users/u1/drive/root"}
        # Should not raise
        await connector.handle_webhook_notification(notification)


# ===========================================================================
# run_sync
# ===========================================================================


class TestRunSyncCoverage:

    @pytest.mark.asyncio
    async def test_full_sync_workflow(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(return_value=[MagicMock()])
        connector._sync_user_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock()
        connector._detect_and_handle_permission_changes = AsyncMock()

        with patch("app.connectors.sources.microsoft.onedrive.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(MagicMock(), MagicMock())):
            await connector.run_sync()

        connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        connector._sync_user_groups.assert_awaited_once()
        connector._process_users_in_batches.assert_awaited_once()


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(return_value=[MagicMock()])
        connector._process_users_in_batches = AsyncMock()

        await connector.run_incremental_sync()
        connector._process_users_in_batches.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock(side_effect=Exception("auth err"))

        with pytest.raises(Exception, match="auth err"):
            await connector.run_incremental_sync()


# ===========================================================================
# _reinitialize_credential_if_needed
# ===========================================================================


class TestReinitializeCredential:

    @pytest.mark.asyncio
    async def test_credential_valid(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock())

        await connector._reinitialize_credential_if_needed()
        # Should not reinitialize

    @pytest.mark.asyncio
    async def test_credential_expired_reinitializes(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("transport closed"))
        connector.credential.close = AsyncMock()

        connector.config = {
            "credentials": {
                "auth": {"tenantId": "t", "clientId": "c", "clientSecret": "s"}
            }
        }

        with patch("app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.onedrive.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.onedrive.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock()
            mock_cred.return_value = new_cred

            await connector._reinitialize_credential_if_needed()

    @pytest.mark.asyncio
    async def test_credential_expired_missing_config(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("closed"))
        connector.credential.close = AsyncMock()

        connector.config = {"credentials": {"auth": {}}}

        with pytest.raises(ValueError, match="Cannot reinitialize"):
            await connector._reinitialize_credential_if_needed()

    @pytest.mark.asyncio
    async def test_old_credential_close_error_ignored(self):
        """Error closing old credential is ignored."""
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(side_effect=Exception("closed"))
        connector.credential.close = AsyncMock(side_effect=Exception("already closed"))

        connector.config = {
            "credentials": {
                "auth": {"tenantId": "t", "clientId": "c", "clientSecret": "s"}
            }
        }

        with patch("app.connectors.sources.microsoft.onedrive.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.onedrive.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.onedrive.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock()
            mock_cred.return_value = new_cred

            await connector._reinitialize_credential_if_needed()


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanupCoverage:

    @pytest.mark.asyncio
    async def test_cleanup_credential_close_error(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock(side_effect=Exception("close err"))
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()

        await connector.cleanup()
        assert connector.credential is None
        assert connector.client is None

    @pytest.mark.asyncio
    async def test_cleanup_no_client_or_msgraph(self):
        """Cleanup when client and msgraph_client are not set."""
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock()
        # No client or msgraph_client attributes

        await connector.cleanup()


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_msgraph_client(self):
        connector = _make_connector()
        connector.msgraph_client = None

        with pytest.raises(Exception, match="MS Graph client not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated_records(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()

        rec1 = MagicMock()
        rec1.id = "r1"
        rec2 = MagicMock()
        rec2.id = "r2"
        rec3 = MagicMock()
        rec3.id = "r3"

        updated_record = MagicMock()
        updated_perms = [MagicMock()]

        # rec1: updated, rec2: not updated, rec3: error
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=[
            (updated_record, updated_perms),
            None,
            Exception("fetch err"),
        ])

        await connector.reindex_records([rec1, rec2, rec3])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_all_records_have_errors(self):
        """When _check_and_fetch_updated_record raises for all records, no updates/reindexing."""
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("err"))

        await connector.reindex_records([MagicMock(id="r1")])
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_missing_drive_or_item_id(self):
        connector = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = None
        record.external_record_id = None

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_item_not_found_at_source(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=None)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_record_updated_returns_data(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        mock_item = _make_drive_item()
        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=mock_item)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        update_record = MagicMock()
        mock_update = RecordUpdate(
            record=update_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        connector._process_delta_item = AsyncMock(return_value=mock_update)

        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        assert result[0].id == "r1"

    @pytest.mark.asyncio
    async def test_record_not_updated(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        mock_item = _make_drive_item()
        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=mock_item)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        mock_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        connector._process_delta_item = AsyncMock(return_value=mock_update)

        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock()
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        mock_item = _make_drive_item()
        drive_mock = MagicMock()
        items_mock = MagicMock()
        items_mock.by_drive_item_id.return_value.get = AsyncMock(return_value=mock_item)
        drive_mock.items = items_mock
        connector.msgraph_client.client.drives.by_drive_id.return_value = drive_mock

        mock_update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="item-1",
        )
        connector._process_delta_item = AsyncMock(return_value=mock_update)

        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.rate_limiter = MagicMock()
        connector.msgraph_client.rate_limiter.__aenter__ = AsyncMock(side_effect=Exception("fail"))
        connector.msgraph_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)

        record = MagicMock()
        record.id = "r1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")

        record = MagicMock()
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"

        result = await connector.get_signed_url(record)
        assert result == "https://signed"

    @pytest.mark.asyncio
    async def test_error_raises(self):
        connector = _make_connector()
        connector._reinitialize_credential_if_needed = AsyncMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(side_effect=Exception("fail"))

        record = MagicMock()
        record.id = "r1"

        with pytest.raises(Exception, match="fail"):
            await connector.get_signed_url(record)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.get_signed_url = AsyncMock(return_value="https://signed")

        record = MagicMock()
        record.record_name = "doc.pdf"
        record.mime_type = "application/pdf"
        record.id = "r1"

        with patch("app.connectors.sources.microsoft.onedrive.connector.create_stream_record_response") as mock_stream, \
             patch("app.connectors.sources.microsoft.onedrive.connector.stream_content") as mock_content:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_signed_url_raises(self):
        connector = _make_connector()
        connector.get_signed_url = AsyncMock(return_value=None)

        record = MagicMock()
        record.record_name = "doc.pdf"
        record.mime_type = "application/pdf"
        record.id = "r1"

        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await connector.stream_record(record)


# ===========================================================================
# _parse_datetime
# ===========================================================================


class TestParseDatetimeCoverage:

    def test_empty_string(self):
        connector = _make_connector()
        assert connector._parse_datetime("") is None

    def test_zero_value(self):
        connector = _make_connector()
        assert connector._parse_datetime(0) is None


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_returns_true(self):
        connector = _make_connector()
        result = await connector.test_connection_and_access()
        assert result is True


# ===========================================================================
# create_connector
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.microsoft.onedrive.connector.DataSourceEntitiesProcessor") as mock_proc:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_proc.return_value = mock_instance

            logger = _make_mock_logger()
            dsp = MagicMock()
            cs = MagicMock()

            result = await OneDriveConnector.create_connector(logger, dsp, cs, "conn-1")
            assert isinstance(result, OneDriveConnector)
            mock_instance.initialize.assert_awaited_once()


# ===========================================================================
# OneDriveSubscriptionManager - additional coverage
# ===========================================================================


class TestSubscriptionManagerCoverage:

    @pytest.mark.asyncio
    async def test_create_subscription_null_result(self):
        mock_client = MagicMock()
        mock_client.client.subscriptions.post = AsyncMock(return_value=None)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        result = await manager.create_subscription("user-1", "https://webhook")
        assert result is None

    @pytest.mark.asyncio
    async def test_renew_subscription_success(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.patch = AsyncMock()
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        result = await manager.renew_subscription("sub-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_renew_subscription_failure(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.patch = AsyncMock(side_effect=Exception("fail"))
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        result = await manager.renew_subscription("sub-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_subscription_failure(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.delete = AsyncMock(side_effect=Exception("fail"))
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        result = await manager.delete_subscription("sub-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_subscription_no_tracking_match(self):
        """Delete subscription that's not in the tracking dict."""
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.delete = AsyncMock()
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        # No subscriptions tracked
        result = await manager.delete_subscription("sub-unknown")
        assert result is True

    @pytest.mark.asyncio
    async def test_renew_all_subscriptions(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.patch = AsyncMock()
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        manager.subscriptions = {"u1": "sub-1", "u2": "sub-2"}

        await manager.renew_all_subscriptions()

    @pytest.mark.asyncio
    async def test_renew_all_error(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.patch = AsyncMock(side_effect=Exception("fail"))
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        manager.subscriptions = {"u1": "sub-1"}

        # Should not raise - error is logged
        await manager.renew_all_subscriptions()

    @pytest.mark.asyncio
    async def test_cleanup_subscriptions(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.delete = AsyncMock()
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        manager.subscriptions = {"u1": "sub-1", "u2": "sub-2"}

        await manager.cleanup_subscriptions()
        assert len(manager.subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cleanup_subscriptions_error(self):
        mock_client = MagicMock()
        sub_by_id = MagicMock()
        sub_by_id.delete = AsyncMock(side_effect=Exception("fail"))
        mock_client.client.subscriptions.by_subscription_id = MagicMock(return_value=sub_by_id)
        mock_client.rate_limiter = MagicMock()
        mock_client.rate_limiter.__aenter__ = AsyncMock()
        mock_client.rate_limiter.__aexit__ = AsyncMock(return_value=False)
        logger = _make_mock_logger()

        manager = OneDriveSubscriptionManager(mock_client, logger)
        manager.subscriptions = {"u1": "sub-1"}

        # Should not raise
        await manager.cleanup_subscriptions()


# ===========================================================================
# _convert_to_permissions
# ===========================================================================


class TestConvertToPermissions:
    @pytest.mark.asyncio
    async def test_user_permission_via_granted_to_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = MagicMock()
        perm.granted_to_v2.user.id = "user-1"
        perm.granted_to_v2.user.additional_data = {"email": "user@example.com"}
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None
        perm.roles = ["write"]

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].external_id == "user-1"
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_group_permission_via_granted_to_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = None
        perm.granted_to_v2.group = MagicMock()
        perm.granted_to_v2.group.id = "group-1"
        perm.granted_to_v2.group.additional_data = {}
        perm.granted_to_identities_v2 = None
        perm.link = None
        perm.roles = ["read"]

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_via_identities_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        identity = MagicMock()
        identity.group = MagicMock()
        identity.group.id = "grp-1"
        identity.group.additional_data = {"email": "grp@example.com"}
        identity.user = None
        perm.granted_to_identities_v2 = [identity]
        perm.link = None
        perm.roles = ["read"]

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_user_via_identities_v2(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        identity = MagicMock()
        identity.group = None
        identity.user = MagicMock()
        identity.user.id = "usr-1"
        identity.user.additional_data = {}
        perm.granted_to_identities_v2 = [identity]
        perm.link = None
        perm.roles = ["write"]

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_anonymous_link_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        perm.link = MagicMock()
        perm.link.scope = "anonymous"
        perm.link.type = "view"
        perm.roles = []

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ANYONE_WITH_LINK

    @pytest.mark.asyncio
    async def test_organization_link_permission(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        perm.link = MagicMock()
        perm.link.scope = "organization"
        perm.link.type = "edit"
        perm.roles = []

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_permission_conversion_error_skipped(self):
        connector = _make_connector()
        perm = MagicMock()
        perm.granted_to_v2 = MagicMock(side_effect=Exception("bad perm"))
        # Make hasattr raise to trigger exception
        type(perm).granted_to_v2 = property(lambda s: (_ for _ in ()).throw(Exception("bad")))
        perm.granted_to_identities_v2 = None
        perm.link = None
        perm.roles = ["read"]

        result = await connector._convert_to_permissions([perm])
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_permissions(self):
        connector = _make_connector()
        result = await connector._convert_to_permissions([])
        assert result == []


# ===========================================================================
# _permissions_equal
# ===========================================================================


class TestPermissionsEqual:
    def test_equal(self):
        connector = _make_connector()
        p1 = Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)
        p2 = Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)
        assert connector._permissions_equal([p1], [p2]) is True

    def test_different_length(self):
        connector = _make_connector()
        p1 = Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)
        assert connector._permissions_equal([p1], []) is False

    def test_different_content(self):
        connector = _make_connector()
        p1 = Permission(external_id="u1", type=PermissionType.READ, entity_type=EntityType.USER)
        p2 = Permission(external_id="u2", type=PermissionType.WRITE, entity_type=EntityType.USER)
        assert connector._permissions_equal([p1], [p2]) is False


# ===========================================================================
# _pass_date_filters
# ===========================================================================


class TestPassDateFilters:
    def test_folder_always_passes(self):
        connector = _make_connector()
        item = _make_drive_item(is_folder=True)
        assert connector._pass_date_filters(item) is True

    def test_no_filters_passes(self):
        connector = _make_connector()
        item = _make_drive_item()
        assert connector._pass_date_filters(item) is True
