"""Deep sync loop tests for GoogleDriveIndividualConnector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.models.entities import FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_drive_individual_deep")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_data_store_provider(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
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
    parents=None, shared=False, head_revision_id="rev-1",
    file_extension="txt", size=1024, owners=None, version=None,
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
    with patch(
        "app.connectors.sources.google.drive.individual.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.individual.connector.SyncPoint"
    ) as MockSP:
        mock_sp = AsyncMock()
        mock_sp.read_sync_point = AsyncMock(return_value=None)
        mock_sp.update_sync_point = AsyncMock()
        MockSP.return_value = mock_sp

        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-ind-1"
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
            "credentials": {"access_token": "tok", "refresh_token": "ref"},
        })

        conn = GoogleDriveIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-ind-1",
            scope="personal",
            created_by="test",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.google_client = MagicMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {}}
        yield conn


# ---------------------------------------------------------------------------
# _process_drive_item deep paths
# ---------------------------------------------------------------------------

class TestIndividualProcessDriveItem:
    """Deep tests for individual connector's _process_drive_item."""

    async def test_new_file_basic(self, connector):
        metadata = _make_file_metadata(file_id="f1", parents=["d1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.external_record_id == "f1"

    async def test_no_file_id_returns_none(self, connector):
        result = await connector._process_drive_item(
            metadata={}, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result is None

    async def test_existing_record_name_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "old.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "d1"
        existing.parent_external_record_id = "parent-1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        metadata = _make_file_metadata(file_id="f1", name="new.txt", parents=["parent-1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_existing_record_content_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "old-rev"
        existing.external_record_group_id = "d1"
        existing.parent_external_record_id = "parent-1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        metadata = _make_file_metadata(
            file_id="f1", name="test.txt", head_revision_id="new-rev", parents=["parent-1"],
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.content_changed is True

    async def test_existing_record_drive_id_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "old-drive"
        existing.parent_external_record_id = "parent-1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        metadata = _make_file_metadata(file_id="f1", name="test.txt", parents=["parent-1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="new-drive",
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_parent_change_detected(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "d1"
        existing.parent_external_record_id = "old-parent"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "completed"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        metadata = _make_file_metadata(file_id="f1", name="test.txt", parents=["new-parent"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.metadata_changed is True

    async def test_folder_is_file_false(self, connector):
        metadata = _make_file_metadata(
            file_id="folder-1", name="Folder",
            mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value, parents=["d1"],
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.record.is_file is False

    async def test_shared_file_marked(self, connector):
        metadata = _make_file_metadata(file_id="f1", shared=True, parents=["d1"])
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.record.is_shared is True

    async def test_version_fallback(self, connector):
        metadata = _make_file_metadata(file_id="f1", parents=["d1"])
        metadata.pop("headRevisionId")
        metadata["version"] = "99"
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.record.external_revision_id == "99"

    async def test_extension_from_name_when_field_missing(self, connector):
        metadata = _make_file_metadata(file_id="f1", name="doc.docx", parents=["d1"])
        metadata.pop("fileExtension", None)
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.record.extension == "docx"

    async def test_exception_returns_none(self, connector):
        provider = MagicMock()

        @asynccontextmanager
        async def _fail():
            raise RuntimeError("fail")
            yield  # noqa

        provider.transaction = _fail
        connector.data_store_provider = provider
        result = await connector._process_drive_item(
            metadata=_make_file_metadata(file_id="f1", parents=["d1"]),
            user_id="u1", user_email="u@t.com", drive_id="d1",
        )
        assert result is None

    async def test_no_content_change_preserves_indexing_status(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.external_record_group_id = "d1"
        existing.parent_external_record_id = "parent-1"
        existing.version = 1
        existing.indexing_status = "completed"
        existing.extraction_status = "extracted"

        connector.data_store_provider = _make_mock_data_store_provider(existing)
        metadata = _make_file_metadata(
            file_id="f1", name="test.txt", parents=["parent-1"], head_revision_id="rev-1",
        )
        result = await connector._process_drive_item(
            metadata=metadata, user_id="u1", user_email="user@t.com", drive_id="d1",
        )
        assert result.record.indexing_status == "completed"
        assert result.record.extraction_status == "extracted"


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestIndividualParseDatetime:
    def test_none(self, connector):
        assert connector._parse_datetime(None) is None

    def test_valid_string(self, connector):
        result = connector._parse_datetime("2025-06-15T10:30:00Z")
        assert isinstance(result, int)

    def test_invalid_string(self, connector):
        assert connector._parse_datetime("garbage") is None

    def test_datetime_object(self, connector):
        from datetime import datetime, timezone
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# _pass_date_filters
# ---------------------------------------------------------------------------

class TestIndividualDateFilters:
    def test_folder_always_passes(self, connector):
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_date_filters(metadata) is True

    def test_no_filters_passes(self, connector):
        metadata = {"mimeType": "text/plain"}
        assert connector._pass_date_filters(metadata) is True

    def test_created_filter_before_cutoff_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == "created" else None)

        metadata = {"mimeType": "text/plain", "createdTime": "2025-01-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False

    def test_modified_filter_after_cutoff_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2025-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == "modified" else None)

        metadata = {"mimeType": "text/plain", "modifiedTime": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(metadata) is False


# ---------------------------------------------------------------------------
# _pass_extension_filter
# ---------------------------------------------------------------------------

class TestIndividualExtensionFilters:
    def test_folder_always_passes(self, connector):
        metadata = {"mimeType": MimeTypes.GOOGLE_DRIVE_FOLDER.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_no_filter_passes(self, connector):
        metadata = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(metadata) is True

    def test_google_docs_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(metadata) is True

    def test_google_docs_not_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )

        metadata = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(metadata) is False

    def test_file_without_extension_in_filter_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )

        metadata = {"mimeType": "text/plain", "name": "Makefile"}
        assert connector._pass_extension_filter(metadata) is False

    def test_file_without_extension_not_in_filter_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )

        metadata = {"mimeType": "text/plain", "name": "Makefile"}
        assert connector._pass_extension_filter(metadata) is True

    def test_extension_from_name_matches_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )

        metadata = {"mimeType": "application/pdf", "name": "report.pdf"}
        assert connector._pass_extension_filter(metadata) is True
