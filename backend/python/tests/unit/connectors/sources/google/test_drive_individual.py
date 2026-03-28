"""Tests for GoogleDriveIndividualConnector (app/connectors/sources/google/drive/individual/connector.py)."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_drive_individual")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    """Create a mock transaction store that returns existing_record on lookup."""
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    """Create a DataStoreProvider mock whose transaction() yields a mock tx store."""
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_file_metadata(
    file_id="file-1",
    name="test.txt",
    mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z",
    modified_time="2025-01-15T00:00:00Z",
    parents=None,
    shared=False,
    head_revision_id="rev-1",
    file_extension="txt",
    size=1024,
    web_view_link="https://drive.google.com/file/d/file-1/view",
):
    meta = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "createdTime": created_time,
        "modifiedTime": modified_time,
        "shared": shared,
        "headRevisionId": head_revision_id,
        "fileExtension": file_extension,
        "size": size,
        "webViewLink": web_view_link,
    }
    if parents is not None:
        meta["parents"] = parents
    return meta


def _make_folder_metadata(folder_id="folder-1", name="My Folder", parents=None):
    return _make_file_metadata(
        file_id=folder_id,
        name=name,
        mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
        parents=parents,
        head_revision_id=None,
        file_extension=None,
        size=0,
    )


@pytest.fixture
def connector():
    """Create a GoogleDriveIndividualConnector with fully mocked dependencies."""
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
        config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {
                "access_token": "test-token",
                "refresh_token": "test-refresh",
            }
        })

        conn = GoogleDriveIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-conn-1",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.google_client = MagicMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


# ---------------------------------------------------------------------------
# _process_drive_item
# ---------------------------------------------------------------------------

class TestProcessDriveItem:
    """Tests for individual file processing logic."""

    async def test_returns_none_for_no_file_id(self, connector):
        result = await connector._process_drive_item(
            metadata={"name": "orphan"},
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result is None

    async def test_new_file_returns_record_update(self, connector):
        metadata = _make_file_metadata()
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result is not None
        assert result.is_new is True
        assert result.is_deleted is False
        assert result.record.record_name == "test.txt"
        assert result.record.external_record_id == "file-1"
        assert result.record.record_type == RecordType.FILE
        assert result.record.is_file is True
        assert result.record.mime_type == "text/plain"
        assert result.record.extension == "txt"

    async def test_folder_detected_as_non_file(self, connector):
        metadata = _make_folder_metadata()
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_existing_record_no_changes(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        metadata = _make_file_metadata(parents=None)
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is False
        # Should preserve indexing status when no content change
        assert result.record.indexing_status == ProgressStatus.COMPLETED.value

    async def test_existing_record_name_changed(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "old_name.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 1
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        metadata = _make_file_metadata(name="new_name.txt")
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_existing_record_content_changed(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-0"
        existing.external_record_group_id = "drive-1"
        existing.parent_external_record_id = None
        existing.version = 1
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        metadata = _make_file_metadata(head_revision_id="rev-1")
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.is_updated is True
        assert result.content_changed is True

    async def test_shared_file_flag(self, connector):
        metadata = _make_file_metadata(shared=True)
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.record.is_shared is True

    async def test_permissions_include_owner(self, connector):
        metadata = _make_file_metadata()
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.OWNER
        assert result.new_permissions[0].email == "user@example.com"

    async def test_google_doc_mime_type_detected(self, connector):
        metadata = _make_file_metadata(
            mime_type=MimeTypes.GOOGLE_DOCS.value,
            file_extension=None,
        )
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.record.mime_type == MimeTypes.GOOGLE_DOCS.value
        assert result.record.is_file is True

    async def test_extension_extracted_from_name_when_not_in_metadata(self, connector):
        metadata = _make_file_metadata(file_extension=None, name="report.pdf")
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.record.extension == "pdf"

    async def test_parent_set_when_different_from_drive(self, connector):
        metadata = _make_file_metadata(parents=["folder-abc"])
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.record.parent_external_record_id == "folder-abc"

    async def test_parent_none_when_matches_drive_id(self, connector):
        metadata = _make_file_metadata(parents=["drive-1"])
        result = await connector._process_drive_item(
            metadata=metadata,
            user_id="u1",
            user_email="user@example.com",
            drive_id="drive-1",
        )
        assert result.record.parent_external_record_id is None


# ---------------------------------------------------------------------------
# Date filters
# ---------------------------------------------------------------------------

class TestDateFilters:
    """Tests for _pass_date_filters."""

    def test_folders_always_pass(self, connector):
        metadata = _make_folder_metadata()
        assert connector._pass_date_filters(metadata) is True

    def test_no_filters_configured_passes(self, connector):
        metadata = _make_file_metadata()
        assert connector._pass_date_filters(metadata) is True

    def test_created_filter_rejects_old_file(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if key.value == "created" else None

        # File created before start date
        metadata = _make_file_metadata(created_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(metadata) is False

    def test_modified_filter_passes_recent_file(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00Z", None)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if key.value == "modified" else None

        metadata = _make_file_metadata(modified_time="2025-06-15T00:00:00Z")
        assert connector._pass_date_filters(metadata) is True


# ---------------------------------------------------------------------------
# Extension filters
# ---------------------------------------------------------------------------

class TestExtensionFilters:
    """Tests for _pass_extension_filter."""

    def test_folders_always_pass(self, connector):
        metadata = _make_folder_metadata()
        assert connector._pass_extension_filter(metadata) is True

    def test_no_filter_passes_everything(self, connector):
        metadata = _make_file_metadata()
        assert connector._pass_extension_filter(metadata) is True

    def test_in_filter_allows_matching_extension(self, connector):
        from app.connectors.core.registry.filters import FilterOperator

        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt", "pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        metadata = _make_file_metadata(file_extension="txt")
        assert connector._pass_extension_filter(metadata) is True

    def test_in_filter_rejects_non_matching_extension(self, connector):
        from app.connectors.core.registry.filters import FilterOperator

        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        metadata = _make_file_metadata(file_extension="docx")
        assert connector._pass_extension_filter(metadata) is False

    def test_google_doc_mime_type_filter_in(self, connector):
        from app.connectors.core.registry.filters import FilterOperator

        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_operator

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        metadata = _make_file_metadata(mime_type=MimeTypes.GOOGLE_DOCS.value, file_extension=None)
        assert connector._pass_extension_filter(metadata) is True


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    """Tests for _parse_datetime utility method."""

    def test_none_returns_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_iso_string_returns_epoch_ms(self, connector):
        result = connector._parse_datetime("2025-01-01T00:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid_string_returns_none(self, connector):
        assert connector._parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# _handle_record_updates
# ---------------------------------------------------------------------------

class TestHandleRecordUpdates:
    """Tests for _handle_record_updates routing."""

    async def test_deleted_record(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="file-1",
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once()

    async def test_metadata_update(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        mock_record = MagicMock()
        mock_record.record_name = "updated.txt"

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
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    async def test_content_update(self, connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

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
        connector.data_entities_processor.on_record_content_update.assert_called_once()


# ---------------------------------------------------------------------------
# Full sync workflow
# ---------------------------------------------------------------------------

class TestFullSync:
    """Tests for full sync flow."""

    async def test_full_sync_processes_files(self, connector):
        # Mock drive_data_source methods
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "token-123"}
        )
        connector.drive_data_source.files_list = AsyncMock(return_value={
            "files": [_make_file_metadata()],
            # No nextPageToken = single page
        })

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_full_sync(
                sync_point_key="personal_drive",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                drive_id="drive-1",
            )

        # Should have processed records
        connector.data_entities_processor.on_new_records.assert_called()
        # Should have saved sync point
        connector.drive_delta_sync_point.update_sync_point.assert_called()

    async def test_full_sync_handles_empty_response(self, connector):
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "token-123"}
        )
        connector.drive_data_source.files_list = AsyncMock(return_value={"files": []})

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_full_sync(
                sync_point_key="personal_drive",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                drive_id="drive-1",
            )

        # on_new_records should not be called since no files
        connector.data_entities_processor.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Incremental sync workflow
# ---------------------------------------------------------------------------

class TestIncrementalSync:
    """Tests for incremental (changes API) sync flow."""

    async def test_incremental_sync_processes_changes(self, connector):
        connector.drive_data_source.changes_list = AsyncMock(return_value={
            "changes": [
                {"file": _make_file_metadata(file_id="changed-1")},
            ],
            "newStartPageToken": "new-token-456",
        })

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_incremental_sync(
                sync_point_key="personal_drive",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                page_token="old-token-123",
                drive_id="drive-1",
            )

        connector.data_entities_processor.on_new_records.assert_called()

    async def test_incremental_sync_handles_deletions(self, connector):
        connector.drive_data_source.changes_list = AsyncMock(return_value={
            "changes": [
                {"removed": True, "fileId": "deleted-file-1"},
            ],
            "newStartPageToken": "new-token",
        })

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_incremental_sync(
                sync_point_key="personal_drive",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                page_token="old-token",
                drive_id="drive-1",
            )

        connector.data_entities_processor.on_record_deleted.assert_called_once()

    async def test_incremental_sync_updates_sync_point(self, connector):
        connector.drive_data_source.changes_list = AsyncMock(return_value={
            "changes": [],
            "newStartPageToken": "updated-token-789",
        })

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_incremental_sync(
                sync_point_key="personal_drive",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                page_token="old-token",
                drive_id="drive-1",
            )

        connector.drive_delta_sync_point.update_sync_point.assert_called_with(
            "personal_drive",
            {"pageToken": "updated-token-789"},
        )


# ---------------------------------------------------------------------------
# run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    """Tests for the main run_sync orchestration."""

    async def test_run_sync_orchestrates_flow(self, connector):
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch(
                 "app.connectors.sources.google.drive.individual.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(FilterCollection(), FilterCollection()),
             ), \
             patch(
                 "app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials",
                 new_callable=AsyncMock,
             ):
            connector.drive_data_source.about_get = AsyncMock(return_value={
                "user": {
                    "displayName": "Test User",
                    "emailAddress": "user@example.com",
                    "permissionId": "perm-1",
                },
                "storageQuota": {},
            })
            connector.drive_data_source.files_get = AsyncMock(return_value={"id": "root-drive-id"})
            connector.drive_data_source.changes_get_start_page_token = AsyncMock(
                return_value={"startPageToken": "start-token"}
            )
            connector.drive_data_source.files_list = AsyncMock(return_value={"files": []})

            # Mock sync point to return None (no existing sync, triggers full sync)
            connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)

            await connector.run_sync()

            # Should create user
            connector.data_entities_processor.on_new_app_users.assert_called_once()
            # Should create record group
            connector.data_entities_processor.on_new_record_groups.assert_called_once()


# ---------------------------------------------------------------------------
# test_connection_and_access
# ---------------------------------------------------------------------------

class TestConnectionTest:
    """Tests for test_connection_and_access."""

    async def test_returns_true_when_initialized(self, connector):
        connector.google_client.get_client.return_value = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is True

    async def test_returns_false_when_data_source_not_initialized(self, connector):
        connector.drive_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_returns_false_when_client_is_none(self, connector):
        connector.google_client.get_client.return_value = None
        result = await connector.test_connection_and_access()
        assert result is False


# ---------------------------------------------------------------------------
# Batch processing in full sync
# ---------------------------------------------------------------------------

class TestBatchProcessing:
    """Tests for batch processing during full sync."""

    async def test_batch_flushed_at_batch_size(self, connector):
        connector.batch_size = 2

        files = [
            _make_file_metadata(file_id=f"file-{i}", name=f"file{i}.txt")
            for i in range(5)
        ]

        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "token-1"}
        )
        connector.drive_data_source.files_list = AsyncMock(return_value={
            "files": files,
        })

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._perform_full_sync(
                sync_point_key="test",
                org_id="org-123",
                user_id="u1",
                user_email="user@example.com",
                drive_id="drive-1",
            )

        # With batch_size=2 and 5 files: 2 full batches + 1 remainder
        assert connector.data_entities_processor.on_new_records.call_count == 3
