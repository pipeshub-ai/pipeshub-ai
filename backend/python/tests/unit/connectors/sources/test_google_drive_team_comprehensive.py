"""Comprehensive tests for GoogleDriveTeamConnector - additional coverage for uncovered methods."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
    IndexingFilterKey,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_logger():
    log = logging.getLogger("test_drive_team_comp")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User",
                      suspended=False, creation_time="2024-01-01T00:00:00.000Z",
                      organizations=None, given_name="Test", family_name="User"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": given_name, "familyName": family_name},
        "suspended": suspended,
        "creationTime": creation_time,
        "organizations": organizations if organizations is not None else [{"title": "Engineer"}],
    }


def _make_file_metadata(
    file_id="file-1", name="test.txt", mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z", modified_time="2025-01-15T00:00:00Z",
    parents=None, shared=False, head_revision_id="rev-1", file_extension="txt",
    size=1024, owners=None, version=None,
):
    meta = {
        "id": file_id, "name": name, "mimeType": mime_type,
        "createdTime": created_time, "modifiedTime": modified_time,
        "shared": shared, "headRevisionId": head_revision_id,
        "fileExtension": file_extension, "size": size,
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if owners is not None:
        meta["owners"] = owners
    if version is not None:
        meta["version"] = version
    return meta


@pytest.fixture
def connector():
    """Create a GoogleDriveTeamConnector with fully mocked dependencies."""
    with patch(
        "app.connectors.sources.google.drive.team.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.team.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.drive.team.connector import (
            GoogleDriveTeamConnector,
        )

        logger = _make_logger()
        dep = MagicMock()
        dep.org_id = "org-1"
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_records = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.add_permission_to_record = AsyncMock()
        provider = _make_mock_data_store_provider()

        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "auth": {
                "adminEmail": "admin@example.com",
                "type": "service_account",
            }
        })

        c = GoogleDriveTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=provider,
            config_service=config_svc,
            connector_id="drive-comp-1",
        )
        c.admin_data_source = AsyncMock()
        c.drive_data_source = AsyncMock()
        c.sync_filters = FilterCollection()
        c.indexing_filters = FilterCollection()
        yield c


# ===========================================================================
# Init
# ===========================================================================
class TestInitComprehensive:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth_credentials(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"type": "service_account"}
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncComprehensive:
    @pytest.mark.asyncio
    async def test_run_sync_orchestrates_steps(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_record_groups = AsyncMock(return_value=[])
            connector._process_users_in_batches = AsyncMock()
            connector.synced_users = []

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_record_groups.assert_awaited_once()
            connector._process_users_in_batches.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_error(self, connector):
        with patch(
            "app.connectors.sources.google.drive.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock(side_effect=Exception("sync fail"))
            with pytest.raises(Exception, match="sync fail"):
                await connector.run_sync()


# ===========================================================================
# _sync_users (extended edge cases)
# ===========================================================================
class TestSyncUsersComprehensive:
    @pytest.mark.asyncio
    async def test_sync_users_no_admin_source_raises(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_no_users_found(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector._sync_users()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_sync_users_skips_user_without_email(self, connector):
        user_no_email = {"id": "u1", "name": {"fullName": "No Email"}}
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_no_email]
        })
        await connector._sync_users()
        assert len(connector.synced_users) == 0

    @pytest.mark.asyncio
    async def test_sync_users_suspended_user(self, connector):
        user = _make_google_user(suspended=True)
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert len(connector.synced_users) == 1
        assert connector.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_sync_users_pagination(self, connector):
        page1_users = [_make_google_user(email="u1@test.com", user_id="u1")]
        page2_users = [_make_google_user(email="u2@test.com", user_id="u2")]

        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": page1_users, "nextPageToken": "token2"},
            {"users": page2_users},
        ])
        await connector._sync_users()
        assert len(connector.synced_users) == 2

    @pytest.mark.asyncio
    async def test_sync_users_empty_name_falls_back_to_email(self, connector):
        user = _make_google_user(
            full_name="",
            given_name="",
            family_name="",
        )
        user["name"]["fullName"] = ""
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "user@example.com"


# ===========================================================================
# Permission role/type mapping
# ===========================================================================
class TestPermissionMappingComprehensive:
    def test_map_role_owner(self, connector):
        assert connector._map_drive_role_to_permission_type("owner") == PermissionType.OWNER

    def test_map_role_organizer(self, connector):
        assert connector._map_drive_role_to_permission_type("organizer") == PermissionType.OWNER

    def test_map_role_file_organizer(self, connector):
        assert connector._map_drive_role_to_permission_type("fileOrganizer") == PermissionType.WRITE

    def test_map_role_writer(self, connector):
        assert connector._map_drive_role_to_permission_type("writer") == PermissionType.WRITE

    def test_map_role_commenter(self, connector):
        assert connector._map_drive_role_to_permission_type("commenter") == PermissionType.COMMENT

    def test_map_role_reader(self, connector):
        assert connector._map_drive_role_to_permission_type("reader") == PermissionType.READ

    def test_map_role_unknown(self, connector):
        assert connector._map_drive_role_to_permission_type("custom_role") == PermissionType.READ

    def test_map_perm_type_user(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("user") == EntityType.USER

    def test_map_perm_type_group(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("group") == EntityType.GROUP

    def test_map_perm_type_domain(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("domain") == EntityType.DOMAIN

    def test_map_perm_type_anyone(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyone") == EntityType.ANYONE

    def test_map_perm_type_anyone_with_link(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyoneWithLink") == EntityType.ANYONE_WITH_LINK

    def test_map_perm_type_anyone_with_link_underscore(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("anyone_with_link") == EntityType.ANYONE_WITH_LINK

    def test_map_perm_type_unknown(self, connector):
        assert connector._map_drive_permission_type_to_entity_type("unknown") == EntityType.USER


# ===========================================================================
# _parse_datetime
# ===========================================================================
class TestParseDatetimeComprehensive:
    def test_iso_with_z_suffix(self, connector):
        result = connector._parse_datetime("2025-01-15T10:30:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_iso_with_offset(self, connector):
        result = connector._parse_datetime("2025-01-15T10:30:00+05:30")
        assert result is not None

    def test_datetime_object(self, connector):
        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result is not None

    def test_none_returns_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_empty_string(self, connector):
        assert connector._parse_datetime("") is None

    def test_invalid_string(self, connector):
        assert connector._parse_datetime("not-a-date") is None


# ===========================================================================
# _pass_date_filters (comprehensive)
# ===========================================================================
class TestPassDateFiltersComprehensive:
    def test_folders_always_pass(self, connector):
        meta = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_date_filters(meta) is True

    def test_no_filters_passes(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_date_filters(meta) is True

    def test_file_without_timestamps_passes(self, connector):
        meta = {"mimeType": "text/plain"}
        assert connector._pass_date_filters(meta) is True


# ===========================================================================
# _pass_extension_filter (comprehensive)
# ===========================================================================
class TestPassExtensionFilterComprehensive:
    def test_folders_always_pass(self, connector):
        meta = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_extension_filter(meta) is True

    def test_no_filter_allows_all(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_empty_filter_allows_all(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = True
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_google_slides_in_operator(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_SLIDES.value]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        meta = {"mimeType": MimeTypes.GOOGLE_SLIDES.value, "name": "Slides"}
        assert connector._pass_extension_filter(meta) is True

    def test_unknown_operator_defaults_to_true(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt"]
        mock_filter.get_operator.return_value = MagicMock(value="UNKNOWN_OP")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        meta = _make_file_metadata(file_extension="txt")
        assert connector._pass_extension_filter(meta) is True


# ===========================================================================
# _process_drive_item (comprehensive)
# ===========================================================================
class TestProcessDriveItemComprehensive:
    @pytest.mark.asyncio
    async def test_new_file_record(self, connector):
        meta = _make_file_metadata(parents=["parent-1"])
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": [{"id": "perm1", "type": "user", "role": "reader",
                            "emailAddress": "u@test.com"}]
        })
        result = await connector._process_drive_item(
            meta, user_id="uid1", user_email="u@test.com",
            drive_id="drive1", is_shared_drive=False
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "test.txt"

    @pytest.mark.asyncio
    async def test_existing_record_parent_changed(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.parent_external_record_id = "old-parent"
        existing.version = 1
        existing.external_record_group_id = "drive1"
        existing.indexing_status = "COMPLETED"
        existing.extraction_status = "COMPLETED"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        meta = _make_file_metadata(parents=["new-parent"])
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": []
        })
        result = await connector._process_drive_item(
            meta, user_id="uid1", user_email="u@test.com",
            drive_id="drive1", is_shared_drive=False
        )
        assert result is not None
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_file_with_version_instead_of_revision(self, connector):
        meta = _make_file_metadata(head_revision_id=None, version="42")
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": []
        })
        result = await connector._process_drive_item(
            meta, user_id="uid1", user_email="u@test.com",
            drive_id="drive1"
        )
        assert result is not None
        assert result.record.external_revision_id == "42"

    @pytest.mark.asyncio
    async def test_shared_with_me_file(self, connector):
        meta = _make_file_metadata(
            shared=True,
            owners=[{"emailAddress": "other@test.com"}],
        )
        connector.drive_data_source.permissions_list = AsyncMock(return_value={
            "permissions": []
        })
        result = await connector._process_drive_item(
            meta, user_id="uid1", user_email="me@test.com",
            drive_id="drive1", is_shared_drive=False
        )
        assert result is not None
        assert result.record.is_shared_with_me is True
        assert result.record.external_record_group_id is None


# ===========================================================================
# _handle_drive_error
# ===========================================================================
class TestHandleDriveErrorComprehensive:
    @pytest.mark.asyncio
    async def test_403_membership_required(self, connector):
        from googleapiclient.errors import HttpError
        resp = MagicMock()
        resp.status = HttpStatusCode.FORBIDDEN.value
        error = HttpError(resp, b"")
        error.error_details = [{"reason": "teamDriveMembershipRequired"}]
        result = await connector._handle_drive_error(error, "TestDrive", "d1")
        assert result is True

    @pytest.mark.asyncio
    async def test_non_http_error(self, connector):
        error = RuntimeError("oops")
        result = await connector._handle_drive_error(error, "TestDrive", "d1")
        assert result is True


# ===========================================================================
# _process_drive_files_batch
# ===========================================================================
class TestProcessDriveFilesBatchComprehensive:
    @pytest.mark.asyncio
    async def test_batch_processes_records(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        perms = [MagicMock()]
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False,
            content_changed=False, permissions_changed=False,
            new_permissions=perms, external_record_id="f1",
        )

        async def fake_gen(*args, **kwargs):
            yield (record, perms, update)

        connector._process_drive_items_generator = fake_gen
        batch, count, total = await connector._process_drive_files_batch(
            files=[{}], user_id="u1", user_email="u@test.com",
            drive_id="d1", is_shared_drive=False, context_name="test",
            batch_records=[], batch_count=0, total_counter=0
        )
        assert total == 1
        assert len(batch) == 1

    @pytest.mark.asyncio
    async def test_batch_handles_deleted_records(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False,
            content_changed=False, permissions_changed=False,
            external_record_id="f1",
        )

        async def fake_gen(*args, **kwargs):
            yield (record, [], update)

        connector._process_drive_items_generator = fake_gen
        connector._handle_record_updates = AsyncMock()
        batch, count, total = await connector._process_drive_files_batch(
            files=[{}], user_id="u1", user_email="u@test.com",
            drive_id="d1", is_shared_drive=False, context_name="test",
            batch_records=[], batch_count=0, total_counter=0
        )
        connector._handle_record_updates.assert_awaited_once()
        assert total == 0

    @pytest.mark.asyncio
    async def test_batch_handles_updated_records(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        record = MagicMock()
        record.record_name = "updated.txt"
        update = RecordUpdate(
            record=record, is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True,
            content_changed=False, permissions_changed=False,
            external_record_id="f1",
        )

        async def fake_gen(*args, **kwargs):
            yield (record, [], update)

        connector._process_drive_items_generator = fake_gen
        connector._handle_record_updates = AsyncMock()
        batch, count, total = await connector._process_drive_files_batch(
            files=[{}], user_id="u1", user_email="u@test.com",
            drive_id="d1", is_shared_drive=False, context_name="test",
            batch_records=[], batch_count=0, total_counter=0
        )
        connector._handle_record_updates.assert_awaited_once()


# ===========================================================================
# _process_remaining_batch_records
# ===========================================================================
class TestProcessRemainingBatchComprehensive:
    @pytest.mark.asyncio
    async def test_empty_batch(self, connector):
        result, count = await connector._process_remaining_batch_records([], "test")
        assert result == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_with_records(self, connector):
        batch = [(MagicMock(), [MagicMock()])]
        result, count = await connector._process_remaining_batch_records(batch, "test")
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        assert count == 0


# ===========================================================================
# _create_and_sync_shared_drive_record_group
# ===========================================================================
class TestCreateAndSyncSharedDriveRecordGroupComprehensive:
    @pytest.mark.asyncio
    async def test_creates_record_group(self, connector):
        drive = {"id": "sd-1", "name": "SharedDrive1", "createdTime": "2025-01-01T00:00:00Z"}
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        await connector._create_and_sync_shared_drive_record_group(drive)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drive_with_description(self, connector):
        drive = {"id": "sd-2", "name": "SharedDrive2", "description": "Test Drive"}
        connector._fetch_permissions = AsyncMock(return_value=([], False))
        await connector._create_and_sync_shared_drive_record_group(drive)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ===========================================================================
# _sync_record_groups
# ===========================================================================
class TestSyncRecordGroupsComprehensive:
    @pytest.mark.asyncio
    async def test_raises_without_drive_source(self, connector):
        connector.drive_data_source = None
        with pytest.raises(ValueError, match="Drive data source not initialized"):
            await connector._sync_record_groups()

    @pytest.mark.asyncio
    async def test_creates_personal_and_shared_drives(self, connector):
        connector.drive_data_source.drives_list = AsyncMock(return_value={
            "drives": [{"id": "sd-1", "name": "Shared1"}]
        })
        connector._create_and_sync_shared_drive_record_group = AsyncMock()
        connector._create_personal_record_group = AsyncMock()
        connector.synced_users = [MagicMock(email="u@test.com")]

        result = await connector._sync_record_groups()
        assert len(result) == 1
        connector._create_and_sync_shared_drive_record_group.assert_awaited_once()
        connector._create_personal_record_group.assert_awaited_once()


# ===========================================================================
# get_signed_url and misc methods
# ===========================================================================
class TestMiscMethods:
    def test_get_signed_url_raises_not_implemented(self, connector):
        record = MagicMock()
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(record)

    @pytest.mark.asyncio
    async def test_test_connection_and_access_success(self, connector):
        # The method checks admin_client and drive_client are not None
        connector.admin_client = MagicMock()
        connector.drive_client = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_not_initialized(self, connector):
        connector.admin_client = None
        connector.drive_client = None
        result = await connector.test_connection_and_access()
        assert result is False

    def test_handle_webhook_notification_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_run_incremental_sync_raises(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.run_incremental_sync()
