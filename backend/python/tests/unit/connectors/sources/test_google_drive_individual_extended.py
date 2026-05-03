"""Extended tests for GoogleDriveIndividualConnector covering more uncovered code paths."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.connectors.sources.google.common.connector_google_exceptions import GoogleDriveError
from app.models.entities import FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_drive_individual_ext")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
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


def _make_file_metadata(
    file_id="file-1", name="test.txt", mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z", modified_time="2025-01-15T00:00:00Z",
    parents=None, shared=False, head_revision_id="rev-1", file_extension="txt",
    size=1024, version=None,
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
    if version is not None:
        meta["version"] = version
    return meta


@pytest.fixture
def connector():
    with patch(
        "app.connectors.sources.google.drive.individual.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.individual.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        conn = GoogleDriveIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-conn-1",
            scope="personal",
            created_by="test",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.google_client = MagicMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestIndividualInit:
    async def test_init_returns_false_when_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    async def test_init_returns_false_when_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {"access_token": "t"},
        })
        result = await connector.init()
        assert result is False

    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_returns_false_when_oauth_config_not_found(self, mock_fetch, connector):
        mock_fetch.return_value = None
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oc-1"},
            "credentials": {"access_token": "t", "refresh_token": "r"},
        })
        result = await connector.init()
        assert result is False

    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    async def test_init_raises_when_incomplete_credentials(self, mock_fetch, connector):
        mock_fetch.return_value = {"config": {}}
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oc-1"},
            "credentials": {},
        })
        with pytest.raises(ValueError, match="Incomplete Google Drive"):
            await connector.init()


# ---------------------------------------------------------------------------
# _process_drive_item - extended
# ---------------------------------------------------------------------------

class TestProcessDriveItemExtended:
    async def test_existing_record_with_parent_change(self, connector):
        """Detect parent change as metadata update."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = "old-parent"
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        metadata = _make_file_metadata(parents=["new-parent"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_existing_record_with_drive_change(self, connector):
        """Detect drive change as metadata update."""
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "old-drive"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        metadata = _make_file_metadata(parents=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="new-drive",
        )
        assert result.is_updated is True

    async def test_version_used_when_no_revision_id(self, connector):
        """Version is used as revision ID fallback."""
        metadata = _make_file_metadata(head_revision_id=None, version="5")
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.record.external_revision_id == "5"

    async def test_extension_extracted_from_name(self, connector):
        """Extension extracted from filename when fileExtension is absent."""
        metadata = _make_file_metadata(file_extension=None, name="report.docx")
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.record.extension == "docx"

    async def test_parent_same_as_drive_id_nullified(self, connector):
        """Parent equal to drive ID is nullified."""
        metadata = _make_file_metadata(parents=["drive-1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.record.parent_external_record_id is None

    async def test_shared_file_record(self, connector):
        """Shared file is_shared flag is set."""
        metadata = _make_file_metadata(shared=True)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.record.is_shared is True

    async def test_process_error_returns_none(self, connector):
        """Exception during processing returns None."""
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = MagicMock(side_effect=Exception("db error"))
        metadata = _make_file_metadata()
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result is None

    async def test_no_timestamps_uses_current(self, connector):
        """Missing timestamps use current time."""
        metadata = _make_file_metadata(created_time=None, modified_time=None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@example.com", drive_id="drive-1",
        )
        assert result.record.source_created_at is not None
        assert result.record.source_updated_at is not None


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestIndividualParseDatetime:
    def test_valid_iso(self, connector):
        result = connector._parse_datetime("2025-06-15T10:30:00Z")
        assert isinstance(result, int)

    def test_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_datetime_object(self, connector):
        from datetime import datetime, timezone
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert isinstance(result, int)

    def test_bad_string(self, connector):
        assert connector._parse_datetime("garbage") is None


# ---------------------------------------------------------------------------
# Extension filter - individual connector
# ---------------------------------------------------------------------------

class TestIndividualExtensionFilter:
    def test_folder_passes(self, connector):
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_no_filter_passes(self, connector):
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(metadata) is True

    def test_google_slides_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_SLIDES.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": MimeTypes.GOOGLE_SLIDES.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_not_in_filter_blocks_extension(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_operator
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(metadata) is False


# ---------------------------------------------------------------------------
# _handle_record_updates
# ---------------------------------------------------------------------------

class TestHandleRecordUpdates:
    async def test_handle_deleted_record(self, connector):
        update = MagicMock()
        update.is_deleted = True
        update.is_new = False
        update.is_updated = False
        update.external_record_id = "ext-1"
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once()

    async def test_handle_new_record(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = True
        update.is_updated = False
        update.record = MagicMock(record_name="new.txt")
        await connector._handle_record_updates(update)

    async def test_handle_metadata_update(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = True
        update.permissions_changed = False
        update.content_changed = False
        update.record = MagicMock(record_name="updated.txt")
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    async def test_handle_permissions_update(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = True
        update.content_changed = False
        update.record = MagicMock(record_name="perms.txt")
        update.new_permissions = [MagicMock()]
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()

    async def test_handle_content_update(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = False
        update.content_changed = True
        update.record = MagicMock(record_name="content.txt")
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    async def test_handle_error_swallowed(self, connector):
        """Errors in handle_record_updates are swallowed."""
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "ext-1"
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=Exception("db error")
        )
        await connector._handle_record_updates(update)  # Should not raise


# ---------------------------------------------------------------------------
# _sync_user_personal_drive
# ---------------------------------------------------------------------------

class TestSyncUserPersonalDrive:
    async def test_no_drive_source_returns(self, connector):
        connector.drive_data_source = None
        await connector._sync_user_personal_drive("drive-1")

    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_user_info_returns(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(return_value={"user": {}})
        await connector._sync_user_personal_drive("drive-1")

    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_when_no_sync_point(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(return_value={
            "user": {"permissionId": "pid-1", "emailAddress": "user@example.com"},
        })
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_perform_full_sync", new_callable=AsyncMock) as mock_full:
            await connector._sync_user_personal_drive("drive-1")
            mock_full.assert_called_once()

    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_sync_when_sync_point_exists(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.about_get = AsyncMock(return_value={
            "user": {"permissionId": "pid-1", "emailAddress": "user@example.com"},
        })
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"pageToken": "token-123"}
        )
        with patch.object(connector, "_perform_incremental_sync", new_callable=AsyncMock) as mock_inc:
            await connector._sync_user_personal_drive("drive-1")
            mock_inc.assert_called_once()


# ---------------------------------------------------------------------------
# run_sync
# ---------------------------------------------------------------------------
class TestRunSync:
    @patch("app.connectors.sources.google.drive.individual.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.drive_data_source = None
        with pytest.raises(GoogleDriveError):
            await connector.run_sync()

    @patch("app.connectors.sources.google.drive.individual.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_successful_sync(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.drive_data_source = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(return_value={
            "user": {"permissionId": "pid-1", "emailAddress": "user@example.com", "displayName": "User"},
            "storageQuota": {},
        })
        connector.drive_data_source.files_get = AsyncMock(return_value={"id": "drive-root-id"})
        connector._get_fresh_datasource = AsyncMock()
        connector._create_app_user = AsyncMock()
        connector._create_personal_record_group = AsyncMock(return_value=MagicMock())
        connector._sync_user_personal_drive = AsyncMock()
        await connector.run_sync()
        connector._sync_user_personal_drive.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_incremental_sync
# ---------------------------------------------------------------------------
class TestRunIncrementalSync:
    async def test_delegates_to_run_sync(self, connector):
        connector._sync_user_personal_drive = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_user_personal_drive.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_connection_and_access
# ---------------------------------------------------------------------------
class TestTestConnectionAndAccess:
    async def test_not_initialized(self, connector):
        connector.drive_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_success(self, connector):
        connector.drive_data_source.about_get = AsyncMock(return_value={
            "user": {"emailAddress": "user@example.com"}
        })
        result = await connector.test_connection_and_access()
        assert result is True

    async def test_exception(self, connector):
        connector.google_client.get_client = MagicMock(side_effect=Exception("err"))
        result = await connector.test_connection_and_access()
        assert result is False


# ---------------------------------------------------------------------------
# get_signed_url
# ---------------------------------------------------------------------------
class TestGetSignedUrl:
    def test_returns_none(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------
class TestCleanup:
    async def test_cleanup(self, connector):
        connector.drive_data_source = MagicMock()
        connector.google_client = MagicMock()
        await connector.cleanup()
        assert connector.drive_data_source is None


# ---------------------------------------------------------------------------
# handle_webhook_notification
# ---------------------------------------------------------------------------
class TestHandleWebhookNotification:
    def test_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


# ---------------------------------------------------------------------------
# _create_personal_record_group
# ---------------------------------------------------------------------------
class TestCreatePersonalRecordGroup:
    async def test_creates_group(self, connector):
        result = await connector._create_personal_record_group("uid-1", "u@test.com", "My Drive", "drive-1")
        assert result is not None
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ---------------------------------------------------------------------------
# _create_app_user
# ---------------------------------------------------------------------------
class TestCreateAppUser:
    async def test_creates_user(self, connector):
        user_about = {
            "user": {"permissionId": "pid-1", "emailAddress": "u@test.com", "displayName": "User"}
        }
        await connector._create_app_user(user_about)
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()


# ---------------------------------------------------------------------------
# _parse_datetime edge cases
# ---------------------------------------------------------------------------
class TestParseDatetimeEdgeCases:
    def test_integer_value(self, connector):
        result = connector._parse_datetime(12345)
        assert result is None

    def test_empty_string(self, connector):
        result = connector._parse_datetime("")
        assert result is None


# ---------------------------------------------------------------------------
# _pass_date_filters edge cases
# ---------------------------------------------------------------------------
class TestPassDateFiltersEdgeCases:
    def test_folder_always_passes(self, connector):
        meta = {"mimeType": "application/vnd.google-apps.folder"}
        assert connector._pass_date_filters(meta) is True

    def test_no_filters_passes(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_date_filters(meta) is True


# ---------------------------------------------------------------------------
# _pass_extension_filter edge cases
# ---------------------------------------------------------------------------
class TestPassExtensionFilterEdgeCases:
    def test_no_filter(self, connector):
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_folder_always_passes(self, connector):
        meta = _make_file_metadata(mime_type="application/vnd.google-apps.folder")
        assert connector._pass_extension_filter(meta) is True


# ---------------------------------------------------------------------------
# reindex_records
# ---------------------------------------------------------------------------
class TestReindexRecords:
    async def test_empty_records(self, connector):
        await connector.reindex_records([])

    async def test_not_initialized(self, connector):
        connector.drive_data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    async def test_updated_and_non_updated(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(return_value={
            "user": {"permissionId": "pid-1", "emailAddress": "user@example.com"}
        })
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(MagicMock(), []), None]
        )
        connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await connector.reindex_records([MagicMock(id="r1"), MagicMock(id="r2")])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_filter_options
# ---------------------------------------------------------------------------
class TestGetFilterOptions:
    async def test_unsupported(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("invalid")
