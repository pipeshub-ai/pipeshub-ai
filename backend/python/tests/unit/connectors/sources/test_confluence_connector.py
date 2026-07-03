"""Tests for app.connectors.sources.atlassian.confluence_cloud.connector."""

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.atlassian.core.confluence_html import HtmlImageContext
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    CONTENT_EXPAND_PARAMS,
    FOLDER_EXPAND_PARAMS,
    PSEUDO_USER_GROUP_PREFIX,
    TIME_OFFSET_HOURS,
    ConfluenceConnector,
    extract_media_from_adf,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
    WebpageRecord,
)
from app.models.blocks import ChildRecord, ChildType, GroupSubType
from app.models.permission import EntityType, Permission, PermissionType
from fastapi import HTTPException

# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.confluence")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-conf-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-conf-1", "team", "test-user")


def _make_mock_response(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


def _folder_data(folder_id="folder1", title="Test Folder", space_id="123", parent_id=None, parent_type=None):
    """Create mock folder data from Confluence API."""
    data = {
        "id": folder_id,
        "type": "folder",
        "title": title,
        "space": {"id": space_id, "key": "TEST"},
        "version": {"number": 1, "when": "2024-01-01T10:00:00.000Z"},
        "history": {
            "createdDate": "2024-01-01T09:00:00.000Z",
            "lastUpdated": {"when": "2024-01-01T10:00:00.000Z", "number": 1},
        },
        "_links": {
            "webui": "/spaces/TEST/pages/folder1",
            "self": "https://example.atlassian.net/wiki/rest/api/content/folder1",
        },
    }

    if parent_id:
        ancestor = {"id": parent_id}
        if parent_type:
            ancestor["type"] = parent_type
        data["ancestors"] = [ancestor]

    return data


# ===========================================================================
# Constants
# ===========================================================================


class TestConfluenceConstants:

    def test_time_offset_hours(self):
        assert TIME_OFFSET_HOURS == 24

    def test_content_expand_params(self):
        assert "ancestors" in CONTENT_EXPAND_PARAMS
        assert "history.lastUpdated" in CONTENT_EXPAND_PARAMS
        assert "space" in CONTENT_EXPAND_PARAMS

    def test_folder_expand_params(self):
        assert "ancestors" in FOLDER_EXPAND_PARAMS
        assert "history.lastUpdated" in FOLDER_EXPAND_PARAMS
        assert "space" in FOLDER_EXPAND_PARAMS
        assert "children.attachment" not in FOLDER_EXPAND_PARAMS
        assert "childTypes.comment" not in FOLDER_EXPAND_PARAMS

    def test_pseudo_user_group_prefix(self):
        assert PSEUDO_USER_GROUP_PREFIX == "[Pseudo-User]"


# ===========================================================================
# Folder sync
# ===========================================================================


class TestTransformToFolderFileRecord:
    """Test _transform_to_folder_file_record for folders."""

    def test_transform_folder_basic(self):
        c = _make_connector()
        data = _folder_data()

        rec = c._transform_to_folder_file_record(data)

        assert rec is not None
        assert isinstance(rec, FileRecord)
        assert rec.record_type == RecordType.FILE
        assert rec.is_file is False
        assert rec.record_name == "Test Folder"
        assert rec.external_record_id == "folder1"
        assert rec.mime_type == MimeTypes.FOLDER.value
        assert rec.external_record_group_id == "123"
        assert rec.size_in_bytes == 0
        assert rec.parent_external_record_id is None
        assert rec.parent_record_type is None

    def test_transform_folder_with_folder_parent(self):
        c = _make_connector()
        data = _folder_data(parent_id="parent-folder1", parent_type="folder")

        rec = c._transform_to_folder_file_record(data)

        assert rec is not None
        assert rec.record_type == RecordType.FILE
        assert rec.is_file is False
        assert rec.parent_external_record_id == "parent-folder1"
        assert rec.parent_record_type == RecordType.FILE

    def test_transform_folder_with_page_parent(self):
        c = _make_connector()
        data = _folder_data(parent_id="parent-page1", parent_type="page")

        rec = c._transform_to_folder_file_record(data)

        assert rec is not None
        assert rec.parent_external_record_id == "parent-page1"
        assert rec.parent_record_type == RecordType.CONFLUENCE_PAGE

    def test_transform_folder_missing_type_fallback(self):
        c = _make_connector()
        data = _folder_data(parent_id="parent1", parent_type=None)

        rec = c._transform_to_folder_file_record(data)

        assert rec is not None
        assert rec.parent_external_record_id == "parent1"
        assert rec.parent_record_type == RecordType.FILE


class TestTransformPageWithFolderParent:
    """Test that pages with folder parents get correct parent_record_type."""

    def test_transform_page_with_folder_parent(self):
        c = _make_connector()
        data = {
            "id": "page1",
            "type": "page",
            "title": "Test Page",
            "space": {"id": "123", "key": "TEST"},
            "version": {"number": 1, "when": "2024-01-01T10:00:00.000Z"},
            "history": {
                "createdDate": "2024-01-01T09:00:00.000Z",
                "lastUpdated": {"when": "2024-01-01T10:00:00.000Z", "number": 1},
            },
            "ancestors": [{"id": "folder1", "type": "folder"}],
            "_links": {
                "webui": "/spaces/TEST/pages/page1",
                "self": "https://example.atlassian.net/wiki/rest/api/content/page1",
            },
        }

        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)

        assert rec is not None
        assert rec.record_type == RecordType.CONFLUENCE_PAGE
        assert rec.parent_external_record_id == "folder1"
        assert rec.parent_record_type == RecordType.FILE

    def test_transform_page_with_page_parent(self):
        c = _make_connector()
        data = {
            "id": "page1",
            "type": "page",
            "title": "Test Page",
            "space": {"id": "123", "key": "TEST"},
            "version": {"number": 1, "when": "2024-01-01T10:00:00.000Z"},
            "history": {
                "createdDate": "2024-01-01T09:00:00.000Z",
                "lastUpdated": {"when": "2024-01-01T10:00:00.000Z", "number": 1},
            },
            "ancestors": [{"id": "parent-page1", "type": "page"}],
            "_links": {
                "webui": "/spaces/TEST/pages/page1",
                "self": "https://example.atlassian.net/wiki/rest/api/content/page1",
            },
        }

        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)

        assert rec is not None
        assert rec.record_type == RecordType.CONFLUENCE_PAGE
        assert rec.parent_external_record_id == "parent-page1"
        assert rec.parent_record_type == RecordType.CONFLUENCE_PAGE


@pytest.mark.asyncio
class TestSyncFolders:
    """Test _sync_folders method."""

    async def test_sync_folders_basic(self):
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        mock_datasource.get_folders_v1.assert_called_once()
        call_args = mock_datasource.get_folders_v1.call_args[1]
        assert call_args["space_key"] == "TEST"
        assert call_args["expand"] == FOLDER_EXPAND_PARAMS
        assert call_args["time_offset_hours"] == TIME_OFFSET_HOURS

        c.data_entities_processor.on_new_records.assert_called_once()
        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 2

        for record_tuple in saved_records:
            record, perms = record_tuple
            assert isinstance(record, FileRecord)
            assert record.record_type == RecordType.FILE
            assert record.is_file is False
            assert record.mime_type == MimeTypes.FOLDER.value

    async def test_sync_folders_with_permissions(self):
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        test_permission = Permission(
            type=PermissionType.READ,
            entity_type=EntityType.USER,
            external_id="user1",
        )

        mock_fetch_permissions = AsyncMock(return_value=[test_permission])

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(
                c,
                "_fetch_page_permissions",
                mock_fetch_permissions,
            ):
                await c._sync_folders("TEST")

        mock_fetch_permissions.assert_called_once_with("folder1")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1
        record, perms = saved_records[0]
        assert len(perms) == 1
        assert perms[0] == test_permission

    async def test_sync_folders_skips_invalid_folder(self):
        """Test that folders with missing id or title are skipped."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    {"id": "folder1", "title": None},  # Missing title
                    {"id": None, "title": "Folder 2"},  # Missing id
                    _folder_data("folder3", "Folder 3"),  # Valid
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Only one valid folder should be saved
        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_handles_transform_error(self):
        """Test that transformation errors are handled gracefully."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                with patch.object(c, "_transform_to_folder_file_record", side_effect=[None, MagicMock()]):
                    await c._sync_folders("TEST")

        # Only the second folder should be saved (first returned None)
        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_handles_item_processing_exception(self):
        """Test that exceptions during item processing are caught and logged."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, side_effect=[Exception("API error"), []]):
                await c._sync_folders("TEST")

        # Second folder should still be processed
        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_with_pagination(self):
        """Test folder sync with multiple pages (cursor pagination)."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        
        # First page with next link
        first_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {"next": "/api/content/search?cursor=abc123"},
            },
        )
        # Second page without next link
        second_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder2", "Folder 2")],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(side_effect=[first_response, second_response])

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Should call get_folders_v1 twice (pagination)
        assert mock_datasource.get_folders_v1.call_count == 2
        # Should save 2 folders total
        assert c.data_entities_processor.on_new_records.call_count == 2

    async def test_sync_folders_updates_checkpoint(self):
        """Test that sync checkpoint is updated after successful sync."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Checkpoint should be updated
        c.pages_sync_point.update_sync_point.assert_called_once()
        args = c.pages_sync_point.update_sync_point.call_args[0]
        assert "confluence_folders" in args[0]
        assert "last_sync_time" in args[1]

    async def test_sync_folders_inherit_permissions_logic(self):
        """Test that inherit_permissions is set correctly based on READ permissions."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        read_perm = Permission(
            type=PermissionType.READ,
            entity_type=EntityType.USER,
            external_id="user1",
        )
        write_perm = Permission(
            type=PermissionType.WRITE,
            entity_type=EntityType.USER,
            external_id="user2",
        )

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(
                c, "_fetch_page_permissions", new_callable=AsyncMock, side_effect=[[read_perm], [write_perm]]
            ):
                await c._sync_folders("TEST")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        folder1, perms1 = saved_records[0]
        folder2, perms2 = saved_records[1]
        
        # Folder1 has READ permission, so inherit_permissions should be False
        assert folder1.inherit_permissions is False
        # Folder2 has only WRITE permission, inherit_permissions should remain default (True)
        # (it's set to True by default in the model)

    async def test_sync_folders_handles_api_failure(self):
        """Test that API failures are handled and raised."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_datasource = MagicMock()
        # API returns non-success status
        failed_response = _make_mock_response(500, {})
        mock_datasource.get_folders_v1 = AsyncMock(return_value=failed_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            await c._sync_folders("TEST")

        # Should not call on_new_records if API fails
        c.data_entities_processor.on_new_records.assert_not_called()

    async def test_sync_folders_handles_empty_results(self):
        """Test that empty results are handled correctly."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        empty_response = _make_mock_response(200, {"results": [], "_links": {}})
        mock_datasource.get_folders_v1 = AsyncMock(return_value=empty_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Should not update checkpoint if nothing was synced
        c.pages_sync_point.update_sync_point.assert_not_called()

    async def test_sync_folders_with_incremental_sync(self):
        """Test incremental sync uses last sync time."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        # Return last sync time
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Check that modified_after was passed to the API
        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-01-01T00:00:00.000Z"

    async def test_sync_folders_with_modified_filter(self):
        """Test sync with modified date filter from sync_filters."""
        c = _make_connector()
        
        # Create mock filter with modified dates
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-02-01T00:00:00.000Z", "2024-03-01T00:00:00.000Z")
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Check that modified dates were passed
        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-02-01T00:00:00.000Z"
        assert call_kwargs["modified_before"] == "2024-03-01T00:00:00.000Z"

    async def test_sync_folders_with_created_filter(self):
        """Test sync with created date filter from sync_filters."""
        c = _make_connector()
        
        # Create mock filter with created dates
        mock_created_filter = MagicMock()
        mock_created_filter.get_datetime_iso = MagicMock(
            return_value=("2024-01-01T00:00:00.000Z", "2024-01-31T23:59:59.000Z")
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_created_filter if key == SyncFilterKey.CREATED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Check that created dates were passed
        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["created_after"] == "2024-01-01T00:00:00.000Z"
        assert call_kwargs["created_before"] == "2024-01-31T23:59:59.000Z"

    async def test_sync_folders_with_filter_and_checkpoint(self):
        """Test sync when both filter and checkpoint exist (uses max)."""
        c = _make_connector()
        
        # Create mock filter with modified date that's older than checkpoint
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-01-01T00:00:00.000Z", None)
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        # Checkpoint is newer than filter
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-02-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Should use the newer checkpoint time
        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-02-01T00:00:00.000Z"

    async def test_sync_folders_with_filter_only(self):
        """Test sync with filter but no checkpoint."""
        c = _make_connector()
        
        # Create mock filter
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-03-01T00:00:00.000Z", None)
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)  # No checkpoint
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        # Should use the filter time
        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-03-01T00:00:00.000Z"

    async def test_sync_folders_pagination_with_invalid_cursor(self):
        """Test pagination when cursor extraction returns None."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        
        # Response with next link but cursor extraction will fail
        response_with_bad_cursor = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {"next": "/invalid-cursor-format"},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=response_with_bad_cursor)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                with patch.object(c, "_extract_cursor_from_next_link", return_value=None):
                    await c._sync_folders("TEST")

        # Should only call API once (stops when cursor is None)
        assert mock_datasource.get_folders_v1.call_count == 1

    async def test_sync_folders_handles_exception(self):
        """Test that exceptions during sync are logged and raised."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_datasource = MagicMock()
        # Simulate exception during API call
        mock_datasource.get_folders_v1 = AsyncMock(side_effect=Exception("Network error"))

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            # Exception should be raised
            with pytest.raises(Exception, match="Network error"):
                await c._sync_folders("TEST")


    async def test_sync_folders_skips_invalid_folder(self):
        """Test that folders with missing id or title are skipped."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    {"id": "folder1", "title": None},
                    {"id": None, "title": "Folder 2"},
                    _folder_data("folder3", "Folder 3"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_handles_transform_error(self):
        """Test that transformation errors are handled gracefully."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                with patch.object(c, "_transform_to_folder_file_record", side_effect=[None, MagicMock()]):
                    await c._sync_folders("TEST")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_handles_item_processing_exception(self):
        """Test that exceptions during item processing are caught and logged."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, side_effect=[Exception("API error"), []]):
                await c._sync_folders("TEST")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved_records) == 1

    async def test_sync_folders_with_pagination(self):
        """Test folder sync with multiple pages (cursor pagination)."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        
        first_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {"next": "/api/content/search?cursor=abc123"},
            },
        )
        second_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder2", "Folder 2")],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(side_effect=[first_response, second_response])

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        assert mock_datasource.get_folders_v1.call_count == 2
        assert c.data_entities_processor.on_new_records.call_count == 2

    async def test_sync_folders_updates_checkpoint(self):
        """Test that sync checkpoint is updated after successful sync."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        c.pages_sync_point.update_sync_point.assert_called_once()
        args = c.pages_sync_point.update_sync_point.call_args[0]
        assert "confluence_folders" in args[0]
        assert "last_sync_time" in args[1]

    async def test_sync_folders_inherit_permissions_logic(self):
        """Test that inherit_permissions is set correctly based on READ permissions."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {
                "results": [
                    _folder_data("folder1", "Folder 1"),
                    _folder_data("folder2", "Folder 2"),
                ],
                "_links": {},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        read_perm = Permission(
            type=PermissionType.READ,
            entity_type=EntityType.USER,
            external_id="user1",
        )
        write_perm = Permission(
            type=PermissionType.WRITE,
            entity_type=EntityType.USER,
            external_id="user2",
        )

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(
                c, "_fetch_page_permissions", new_callable=AsyncMock, side_effect=[[read_perm], [write_perm]]
            ):
                await c._sync_folders("TEST")

        saved_records = c.data_entities_processor.on_new_records.call_args[0][0]
        folder1, perms1 = saved_records[0]
        folder2, perms2 = saved_records[1]
        
        assert folder1.inherit_permissions is False

    async def test_sync_folders_handles_api_failure(self):
        """Test that API failures are handled and raised."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_datasource = MagicMock()
        failed_response = _make_mock_response(500, {})
        mock_datasource.get_folders_v1 = AsyncMock(return_value=failed_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            await c._sync_folders("TEST")

        c.data_entities_processor.on_new_records.assert_not_called()

    async def test_sync_folders_handles_empty_results(self):
        """Test that empty results are handled correctly."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        empty_response = _make_mock_response(200, {"results": [], "_links": {}})
        mock_datasource.get_folders_v1 = AsyncMock(return_value=empty_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        c.pages_sync_point.update_sync_point.assert_not_called()

    async def test_sync_folders_with_incremental_sync(self):
        """Test incremental sync uses last sync time."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-01-01T00:00:00.000Z"

    async def test_sync_folders_with_modified_filter(self):
        """Test sync with modified date filter from sync_filters."""
        c = _make_connector()
        
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-02-01T00:00:00.000Z", "2024-03-01T00:00:00.000Z")
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-02-01T00:00:00.000Z"
        assert call_kwargs["modified_before"] == "2024-03-01T00:00:00.000Z"

    async def test_sync_folders_with_created_filter(self):
        """Test sync with created date filter from sync_filters."""
        c = _make_connector()
        
        mock_created_filter = MagicMock()
        mock_created_filter.get_datetime_iso = MagicMock(
            return_value=("2024-01-01T00:00:00.000Z", "2024-01-31T23:59:59.000Z")
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_created_filter if key == SyncFilterKey.CREATED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["created_after"] == "2024-01-01T00:00:00.000Z"
        assert call_kwargs["created_before"] == "2024-01-31T23:59:59.000Z"

    async def test_sync_folders_with_filter_and_checkpoint(self):
        """Test sync when both filter and checkpoint exist (uses max)."""
        c = _make_connector()
        
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-01-01T00:00:00.000Z", None)
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-02-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-02-01T00:00:00.000Z"

    async def test_sync_folders_with_filter_only(self):
        """Test sync with filter but no checkpoint."""
        c = _make_connector()
        
        mock_modified_filter = MagicMock()
        mock_modified_filter.get_datetime_iso = MagicMock(
            return_value=("2024-03-01T00:00:00.000Z", None)
        )
        
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: mock_modified_filter if key == SyncFilterKey.MODIFIED else None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        folders_response = _make_mock_response(
            200,
            {"results": [_folder_data("folder1", "Folder 1")], "_links": {}},
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=folders_response)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                await c._sync_folders("TEST")

        call_kwargs = mock_datasource.get_folders_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-03-01T00:00:00.000Z"

    async def test_sync_folders_pagination_with_invalid_cursor(self):
        """Test pagination when cursor extraction returns None."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        mock_datasource = MagicMock()
        
        response_with_bad_cursor = _make_mock_response(
            200,
            {
                "results": [_folder_data("folder1", "Folder 1")],
                "_links": {"next": "/invalid-cursor-format"},
            },
        )
        mock_datasource.get_folders_v1 = AsyncMock(return_value=response_with_bad_cursor)

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with patch.object(c, "_fetch_page_permissions", new_callable=AsyncMock, return_value=[]):
                with patch.object(c, "_extract_cursor_from_next_link", return_value=None):
                    await c._sync_folders("TEST")

        assert mock_datasource.get_folders_v1.call_count == 1

    async def test_sync_folders_handles_exception(self):
        """Test that exceptions during sync are logged and raised."""
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_datasource = MagicMock()
        mock_datasource.get_folders_v1 = AsyncMock(side_effect=Exception("Network error"))

        with patch.object(c, "_get_fresh_datasource", return_value=mock_datasource):
            with pytest.raises(Exception, match="Network error"):
                await c._sync_folders("TEST")


# ===========================================================================
# _transform_to_folder_file_record
# ===========================================================================


class TestTransformFolderFileRecord:
    """Test _transform_to_folder_file_record transformation method."""

    def test_transform_folder_with_all_fields(self):
        """Test transforming folder with all fields present."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "type": "folder",
            "spaceId": "SPACE1",
            "parentId": "parent456",
            "parentType": "folder",
            "version": {"number": 5, "createdAt": "2024-01-15T10:00:00.000Z"},
            "createdAt": "2024-01-01T09:00:00.000Z",
            "_links": {
                "webui": "/spaces/TEST/pages/folder123",
                "base": "https://example.atlassian.net/wiki",
            },
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.external_record_id == "folder123"
        assert result.record_name == "My Folder"
        assert result.is_file is False
        assert result.mime_type == MimeTypes.FOLDER.value
        assert result.external_record_group_id == "SPACE1"
        assert result.parent_external_record_id == "parent456"
        assert result.weburl == "https://example.atlassian.net/wiki/spaces/TEST/pages/folder123"

    def test_transform_folder_missing_id(self):
        """Test that folder without id returns None."""
        c = _make_connector()
        folder_data = {"title": "My Folder", "spaceId": "SPACE1"}
        
        result = c._transform_to_folder_file_record(folder_data)
        assert result is None

    def test_transform_folder_missing_title(self):
        """Test that folder without title returns None."""
        c = _make_connector()
        folder_data = {"id": "folder123", "spaceId": "SPACE1"}
        
        result = c._transform_to_folder_file_record(folder_data)
        assert result is None

    def test_transform_folder_missing_space(self):
        """Test that folder without space logs warning and returns None."""
        c = _make_connector()
        folder_data = {"id": "folder123", "title": "My Folder"}
        
        result = c._transform_to_folder_file_record(folder_data)
        assert result is None

    def test_transform_folder_with_v1_timestamps(self):
        """Test folder with v1 API timestamp format."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "space": {"id": "123", "key": "TEST"},
            "history": {
                "createdDate": "2024-01-01T09:00:00.000Z",
                "lastUpdated": {
                    "when": "2024-01-15T10:00:00.000Z",
                    "number": 3,
                },
            },
            "_links": {
                "webui": "/spaces/TEST/pages/folder123",
                "self": "https://example.atlassian.net/wiki/rest/api/content/folder123",
            },
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.external_record_group_id == "123"
        assert result.source_created_at is not None
        assert result.source_updated_at is not None

    def test_transform_folder_with_v1_space_format(self):
        """Test folder with v1 space format (nested object)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "space": {"id": "SPACE789", "key": "TEST"},
            "version": {"number": 2},
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.external_record_group_id == "SPACE789"

    def test_transform_folder_with_ancestors(self):
        """Test folder with parent from ancestors (v1 format)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "ancestors": [
                {"id": "ancestor1", "type": "page"},
                {"id": "parent456", "type": "folder"},
            ],
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.parent_external_record_id == "parent456"
        assert result.parent_record_type == RecordType.FILE  # folder maps to FILE

    def test_transform_folder_parent_type_page(self):
        """Test folder with page parent."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "parentId": "page789",
            "parentType": "page",
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.parent_record_type == RecordType.CONFLUENCE_PAGE

    def test_transform_folder_parent_type_blogpost(self):
        """Test folder with blogpost parent."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "parentId": "blog456",
            "parentType": "blogpost",
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.parent_record_type == RecordType.CONFLUENCE_BLOGPOST

    def test_transform_folder_parent_type_unknown(self):
        """Test folder with unknown parent type (defaults to FILE)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "parentId": "parent999",
            "parentType": "unknown_type",
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.parent_record_type == RecordType.FILE

    def test_transform_folder_weburl_without_base(self):
        """Test folder web URL construction when no base URL."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "_links": {
                "webui": "/spaces/TEST/pages/folder123",
                "self": "https://example.atlassian.net/wiki/rest/api/content/folder123",
            },
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.weburl == "https://example.atlassian.net/wiki/spaces/TEST/pages/folder123"

    def test_transform_folder_with_existing_record(self):
        """Test folder transformation with existing record (version increment)."""
        c = _make_connector()
        
        existing = MagicMock()
        existing.id = "existing-id-123"
        existing.version = 5
        existing.external_revision_id = "2"
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "version": {"number": 3},  # Different version
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data, existing_record=existing)
        
        assert result is not None
        assert result.id == "existing-id-123"
        assert result.version == 6  # Incremented from existing

    def test_transform_folder_existing_record_same_version(self):
        """Test folder with existing record and same version (no increment)."""
        c = _make_connector()
        
        existing = MagicMock()
        existing.id = "existing-id-123"
        existing.version = 5
        existing.external_revision_id = "3"
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "version": {"number": 3},  # Same version
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data, existing_record=existing)
        
        assert result is not None
        assert result.version == 5  # Not incremented

    def test_transform_folder_handles_exception(self):
        """Test that transformation exceptions are caught and return None."""
        c = _make_connector()
        
        # Invalid data that will cause exception
        folder_data = {"id": "folder123", "title": "Test"}
        
        with patch.object(c, '_parse_confluence_datetime', side_effect=Exception("Parse error")):
            result = c._transform_to_folder_file_record(folder_data)
            assert result is None

    def test_transform_folder_version_not_dict(self):
        """Test when version is not a dict (line 2663->2670)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "version": 5,  # Not a dict, just a number
            "history": {
                "lastUpdated": {
                    "when": "2024-01-15T10:00:00.000Z",
                    "number": 5,
                },
            },
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        # Should fall back to v1 format from history
        assert result.source_updated_at is not None

    def test_transform_folder_last_updated_not_dict(self):
        """Test when lastUpdated is not a dict (line 2673->2681)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "history": {
                "lastUpdated": "2024-01-15T10:00:00.000Z",  # Not a dict, just a string
            },
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        # Should handle gracefully when lastUpdated is not a dict

    def test_transform_folder_no_webui(self):
        """Test when webui is not present (line 2714->2725)."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "_links": {
                "self": "https://example.atlassian.net/wiki/rest/api/content/folder123",
                # No webui
            },
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.weburl is None  # No weburl when webui is missing

    def test_transform_folder_webui_no_wiki_in_self(self):
        """Test web URL construction when self link has no /wiki/."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "_links": {
                "webui": "/spaces/TEST/pages/folder123",
                "self": "https://example.atlassian.net/rest/api/content/folder123",  # No /wiki/
            },
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        # Web URL construction should fail gracefully

    def test_transform_folder_version_number_from_history(self):
        """Test version_number fallback to history.lastUpdated.number."""
        c = _make_connector()
        
        folder_data = {
            "id": "folder123",
            "title": "My Folder",
            "spaceId": "SPACE1",
            "history": {
                "lastUpdated": {
                    "when": "2024-01-15T10:00:00.000Z",
                    "number": 7,
                },
            },
            "_links": {"webui": "/spaces/TEST/pages/folder123"},
        }
        
        result = c._transform_to_folder_file_record(folder_data)
        
        assert result is not None
        assert result.external_revision_id == "7"


# ===========================================================================
# ConfluenceConnector._extract_cursor_from_next_link
# ===========================================================================


class TestExtractCursorFromNextLink:

    def test_extracts_cursor(self):
        connector = _make_connector()
        next_link = "/wiki/api/v2/spaces?cursor=abc123&limit=20"
        result = connector._extract_cursor_from_next_link(next_link)
        assert result == "abc123"

    def test_returns_none_when_no_cursor(self):
        connector = _make_connector()
        next_link = "/wiki/api/v2/spaces?limit=20"
        result = connector._extract_cursor_from_next_link(next_link)
        assert result is None

    def test_returns_none_for_empty(self):
        connector = _make_connector()
        result = connector._extract_cursor_from_next_link("")
        assert result is None


# ===========================================================================
# ConfluenceConnector._sync_permission_changes_from_audit_log
# ===========================================================================


class TestSyncPermissionChangesFromAuditLog:

    @pytest.mark.asyncio
    async def test_first_run_initializes_checkpoint(self):
        """First run (no sync point) initializes checkpoint and skips."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.audit_log_sync_point.update_sync_point = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited_once()
        # Verify checkpoint was set (not None)
        call_args = connector.audit_log_sync_point.update_sync_point.call_args[0]
        assert "last_sync_time_ms" in call_args[1]

    @pytest.mark.asyncio
    async def test_subsequent_run_no_changes(self):
        """Subsequent run with no permission changes still updates checkpoint."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=[])

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_subsequent_run_with_changes(self):
        """Subsequent run finds permission changes and syncs them."""
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=["Page Title"])
        connector._sync_content_permissions_by_titles = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector._sync_content_permissions_by_titles.assert_awaited_once_with(["Page Title"])
        connector.audit_log_sync_point.update_sync_point.assert_awaited()


# ===========================================================================
# ConfluenceConnector._extract_content_title_from_audit_record
# ===========================================================================


class TestExtractContentTitleFromAuditRecord:

    def test_permission_change_with_page_and_space(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Page", "name": "My Page"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result == "My Page"

    def test_permission_change_with_blog_and_space(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Blog", "name": "My Blog"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result == "My Blog"

    def test_non_permission_category_returns_none(self):
        connector = _make_connector()
        record = {
            "category": "Security",
            "associatedObjects": [
                {"objectType": "Page", "name": "Test"},
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_space_returns_none(self):
        """Permission change without Space is a global change, not content-level."""
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Page", "name": "Test"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_content_returns_none(self):
        """Permission change with Space but no Page/Blog is space-level."""
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "ENG"},
            ],
        }
        result = connector._extract_content_title_from_audit_record(record)
        assert result is None


# ===========================================================================
# ConfluenceConnector._fetch_permission_audit_logs
# ===========================================================================


class TestFetchPermissionAuditLogs:

    @pytest.mark.asyncio
    async def test_fetches_and_extracts_titles(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_audit_logs = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {
                    "category": "Permissions",
                    "associatedObjects": [
                        {"objectType": "Page", "name": "Restricted Page"},
                        {"objectType": "Space", "name": "ENG"},
                    ],
                },
                {
                    "category": "Security",
                    "associatedObjects": [],
                },
            ],
            "size": 2,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        titles = await connector._fetch_permission_audit_logs(1000, 2000)
        assert "Restricted Page" in titles

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_audit_logs = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        titles = await connector._fetch_permission_audit_logs(1000, 2000)
        assert titles == []


# ===========================================================================
# ConfluenceConnector._fetch_space_permissions
# ===========================================================================


class TestFetchSpacePermissions:

    @pytest.mark.asyncio
    async def test_fetches_permissions(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_space_permissions_assignments = AsyncMock(return_value=_make_mock_response(200, {
            "results": [{"id": "perm-1"}],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_space_permission = AsyncMock(return_value=Permission(
            entity_type=EntityType.USER,
            type=PermissionType.READ,
            email="user@example.com",
        ))

        permissions = await connector._fetch_space_permissions("space-1", "Engineering")
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_space_permissions_assignments = AsyncMock(return_value=_make_mock_response(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_space_permissions("space-1", "Engineering")
        assert permissions == []


# ===========================================================================
# ConfluenceConnector._fetch_page_permissions
# ===========================================================================


class TestFetchPagePermissions:

    @pytest.mark.asyncio
    async def test_fetches_page_permissions(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_permissions_v1 = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"operation": "read", "restrictions": {"user": {"results": []}, "group": {"results": []}}},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._transform_page_restriction_to_permissions = AsyncMock(return_value=[])

        permissions = await connector._fetch_page_permissions("page-1")
        assert permissions == []
        connector._transform_page_restriction_to_permissions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_page_permissions_v1 = AsyncMock(return_value=_make_mock_response(403, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_page_permissions("page-1")
        assert permissions == []


# ===========================================================================
# ConfluenceConnector._sync_content_permissions_by_titles
# ===========================================================================


class TestSyncContentPermissionsByTitles:

    @pytest.mark.asyncio
    async def test_empty_titles_no_op(self):
        connector = _make_connector()
        await connector._sync_content_permissions_by_titles([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_items_not_in_db(self):
        """Items not in DB are skipped (respects sync filters)."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_content_by_titles = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "page-1", "title": "Test Page", "type": "page"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        # Record not found in DB
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        await connector._sync_content_permissions_by_titles(["Test Page"])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_records_permissions(self):
        """Items found in DB have their permissions refreshed."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_content_by_titles = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "page-1", "title": "Test Page", "type": "page"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Record exists in DB
        existing = MagicMock()
        existing.id = "existing-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        mock_record = MagicMock()
        mock_record.inherit_permissions = True
        connector._transform_to_webpage_record = MagicMock(return_value=mock_record)
        connector._fetch_page_permissions = AsyncMock(return_value=[
            Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="alice@example.com")
        ])

        await connector._sync_content_permissions_by_titles(["Test Page"])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_blogpost_type_and_read_permissions(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_content_by_titles = AsyncMock(return_value=_make_mock_response(200, {
            "results": [
                {"id": "blog-1", "title": "Test Blog", "type": "blogpost"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=MagicMock(id="existing-blog")
        )
        mock_record = MagicMock()
        mock_record.inherit_permissions = True
        connector._transform_to_webpage_record = MagicMock(return_value=mock_record)
        connector._fetch_page_permissions = AsyncMock(return_value=[
            Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="bob@example.com")
        ])

        await connector._sync_content_permissions_by_titles(["Test Blog"])

        assert mock_record.inherit_permissions is False
        connector.data_entities_processor.on_new_records.assert_awaited_once()


# =============================================================================
# Merged from test_confluence_connector_coverage.py
# =============================================================================

# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps_cov():
    logger = logging.getLogger("test.confluence.coverage")
    dep = MagicMock()
    dep.org_id = "org-cov-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.migrate_group_to_user_by_external_id = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _conn():
    logger, dep, dsp, cs = _make_mock_deps_cov()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-cov-1", "team", "test-user-id")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


# ===========================================================================
# _parse_confluence_datetime
# ===========================================================================


class TestParseConfluenceDatetime:
    def test_valid_z_suffix(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(ts, int)
        assert ts > 0

    def test_valid_offset(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526+00:00")
        assert isinstance(ts, int)
        assert ts > 0

    def test_invalid_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("not-a-date")
        assert ts is None

    def test_empty_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("")
        assert ts is None


# ===========================================================================
# _transform_to_app_user
# ===========================================================================


class TestTransformToAppUser:
    def test_creates_app_user(self):
        c = _conn()
        user_data = {
            "accountId": "acc-1",
            "email": "alice@example.com",
            "displayName": "Alice Smith",
            "lastModified": "2025-01-01T00:00:00.000Z",
        }
        result = c._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "alice@example.com"
        assert result.full_name == "Alice Smith"
        assert result.source_user_id == "acc-1"

    def test_missing_account_id(self):
        c = _conn()
        result = c._transform_to_app_user({"email": "bob@example.com"})
        assert result is None

    def test_missing_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-2"})
        assert result is None

    def test_whitespace_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-3", "email": "  "})
        assert result is None

    def test_no_last_modified(self):
        c = _conn()
        result = c._transform_to_app_user({
            "accountId": "acc-4", "email": "test@test.com",
            "displayName": "Test User"
        })
        assert result is not None
        assert result.source_updated_at is None


# ===========================================================================
# _transform_to_user_group
# ===========================================================================


class TestTransformToUserGroup:
    def test_creates_group(self):
        c = _conn()
        result = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert result is not None
        assert result.name == "devs"
        assert result.source_user_group_id == "g1"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_user_group({"id": "g1"}) is None

    def test_both_missing(self):
        c = _conn()
        assert c._transform_to_user_group({}) is None


# ===========================================================================
# _transform_to_space_record_group
# ===========================================================================


class TestTransformToSpaceRecordGroup:
    def test_full_space(self):
        c = _conn()
        data = {
            "id": "100",
            "name": "Engineering",
            "key": "ENG",
            "description": "Eng space",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "_links": {"webui": "/spaces/ENG"},
        }
        result = c._transform_to_space_record_group(data, "https://wiki.example.com")
        assert result is not None
        assert result.name == "Engineering"
        assert result.short_name == "ENG"
        assert result.web_url == "https://wiki.example.com/spaces/ENG"

    def test_no_base_url(self):
        c = _conn()
        data = {"id": "101", "name": "Test", "key": "TST"}
        result = c._transform_to_space_record_group(data, None)
        assert result is not None
        assert result.web_url is None

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_space_record_group({"name": "T"}, None) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_space_record_group({"id": "1"}, None) is None


# ===========================================================================
# _map_confluence_permission
# ===========================================================================


class TestMapConfluencePermission:
    def test_administer(self):
        c = _conn()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _conn()
        assert c._map_confluence_permission("read", "page") == PermissionType.READ

    def test_delete_space(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_page(self):
        c = _conn()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_delete_blogpost(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "blogpost") == PermissionType.WRITE

    def test_archive_attachment(self):
        c = _conn()
        assert c._map_confluence_permission("archive", "attachment") == PermissionType.WRITE

    def test_restrict_content_uses_default_read(self):
        c = _conn()
        assert c._map_confluence_permission("restrict_content", "space") == PermissionType.READ

    def test_export_uses_default_read(self):
        c = _conn()
        assert c._map_confluence_permission("export", "space") == PermissionType.READ


# ===========================================================================
# _map_page_permission
# ===========================================================================


class TestMapPagePermission:
    def test_read(self):
        c = _conn()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _conn()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _conn()
        assert c._map_page_permission("delete") == PermissionType.READ


# ===========================================================================
# _construct_web_url
# ===========================================================================


class TestConstructWebUrl:
    def test_v2_with_base_url(self):
        c = _conn()
        links = {"webui": "/spaces/ENG/pages/123"}
        url = c._construct_web_url(links, "https://wiki.example.com")
        assert url == "https://wiki.example.com/spaces/ENG/pages/123"

    def test_v1_with_self_link(self):
        c = _conn()
        links = {
            "webui": "/spaces/ENG/pages/123",
            "self": "https://company.atlassian.net/wiki/rest/api/content/123",
        }
        url = c._construct_web_url(links, None)
        assert url == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_no_webui(self):
        c = _conn()
        url = c._construct_web_url({}, "https://base.com")
        assert url is None

    def test_no_base_no_self(self):
        c = _conn()
        links = {"webui": "/some/path"}
        url = c._construct_web_url(links, None)
        assert url is None

    def test_self_link_without_wiki(self):
        c = _conn()
        links = {"webui": "/path", "self": "https://other.com/api/content/1"}
        url = c._construct_web_url(links, None)
        assert url is None


# ===========================================================================
# _transform_to_webpage_record
# ===========================================================================


class TestTransformToWebpageRecord:
    def _page_data(self, **overrides):
        data = {
            "id": "page-1",
            "title": "Test Page",
            "space": {"id": 100},
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 5},
            },
            "ancestors": [{"id": "parent-1"}],
            "_links": {
                "webui": "/spaces/ENG/pages/1",
                "self": "https://company.atlassian.net/wiki/rest/api/content/1",
            },
        }
        data.update(overrides)
        return data

    def test_v1_format(self):
        c = _conn()
        result = c._transform_to_webpage_record(
            self._page_data(), RecordType.CONFLUENCE_PAGE
        )
        assert result is not None
        assert result.record_name == "Test Page"
        assert result.external_record_id == "page-1"
        assert result.parent_external_record_id == "parent-1"
        assert result.external_revision_id == "5"
        assert result.version == 0

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "page-v2",
            "title": "V2 Page",
            "spaceId": 200,
            "parentId": "parent-v2",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 3},
            "_links": {"webui": "/p", "base": "https://wiki.com"},
        }
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert result is not None
        assert result.external_record_group_id == "200"
        assert result.parent_external_record_id == "parent-v2"
        assert result.record_type == RecordType.CONFLUENCE_BLOGPOST

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_webpage_record({"title": "t"}, RecordType.CONFLUENCE_PAGE) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_webpage_record({"id": "1"}, RecordType.CONFLUENCE_PAGE) is None

    def test_no_space(self):
        c = _conn()
        data = {"id": "1", "title": "T"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = self._page_data()  # version=5
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result is not None
        assert result.id == "existing-id"
        assert result.version == 3  # bumped from 2

    def test_existing_record_no_version_change(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "5"  # same as data
        data = self._page_data()
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result.version == 2  # unchanged

    def test_no_ancestors_no_parent(self):
        c = _conn()
        data = self._page_data()
        data.pop("ancestors", None)
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert result is not None
        assert result.parent_external_record_id is None


# ===========================================================================
# _transform_to_attachment_file_record
# ===========================================================================


class TestTransformToAttachmentFileRecord:
    def _attachment_data(self, **overrides):
        data = {
            "id": "att-1",
            "title": "report.pdf",
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 2},
            },
            "extensions": {"fileSize": 12345, "mediaType": "application/pdf"},
            "_links": {
                "webui": "/att/report.pdf",
                "self": "https://company.atlassian.net/wiki/rest/api/content/att-1",
            },
        }
        data.update(overrides)
        return data

    def test_basic_attachment(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "parent-1", "space-1"
        )
        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.extension == "pdf"
        assert result.size_in_bytes == 12345
        assert result.is_file is True
        assert result.is_dependent_node is True

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "att-v2",
            "title": "image.png",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 1},
            "fileSize": 5000,
            "mediaType": "image/png",
            "_links": {"webui": "/att", "base": "https://wiki.com"},
        }
        result = c._transform_to_attachment_file_record(data, "p1", "s1")
        assert result is not None
        assert result.extension == "png"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"title": "file.txt"}, "p", "s"
        ) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"id": "att-1"}, "p", "s"
        ) is None

    def test_filename_with_query_params(self):
        c = _conn()
        data = self._attachment_data(title="file.pdf?version=1")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.record_name == "file.pdf"

    def test_no_extension(self):
        c = _conn()
        data = self._attachment_data(title="README")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.extension is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "ex-att"
        existing.version = 1
        existing.external_revision_id = "1"  # old version
        data = self._attachment_data()  # version=2
        result = c._transform_to_attachment_file_record(
            data, "p", "s", existing_record=existing
        )
        assert result.id == "ex-att"
        assert result.version == 2

    def test_parent_node_id(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "p1", "s1", parent_node_id="node-123"
        )
        assert result.parent_node_id == "node-123"

    def test_mime_type_from_metadata(self):
        c = _conn()
        data = {
            "id": "att-m",
            "title": "file.bin",
            "metadata": {"mediaType": "application/octet-stream"},
            "_links": {},
        }
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result is not None


# ===========================================================================
# _create_permission_from_principal
# ===========================================================================


class TestCreatePermissionFromPrincipal:
    @pytest.mark.asyncio
    async def test_user_found(self):
        c = _conn()
        mock_user = MagicMock()
        mock_user.email = "alice@test.com"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=mock_user)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "alice@test.com"
        assert perm.entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=False
        )
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_create_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        pseudo_group = MagicMock()
        pseudo_group.source_user_group_id = "acc-1"
        c._create_pseudo_group = AsyncMock(return_value=pseudo_group)

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_user_not_found_existing_pseudo(self):
        c = _conn()
        pseudo = MagicMock()
        pseudo.source_user_group_id = "acc-1"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=pseudo)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        c = _conn()
        mock_group = MagicMock()
        mock_group.source_user_group_id = "grp-1"
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=mock_group)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP
        assert perm.external_id == "grp-1"

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-x", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_principal_type(self):
        c = _conn()
        perm = await c._create_permission_from_principal("role", "r1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_store_provider.transaction.side_effect = Exception("DB error")
        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is None


# ===========================================================================
# _create_pseudo_group
# ===========================================================================


class TestCreatePseudoGroup:
    @pytest.mark.asyncio
    async def test_creates_and_saves(self):
        c = _conn()
        result = await c._create_pseudo_group("acc-no-email")
        assert result is not None
        assert result.name == f"{PSEUDO_USER_GROUP_PREFIX} acc-no-email"
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("fail"))
        result = await c._create_pseudo_group("acc-fail")
        assert result is None


# ===========================================================================
# _transform_space_permission
# ===========================================================================


class TestTransformSpacePermission:
    @pytest.mark.asyncio
    async def test_valid_permission(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        perm_data = {
            "principal": {"type": "user", "id": "acc-1"},
            "operation": {"key": "read", "targetType": "space"},
        }
        result = await c._transform_space_permission(perm_data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_principal(self):
        c = _conn()
        result = await c._transform_space_permission({"operation": {"key": "read"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_operation(self):
        c = _conn()
        result = await c._transform_space_permission(
            {"principal": {"type": "user", "id": "1"}}
        )
        assert result is None


class TestTransformAccessClassSpacePermission:
    def _access_class_perm(
        self,
        principal_id: str,
        operation_key: str = "read",
        target_type: str = "space",
    ) -> dict[str, Any]:
        return {
            "principal": {"type": "access-class", "id": principal_id},
            "operation": {"key": operation_key, "targetType": target_type},
        }

    def _group(self, name: str, external_id: str) -> AppUserGroup:
        return AppUserGroup(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-cov-1",
            source_user_group_id=external_id,
            name=name,
            org_id="org-cov-1",
        )

    def _mock_groups(self, connector: ConfluenceConnector, groups: list[AppUserGroup]) -> None:
        mock_tx = connector.data_store_provider.transaction.return_value
        mock_tx.get_user_groups = AsyncMock(return_value=groups)

    @pytest.mark.asyncio
    async def test_licensed_users_read_space_maps_to_group(self):
        c = _conn()
        self._mock_groups(c, [self._group("confluence-users", "grp-licensed-uuid")])
        result = await c._transform_space_permission(
            self._access_class_perm("ALL_LICENSED_USERS")
        )
        assert result is not None
        assert result.entity_type == EntityType.GROUP
        assert result.external_id == "grp-licensed-uuid"
        assert result.type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_licensed_users_read_space_org_fallback(self):
        c = _conn()
        self._mock_groups(c, [])
        result = await c._transform_space_permission(
            self._access_class_perm("ALL_LICENSED_USERS")
        )
        assert result is not None
        assert result.entity_type == EntityType.ORG
        assert result.type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_product_admins_administer_space_maps_to_group(self):
        c = _conn()
        self._mock_groups(c, [self._group("org-admins", "grp-admin-uuid")])
        result = await c._transform_space_permission(
            self._access_class_perm("ALL_PRODUCT_ADMINS", "administer", "space")
        )
        assert result is not None
        assert result.entity_type == EntityType.GROUP
        assert result.external_id == "grp-admin-uuid"
        assert result.type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_product_admins_administer_space_org_fallback(self):
        c = _conn()
        self._mock_groups(c, [])
        result = await c._transform_space_permission(
            self._access_class_perm("ALL_PRODUCT_ADMINS", "administer", "space")
        )
        assert result is not None
        assert result.entity_type == EntityType.ORG
        assert result.type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_licensed_users_non_read_operation_skipped(self):
        c = _conn()
        self._mock_groups(c, [self._group("confluence-users", "grp-licensed-uuid")])
        result = await c._transform_space_permission(
            self._access_class_perm("ALL_LICENSED_USERS", "create", "page")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_access_class_does_not_call_principal_helper(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock()
        self._mock_groups(c, [])
        await c._transform_space_permission(self._access_class_perm("ALL_LICENSED_USERS"))
        c._create_permission_from_principal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_name_cache_refreshes_after_invalidation(self):
        """Stale cache from a prior sync must not block group resolution on re-sync."""
        c = _conn()
        c._group_name_to_external_id = {}
        self._mock_groups(c, [self._group("confluence-users", "grp-licensed-uuid")])

        stale = await c._transform_space_permission(
            self._access_class_perm("ALL_LICENSED_USERS")
        )
        assert stale is not None
        assert stale.entity_type == EntityType.ORG

        c._group_name_to_external_id = None
        fresh = await c._transform_space_permission(
            self._access_class_perm("ALL_LICENSED_USERS")
        )
        assert fresh is not None
        assert fresh.entity_type == EntityType.GROUP
        assert fresh.external_id == "grp-licensed-uuid"


class TestDedupeSpacePermissions:
    def test_collapse_duplicate_org_read(self):
        perms = [
            Permission(entity_type=EntityType.ORG, type=PermissionType.READ),
            Permission(entity_type=EntityType.ORG, type=PermissionType.READ),
        ]
        result = ConfluenceConnector._dedupe_space_permissions(perms)
        assert len(result) == 1

    def test_keeps_distinct_permission_types(self):
        perms = [
            Permission(entity_type=EntityType.ORG, type=PermissionType.READ),
            Permission(entity_type=EntityType.ORG, type=PermissionType.OWNER),
        ]
        result = ConfluenceConnector._dedupe_space_permissions(perms)
        assert len(result) == 2


class TestFetchSpacePermissionsAccessClassDedupe:
    @pytest.mark.asyncio
    async def test_dedupes_identical_access_class_rows(self):
        connector = _make_connector()
        row = {
            "principal": {"type": "access-class", "id": "ALL_LICENSED_USERS"},
            "operation": {"key": "read", "targetType": "space"},
        }
        mock_ds = MagicMock()
        mock_ds.get_space_permissions_assignments = AsyncMock(
            return_value=_make_mock_response(
                200,
                {"results": [row, row, row], "_links": {}},
            )
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        mock_tx = connector.data_store_provider.transaction.return_value
        mock_tx.get_user_groups = AsyncMock(return_value=[])

        permissions = await connector._fetch_space_permissions("space-1", "IT")
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG
        assert permissions[0].type == PermissionType.READ


# ===========================================================================
# _transform_page_restriction_to_permissions
# ===========================================================================


class TestTransformPageRestrictionToPermissions:
    @pytest.mark.asyncio
    async def test_processes_users_and_groups(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        restriction = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "acc-1"}]},
                "group": {"results": [{"id": "grp-1"}]},
            },
        }
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _conn()
        perms = await c._transform_page_restriction_to_permissions({})
        assert perms == []

    @pytest.mark.asyncio
    async def test_empty_restrictions(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(return_value=None)
        restriction = {"operation": "read", "restrictions": {}}
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert perms == []


# ===========================================================================
# _process_webpage_with_update
# ===========================================================================


class TestProcessWebpageWithUpdate:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _conn()
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.is_new is True
        assert result.record is mock_record

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = None
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 2},  # changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.is_new is False
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.record is None

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "ancestors": [{"id": "new-parent"}],  # parent changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.metadata_changed is True


# ===========================================================================
# _fetch_group_members
# ===========================================================================


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [
                {"email": "a@t.com", "displayName": "A"},
                {"email": "", "displayName": "NoEmail"},
            ],
            "size": 2,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails, account_ids = await c._fetch_group_members("g1", "Group 1")
        assert emails == ["a@t.com"]
        assert account_ids == []

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails, account_ids = await c._fetch_group_members("g1", "G")
        assert emails == []
        assert account_ids == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        emails, account_ids = await c._fetch_group_members("g1", "G")
        assert emails == []
        assert account_ids == []


# ===========================================================================
# _get_app_users_by_emails
# ===========================================================================


class TestGetAppUsersByEmails:
    @pytest.mark.asyncio
    async def test_empty_emails(self):
        c = _conn()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _conn()
        u1 = MagicMock()
        u1.email = "a@t.com"
        u2 = MagicMock()
        u2.email = "b@t.com"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1
        assert result[0].email == "a@t.com"

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert result == []


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _conn()
        result = await c.get_signed_url(MagicMock())
        assert result == ""


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _conn()
        await c.cleanup()  # should not raise


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _conn()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_no_op(self):
        c = _conn()
        await c.handle_webhook_notification({})


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _conn()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _conn()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _conn()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        with pytest.raises(ValueError, match="Unsupported"):
            await c.get_filter_options("unknown_key")


# ===========================================================================
# _get_space_options
# ===========================================================================


class TestGetSpaceOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].id == "ENG"

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "HR", "name": "Human Resources"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "HR", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_next_cursor(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "A", "name": "A"}],
            "_links": {"next": "/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "abc"

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_space_options(1, 20, None, None)


# ===========================================================================
# _get_page_options / _get_blogpost_options
# ===========================================================================


class TestGetPageOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "Match", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "Match", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_page_options(1, 20, None, None)


class TestGetBlogpostOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Blog 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Hit", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Hit", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_blogpost_options(1, 20, None, None)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "Test"
        record.id = "rec-1"
        record.external_record_group_id = "sp1"
        record.weburl = "https://example.atlassian.net/wiki/spaces/TEST/pages/p1"
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(
            return_value=_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_data_with_adf = AsyncMock(return_value={"id": "p1", "body": {"atlas_doc_format": {"value": "{}"}}})
        c._process_page_attachments_for_children = AsyncMock(return_value={})
        c._parse_confluence_page_to_blocks = AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}")))
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        async def mock_fetch(r):
            yield b"data"

        c._fetch_attachment_content = mock_fetch
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.create_stream_record_response") as mock_create:
            mock_create.return_value = MagicMock()
            result = await c.stream_record(record)
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_exception(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "T"
        c._fetch_page_data_with_adf = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# _fetch_page_content
# ===========================================================================


class TestFetchPageContent:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"styled_view": {"value": "<h1>Content</h1>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert result == "<h1>Content</h1>"
        mock_ds.get_page_content_v2.assert_awaited_once_with(
            page_id="p1", body_format="styled_view"
        )

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"styled_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert result == "<p>Blog</p>"

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _conn()
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.TICKET)
        assert exc_info.value.status_code == 400


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _conn()
        await c.reindex_records([])
        c.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _conn()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        r1 = MagicMock()
        r1.id = "r1"
        r2 = MagicMock()
        r2.id = "r2"

        mock_updated = (MagicMock(), [Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="x@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[mock_updated, None])

        await c.reindex_records([r1, r2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.on_updated_record_permissions.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_exception_skips(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))
        await c.reindex_records([MagicMock()])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_BLOGPOST
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.id = "r1"
        c._check_and_fetch_updated_page = AsyncMock(side_effect=Exception("fail"))
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_page
# ===========================================================================


class TestCheckAndFetchUpdatedPage:
    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_change(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 5},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 6}, "space": {"id": 1}, "id": "p1", "title": "T",
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_wr = MagicMock()
        mock_wr.inherit_permissions = True
        c._transform_to_webpage_record = MagicMock(return_value=mock_wr)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is not None


# ===========================================================================
# _check_and_fetch_updated_attachment
# ===========================================================================


class TestCheckAndFetchUpdatedAttachment:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = None
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(200, {
            "version": {"number": 2},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        record.external_revision_id = "2"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None


# ===========================================================================
# create_connector (class method)
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_factory(self):
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.DataSourceEntitiesProcessor") as mock_dep:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance.org_id = "org-1"
            mock_dep.return_value = mock_instance

            logger = logging.getLogger("test")
            dsp = MagicMock()
            mock_tx = MagicMock()
            mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
            mock_tx.__aexit__ = AsyncMock(return_value=None)
            dsp.transaction.return_value = mock_tx
            cs = MagicMock()

            connector = await ConfluenceConnector.create_connector(
                logger, dsp, cs, "c1", "team", "test-user-id"
            )
            assert isinstance(connector, ConfluenceConnector)
            mock_instance.initialize.assert_awaited_once()


# ===========================================================================
# _extract_cursor_from_next_link
# ===========================================================================
class TestExtractCursorFromNextLinkCoverage:
    def test_valid_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20&cursor=eyJpZCI6Ijk5In0=")
        assert result == "eyJpZCI6Ijk5In0="

    def test_no_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20")
        assert result is None

    def test_empty_string(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("")
        assert result is None

    def test_none(self):
        c = _conn()
        result = c._extract_cursor_from_next_link(None)
        assert result is None


# ===========================================================================
# _extract_content_title_from_audit_record
# ===========================================================================
class TestExtractContentTitleFromAuditRecordCoverage:
    def test_non_permission_category(self):
        c = _conn()
        result = c._extract_content_title_from_audit_record({"category": "Security"})
        assert result is None

    def test_no_space(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Page", "name": "My Page"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_content(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Space", "name": "My Space"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_valid_page_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Page", "name": "My Page"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Page"

    def test_blog_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Blog", "name": "My Blog"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Blog"


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncCoverage:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_successful_sync(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._sync_users = AsyncMock()
        c._sync_user_groups = AsyncMock()

        space = MagicMock()
        space.short_name = "TEST"
        space.name = "Test Space"
        c._sync_spaces = AsyncMock(return_value=[space])
        c._sync_folders = AsyncMock()
        c._sync_content = AsyncMock()
        c._sync_permission_changes_from_audit_log = AsyncMock()

        await c.run_sync()
        c._sync_users.assert_awaited_once()
        c._sync_user_groups.assert_awaited_once()
        c._sync_folders.assert_awaited_once_with("TEST")
        assert c._sync_content.await_count == 2  # pages + blogposts


# ===========================================================================
# _sync_users
# ===========================================================================
class TestSyncUsersCoverage:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()


# ===========================================================================
# _sync_user_groups
# ===========================================================================
class TestSyncUserGroupsCoverage:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================
class TestFetchAttachmentContent:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = None
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        # should be an async generator even with error
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_client(self):
        c = _conn()
        c.data_source = None
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = "page-1"
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        assert result is not None


# ===========================================================================
# _map_confluence_permission edge cases
# ===========================================================================
class TestMapConfluencePermissionEdgeCases:
    def test_create_blogpost(self):
        c = _conn()
        result = c._map_confluence_permission("create", "blogpost")
        assert result == PermissionType.WRITE

    def test_create_attachment(self):
        c = _conn()
        result = c._map_confluence_permission("create", "attachment")
        assert result == PermissionType.WRITE

    def test_delete_page(self):
        c = _conn()
        result = c._map_confluence_permission("delete", "page")
        assert result == PermissionType.WRITE

    def test_archive_page(self):
        c = _conn()
        result = c._map_confluence_permission("archive", "page")
        assert result == PermissionType.WRITE

    def test_unknown_returns_read(self):
        c = _conn()
        result = c._map_confluence_permission("unknown_op", "unknown_target")
        assert result == PermissionType.READ


# ===========================================================================
# _map_page_permission edge cases
# ===========================================================================
class TestMapPagePermissionEdgeCases:
    def test_delete_permission(self):
        c = _conn()
        result = c._map_page_permission("delete")
        assert result == PermissionType.READ

    def test_administer_permission(self):
        c = _conn()
        result = c._map_page_permission("administer")
        assert result == PermissionType.READ


# ===========================================================================
# _process_webpage_with_update edge cases
# ===========================================================================
class TestProcessWebpageWithUpdateEdgeCases:
    @pytest.mark.asyncio
    async def test_no_change_skips(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "v10"
        existing.version = 10
        existing.record_name = "Same Title"
        content_data = {
            "id": "page-1",
            "title": "Same Title",
            "version": {"number": 10, "when": "2025-01-01T00:00:00Z"},
            "_links": {"webui": "/wiki/page"},
            "status": "current",
        }
        c._transform_to_webpage_record = MagicMock(return_value=MagicMock(
            external_revision_id="v10", version=10, record_name="Same Title"
        ))
        result = await c._process_webpage_with_update(
            content_data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_transform_returns_none(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {"id": "p1"}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result is not None
        assert result.record is None

# =============================================================================
# Merged from test_confluence_connector_full_coverage.py
# =============================================================================

def _make_mock_deps_fullcov():
    logger = logging.getLogger("test.confluence.full")
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.migrate_group_to_user_by_external_id = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.batch_upsert_records = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx
    dep.data_store_provider = dsp

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs, mock_tx


def _c():
    logger, dep, dsp, cs, _ = _make_mock_deps_fullcov()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-1", "team", "test-user-id")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


class TestParseConfluenceDatetimeFullCoverage:
    def test_valid(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid(self):
        c = _c()
        assert c._parse_confluence_datetime("not-a-date") is None

    def test_with_timezone(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-01-01T00:00:00+00:00")
        assert isinstance(result, int)


class TestConstructWebUrlFullCoverage:
    def test_with_base_url(self):
        c = _c()
        links = {"webui": "/spaces/ENG/pages/123"}
        assert c._construct_web_url(links, "https://company.atlassian.net/wiki") == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_without_base_url_v1_fallback(self):
        c = _c()
        links = {"webui": "/pages/123", "self": "https://company.atlassian.net/wiki/rest/api/content/123"}
        result = c._construct_web_url(links)
        assert result == "https://company.atlassian.net/wiki/pages/123"

    def test_no_webui(self):
        c = _c()
        assert c._construct_web_url({}) is None

    def test_no_base_no_self(self):
        c = _c()
        links = {"webui": "/page"}
        assert c._construct_web_url(links) is None


class TestMapConfluencePermissionFullCoverage:
    def test_administer(self):
        c = _c()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _c()
        assert c._map_confluence_permission("read", "space") == PermissionType.READ

    def test_delete_space(self):
        c = _c()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_page(self):
        c = _c()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_archive_blogpost(self):
        c = _c()
        assert c._map_confluence_permission("archive", "blogpost") == PermissionType.WRITE

    def test_delete_attachment(self):
        c = _c()
        assert c._map_confluence_permission("delete", "attachment") == PermissionType.WRITE

    def test_restrict_content_and_export_use_default_read(self):
        c = _c()
        assert c._map_confluence_permission("export", "space") == PermissionType.READ


class TestMapPagePermissionFullCoverage:
    def test_read(self):
        c = _c()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _c()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _c()
        assert c._map_page_permission("magic") == PermissionType.READ


class TestTransformToAppUserFullCoverage:
    def test_valid(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@test.com", "displayName": "User"})
        assert user is not None
        assert user.email == "u@test.com"

    def test_no_account_id(self):
        c = _c()
        assert c._transform_to_app_user({"email": "u@test.com"}) is None

    def test_no_email(self):
        c = _c()
        assert c._transform_to_app_user({"accountId": "u1"}) is None

    def test_with_last_modified(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "displayName": "User One", "lastModified": "2025-01-01T00:00:00Z"})
        assert user is not None
        assert user.source_updated_at is not None

    def test_exception_returns_none(self):
        c = _c()
        with patch.object(c, "_parse_confluence_datetime", side_effect=Exception("fail")):
            assert c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "lastModified": "bad"}) is None


class TestTransformToUserGroupFullCoverage:
    def test_valid(self):
        c = _c()
        g = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert g is not None
        assert g.name == "devs"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_no_name(self):
        c = _c()
        assert c._transform_to_user_group({"id": "g1"}) is None


class TestTransformToSpaceRecordGroupFullCoverage:
    def test_valid_with_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG", "_links": {"webui": "/spaces/ENG"}, "createdAt": "2025-01-01T00:00:00Z"}
        rg = c._transform_to_space_record_group(data, "https://company.atlassian.net/wiki")
        assert rg is not None
        assert rg.name == "Engineering"
        assert "ENG" in rg.web_url

    def test_without_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG"}
        rg = c._transform_to_space_record_group(data)
        assert rg is not None
        assert rg.web_url is None

    def test_missing_id(self):
        c = _c()
        assert c._transform_to_space_record_group({"name": "Eng"}) is None

    def test_missing_name(self):
        c = _c()
        assert c._transform_to_space_record_group({"id": "s1"}) is None


class TestTransformToWebpageRecordFullCoverage:
    def test_v2_format(self):
        c = _c()
        data = {
            "id": "p1", "title": "Test Page", "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-01-02T00:00:00Z", "number": 3},
            "spaceId": 100, "parentId": "p0",
            "_links": {"webui": "/page/p1", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert rec is not None
        assert rec.record_name == "Test Page"
        assert rec.external_record_group_id == "100"
        assert rec.parent_external_record_id == "p0"

    def test_v1_format(self):
        c = _c()
        data = {
            "id": "p2", "title": "Blog",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 5}},
            "space": {"id": 200}, "ancestors": [{"id": "root"}, {"id": "parent1"}],
            "_links": {"webui": "/blog", "self": "https://c.atlassian.net/wiki/rest/api/content/p2"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert rec is not None
        assert rec.parent_external_record_id == "parent1"

    def test_no_space(self):
        c = _c()
        data = {"id": "p3", "title": "Orphan"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "3"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 3

    def test_existing_record_same_version(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 2


class TestTransformToAttachmentFileRecordFullCoverage:
    def test_v1_format(self):
        c = _c()
        data = {
            "id": "att-1", "title": "doc.pdf",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 2}},
            "extensions": {"fileSize": 1024, "mediaType": "application/pdf"},
            "_links": {"webui": "/att", "self": "https://c.atlassian.net/wiki/rest/api/content/att-1"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-1", "space-1")
        assert rec is not None
        assert rec.record_name == "doc.pdf"
        assert rec.extension == "pdf"
        assert rec.size_in_bytes == 1024

    def test_v2_format(self):
        c = _c()
        data = {
            "id": "att-2", "title": "img.png",
            "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-03-01T00:00:00Z", "number": 1},
            "fileSize": 2048, "mediaType": "image/png",
            "_links": {"webui": "/att2", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-2", "space-2")
        assert rec is not None
        assert rec.extension == "png"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"title": "f.txt"}, "p", "s") is None

    def test_no_title(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"id": "att-3"}, "p", "s") is None

    def test_query_params_in_title(self):
        c = _c()
        data = {"id": "att-4", "title": "image.jpg?version=1", "mediaType": "image/jpeg", "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec.record_name == "image.jpg"

    def test_unknown_media_type(self):
        c = _c()
        data = {"id": "att-5", "title": "f.xyz", "extensions": {"mediaType": "application/x-custom"}, "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec is not None


class TestProcessWebpageWithUpdateFullCoverage:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _c()
        data = {"id": "p1", "title": "Page", "version": {"number": 1}, "spaceId": 1, "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.is_new is True
        assert result.record is not None

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _c()
        existing = MagicMock()
        existing.id = "e1"
        existing.version = 1
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {"id": "p1", "title": "Page", "version": {"number": 2}, "spaceId": 1, "parentId": "new-parent", "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, existing, [])
        assert result.content_changed is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _c()
        result = await c._process_webpage_with_update({}, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.record is None


class TestCreatePermissionFromPrincipalFullCoverage:
    @pytest.mark.asyncio
    async def test_user_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps_fullcov()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_by_source_id = AsyncMock(return_value=MagicMock(email="u@t.com"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "u@t.com"

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _c()
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_with_pseudo_group(self):
        c = _c()
        c._create_pseudo_group = AsyncMock(return_value=MagicMock(source_user_group_id="u1"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ, create_pseudo_group_if_missing=True)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps_fullcov()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(source_user_group_id="g1"))
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _c()
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        c = _c()
        perm = await c._create_permission_from_principal("robot", "r1", PermissionType.READ)
        assert perm is None


class TestCreatePseudoGroupFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        result = await c._create_pseudo_group("account-123")
        assert result is not None
        assert PSEUDO_USER_GROUP_PREFIX in result.name
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _c()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("db fail"))
        result = await c._create_pseudo_group("account-123")
        assert result is None


class TestTransformPageRestrictionToPermissionsFullCoverage:
    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _c()
        result = await c._transform_page_restriction_to_permissions({})
        assert result == []

    @pytest.mark.asyncio
    async def test_users_and_groups(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "u1"}]},
                "group": {"results": [{"id": "g1"}]},
            },
        }
        result = await c._transform_page_restriction_to_permissions(data)
        assert len(result) == 2


class TestTransformSpacePermissionFullCoverage:
    @pytest.mark.asyncio
    async def test_valid(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {"principal": {"type": "user", "id": "u1"}, "operation": {"key": "read", "targetType": "space"}}
        result = await c._transform_space_permission(data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_fields(self):
        c = _c()
        assert await c._transform_space_permission({}) is None


class TestFetchGroupMembersFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "u@t.com", "displayName": "U"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails, account_ids = await c._fetch_group_members("g1", "devs")
        assert "u@t.com" in emails
        assert account_ids == []

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == ([], [])

    @pytest.mark.asyncio
    async def test_skips_no_email(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "", "displayName": "NoEmail"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == ([], [])


class TestGetAppUsersByEmailsFullCoverage:
    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _c()
        u1 = MagicMock(email="a@t.com")
        u2 = MagicMock(email="b@t.com")
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_list(self):
        c = _c()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_error(self):
        c = _c()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        assert await c._get_app_users_by_emails(["a@t.com"]) == []


class TestGetSignedUrlFullCoverage:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _c()
        assert await c.get_signed_url(MagicMock()) == ""


class TestStreamRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_page_stream(self):
        c = _c()
        c._fetch_page_data_with_adf = AsyncMock(return_value={"id": "p1", "body": {"atlas_doc_format": {"value": "{}"}}})
        c._process_page_attachments_for_children = AsyncMock(return_value={})
        c._parse_confluence_page_to_blocks = AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}")))
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.record_name = "Test"
        record.external_record_id = "p1"
        record.weburl = "http://example.com"
        record.id = "rec-1"
        record.external_record_group_id = "space-1"
        resp = await c.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock()
        record.record_type = RecordType.OTHERS
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c.stream_record(record)


class TestFetchPageContentFullCoverage:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"styled_view": {"value": "<p>Content</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "<p>Content</p>" in result

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"styled_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert "<p>Blog</p>" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result


class TestReindexRecordsFullCoverage:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _c()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_with_updated_and_non_updated(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        rec1 = MagicMock(id="r1", record_type=RecordType.CONFLUENCE_PAGE)
        rec2 = MagicMock(id="r2", record_type=RecordType.CONFLUENCE_PAGE)

        updated = (MagicMock(), [Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[updated, None])

        await c.reindex_records([rec1, rec2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()


class TestCheckAndFetchUpdatedRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_dispatches_page(self):
        c = _c()
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_PAGE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_blogpost(self):
        c = _c()
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_BLOGPOST)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_file(self):
        c = _c()
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.FILE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock(record_type="UNKNOWN")
        result = await c._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedPageFullCoverage:
    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 5},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        record.id = "e1"
        record.version = 1
        record.parent_external_record_id = None

        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 3},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None


class TestRunIncrementalSyncFullCoverage:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _c()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


class TestCleanupFullCoverage:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _c()
        await c.cleanup()


class TestHandleWebhookNotificationFullCoverage:
    @pytest.mark.asyncio
    async def test_does_nothing(self):
        c = _c()
        await c.handle_webhook_notification({"type": "test"})


class TestGetFilterOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _c()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _c()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _c()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _c()
        with pytest.raises(ValueError):
            await c.get_filter_options("unknown_key")


class TestGetSpaceOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "ENG", "name": "Engineering"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "Eng", None)
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {"next": "/wiki/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert result.has_more is True


class TestGetPageOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "My Page", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "My", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert result.success is True


class TestGetBlogpostOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Blog", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Blog", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Post 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert result.success is True


class TestSyncSpacesIncludeFilter:
    @pytest.mark.asyncio
    async def test_in_filter(self):
        c = _c()
        c.indexing_filters = FilterCollection()
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.IN
        space_filter.get_value.return_value = ["ENG"]
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None)

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "1", "key": "ENG", "name": "Engineering"}],
            "_links": {"base": "https://c.atlassian.net/wiki"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=RecordGroup(
            external_group_id="1", name="Engineering", short_name="ENG",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE, connector_id="conn-1",
        ))

        spaces = await c._sync_spaces()
        assert len(spaces) == 1
        mock_ds.get_spaces.assert_awaited_once()
        call_kwargs = mock_ds.get_spaces.call_args
        assert call_kwargs[1].get("keys") == ["ENG"]

# ---------------------------------------------------------------------------
# Helpers shared by new tests
# ---------------------------------------------------------------------------


def _mk_connector():
    """Create a fresh ConfluenceConnector with full mocks."""
    logger, dep, dsp, cs, _ = _make_mock_deps_fullcov()
    c = ConfluenceConnector(logger, dep, dsp, cs, "conn-new-1", "team", "usr-1")
    return c


def _mk_resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


def _mock_ds_for_embedded_images(
    *,
    content_type="page",
    attachments_v2,
    adf_body,
    footer_comments=None,
    inline_comments=None,
    adf_status=200,
):
    """Build datasource mock for _sync_content embedded-image detection paths."""
    ds = MagicMock()
    base_links = {"base": "https://wiki.example.com/wiki"}
    adf_resp = _mk_resp(adf_status, {
        "body": {"atlas_doc_format": {"value": json.dumps(adf_body)}},
    })
    footer_resp = _mk_resp(200, {"results": footer_comments or []})
    inline_resp = _mk_resp(200, {"results": inline_comments or []})
    att_resp = _mk_resp(200, {"results": attachments_v2, "_links": base_links})

    if content_type == "page":
        ds.get_page_attachments = AsyncMock(return_value=att_resp)
        ds.get_page_content_v2 = AsyncMock(return_value=adf_resp)
        ds.get_page_footer_comments = AsyncMock(return_value=footer_resp)
        ds.get_page_inline_comments = AsyncMock(return_value=inline_resp)
    else:
        ds.get_blogpost_attachments = AsyncMock(return_value=att_resp)
        ds.get_blogpost_content_v2 = AsyncMock(return_value=adf_resp)
        ds.get_blog_post_footer_comments = AsyncMock(return_value=footer_resp)
        ds.get_blog_post_inline_comments = AsyncMock(return_value=inline_resp)
    return ds


def _sync_page_data_with_attachments(attachments, item_id="100001", title="Page1"):
    return {
        "id": item_id,
        "title": title,
        "space": {"id": "sp1", "key": "S1"},
        "version": {"number": 1, "when": "2024-01-01T10:00:00Z"},
        "history": {
            "createdDate": "2024-01-01T09:00:00Z",
            "lastUpdated": {"when": "2024-01-01T10:00:00Z", "number": 1},
        },
        "ancestors": [],
        "_links": {
            "webui": f"/spaces/S1/pages/{item_id}",
            "self": f"https://x.atlassian.net/wiki/rest/api/content/{item_id}",
        },
        "childTypes": {"comment": {"value": False}},
        "children": {"attachment": {"results": attachments}},
    }


async def _collect_bytes(gen):
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    return b"".join(chunks)


# ===========================================================================
# init()
# ===========================================================================


class TestInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        c = _mk_connector()
        mock_client = MagicMock()
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            with patch(
                "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
            ) as mock_ds_cls:
                result = await c.init()

        assert result is True
        assert c.external_client is mock_client
        mock_ds_cls.assert_called_once_with(mock_client)

    @pytest.mark.asyncio
    async def test_init_exception_returns_false(self):
        c = _mk_connector()
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("build failed"),
        ):
            result = await c.init()

        assert result is False


# ===========================================================================
# _get_fresh_datasource()
# ===========================================================================


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_if_no_client(self):
        c = _mk_connector()
        c.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_raises_if_no_config(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="configuration not found"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_api_token_auth_returns_datasource_directly(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ) as mock_ds_cls:
            mock_ds_cls.return_value = MagicMock()
            result = await c._get_fresh_datasource()
        mock_ds_cls.assert_called_once_with(c.external_client)

    @pytest.mark.asyncio
    async def test_oauth_raises_if_no_token(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": ""}}
        )
        with pytest.raises(Exception, match="No OAuth access token"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_oauth_updates_token_when_changed(self):
        c = _mk_connector()
        internal_client = MagicMock()
        internal_client.get_token.return_value = "old-token"
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = internal_client
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "new-token"},
            }
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ):
            await c._get_fresh_datasource()
        internal_client.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_oauth_no_update_when_token_same(self):
        c = _mk_connector()
        internal_client = MagicMock()
        internal_client.get_token.return_value = "same-token"
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = internal_client
        c.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "same-token"},
            }
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ):
            await c._get_fresh_datasource()
        internal_client.set_token.assert_not_called()


# ===========================================================================
# test_connection_and_access()
# ===========================================================================


class TestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self):
        c = _mk_connector()
        c.external_client = None
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_mk_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_failed_response(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("connection error"))
        result = await c.test_connection_and_access()
        assert result is False


# ===========================================================================
# _sync_users() – happy paths and error paths
# ===========================================================================


class TestSyncUsersHappyPath:
    @pytest.mark.asyncio
    async def test_saves_users_with_email(self):
        c = _mk_connector()
        user_result = {
            "user": {
                "accountId": "acc-1",
                "email": "user1@example.com",
                "displayName": "User One",
            }
        }
        ds = MagicMock()
        ds.search_users = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": [user_result]}),
                _mk_resp(200, {"results": []}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()
        c.data_entities_processor.on_new_app_users.assert_called_once()
        saved = c.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(saved) == 1
        assert saved[0].email == "user1@example.com"

    @pytest.mark.asyncio
    async def test_skips_users_without_email(self):
        c = _mk_connector()
        user_result = {
            "user": {"accountId": "acc-2", "email": "", "displayName": "No Email User"}
        }
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_mk_resp(200, {"results": [user_result]}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()
        c.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_migration_exception(self):
        c = _mk_connector()
        user_result = {
            "user": {
                "accountId": "acc-3",
                "email": "migfail@example.com",
                "displayName": "Mig Fail",
            }
        }
        ds = MagicMock()
        ds.search_users = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": [user_result]}),
                _mk_resp(200, {"results": []}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock(
            side_effect=RuntimeError("migration failed")
        )
        # Should not raise – exception is caught and logged
        await c._sync_users()
        c.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_transform_returns_none_skips_user(self):
        c = _mk_connector()
        user_result = {"user": {"accountId": "acc-x", "email": "x@example.com"}}
        ds = MagicMock()
        ds.search_users = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": [user_result]}),
                _mk_resp(200, {"results": []}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with patch.object(c, "_transform_to_app_user", return_value=None):
            await c._sync_users()
        c.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_outer_exception_is_reraised(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("ds fail"))
        with pytest.raises(RuntimeError, match="ds fail"):
            await c._sync_users()

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        """Stops when results < batch_size."""
        c = _mk_connector()
        users_full = [
            {"user": {"accountId": f"acc-{i}", "email": f"u{i}@x.com", "displayName": f"U{i}"}}
            for i in range(100)
        ]
        users_partial = [
            {"user": {"accountId": "acc-200", "email": "last@x.com", "displayName": "Last"}}
        ]
        ds = MagicMock()
        ds.search_users = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": users_full}),
                _mk_resp(200, {"results": users_partial}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()
        # Two pages fetched, two on_new_app_users calls
        assert c.data_entities_processor.on_new_app_users.call_count == 2


# ===========================================================================
# _sync_user_groups() – happy paths and error paths
# ===========================================================================


class TestSyncUserGroupsHappyPath:
    @pytest.mark.asyncio
    async def test_saves_group_with_members(self):
        c = _mk_connector()
        group_data = {"id": "grp-1", "name": "Engineers"}
        ds = MagicMock()
        ds.get_groups = AsyncMock(
            return_value=_mk_resp(200, {"results": [group_data], "size": 1})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_group_members = AsyncMock(return_value=(["eng@example.com"], []))
        app_user = MagicMock()
        app_user.email = "eng@example.com"
        c._get_app_users_by_emails = AsyncMock(return_value=[app_user])

        await c._sync_user_groups()

        c.data_entities_processor.on_new_user_groups.assert_called()
        call_args = c.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert len(call_args) == 1
        group, members = call_args[0]
        assert group.name == "Engineers"
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_per_group_exception_continues(self):
        c = _mk_connector()
        group_data = {"id": "grp-err", "name": "ErrGroup"}
        ds = MagicMock()
        ds.get_groups = AsyncMock(
            return_value=_mk_resp(200, {"results": [group_data], "size": 1})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_group_members = AsyncMock(side_effect=RuntimeError("members fail"))
        # Should not raise
        await c._sync_user_groups()
        c.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_group_with_no_id_or_name(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_groups = AsyncMock(
            return_value=_mk_resp(200, {"results": [{"id": None, "name": "NoId"}], "size": 1})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()
        c.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination_stops_when_size_lt_batch(self):
        c = _mk_connector()
        group_data = {"id": "grp-1", "name": "G1"}
        ds = MagicMock()
        ds.get_groups = AsyncMock(
            return_value=_mk_resp(200, {"results": [group_data], "size": 1})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_group_members = AsyncMock(return_value=([], []))
        c._get_app_users_by_emails = AsyncMock(return_value=[])
        await c._sync_user_groups()
        assert ds.get_groups.call_count == 1  # size=1 < batch_size=50

    @pytest.mark.asyncio
    async def test_outer_exception_is_reraised(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("ds fail"))
        with pytest.raises(RuntimeError):
            await c._sync_user_groups()


# ===========================================================================
# _sync_spaces() – NOT_IN filter, exceptions, edge cases
# ===========================================================================


class TestSyncSpacesAdditional:
    @pytest.mark.asyncio
    async def test_not_in_filter_excludes_spaces(self):
        c = _mk_connector()
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.NOT_IN
        space_filter.get_value.return_value = ["EXCL"]
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=space_filter)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(
                200,
                {
                    "results": [
                        {"id": "1", "key": "EXCL", "name": "Excluded"},
                        {"id": "2", "key": "KEEP", "name": "Kept"},
                    ],
                    "_links": {"base": "https://example.atlassian.net/wiki"},
                },
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(
            return_value=MagicMock(
                external_group_id="2",
                name="Kept",
                short_name="KEEP",
                group_type=RecordGroupType.CONFLUENCE_SPACES,
                connector_name=Connectors.CONFLUENCE,
                connector_id="conn-new-1",
            )
        )

        spaces = await c._sync_spaces()
        # EXCL should be filtered out on the client side
        assert len(spaces) == 1

    @pytest.mark.asyncio
    async def test_empty_spaces_data_breaks(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        spaces = await c._sync_spaces()
        assert spaces == []
        c.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_space_exception_continues(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(
                200,
                {
                    "results": [{"id": "sp-1", "name": "Space1", "key": "S1"}],
                    "_links": {},
                },
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_space_permissions = AsyncMock(side_effect=RuntimeError("perm fail"))

        # Should not raise; per-space exception is caught
        spaces = await c._sync_spaces()
        assert spaces == []

    @pytest.mark.asyncio
    async def test_space_with_no_id_is_skipped(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(
                200,
                {"results": [{"id": None, "name": "NoId"}], "_links": {}},
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        spaces = await c._sync_spaces()
        assert spaces == []
        c.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_transform_none_skips_space(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(
                200,
                {"results": [{"id": "sp-1", "name": "S1", "key": "S1"}], "_links": {}},
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=None)

        spaces = await c._sync_spaces()
        assert spaces == []

    @pytest.mark.asyncio
    async def test_outer_exception_is_reraised(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fatal"))
        with pytest.raises(RuntimeError):
            await c._sync_spaces()

    @pytest.mark.asyncio
    async def test_cursor_extraction_fail_stops_pagination(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        mock_rg = MagicMock(
            external_group_id="sp-1", name="S1", short_name="S1",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE, connector_id="conn-new-1",
        )

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(
                200,
                {
                    "results": [{"id": "sp-1", "name": "S1", "key": "S1"}],
                    "_links": {"next": "NO_CURSOR_PARAM_HERE", "base": "https://x.atlassian.net"},
                },
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=mock_rg)
        c._extract_cursor_from_next_link = MagicMock(return_value=None)

        spaces = await c._sync_spaces()
        assert len(spaces) == 1
        assert ds.get_spaces.call_count == 1


# ===========================================================================
# _sync_content() helpers and main paths
# ===========================================================================


def _setup_sync_content(c):
    c.sync_filters = MagicMock()
    c.sync_filters.get = MagicMock(return_value=None)
    c.indexing_filters = MagicMock()
    c.indexing_filters.is_enabled = MagicMock(return_value=True)
    c.pages_sync_point = MagicMock()
    c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
    c.pages_sync_point.update_sync_point = AsyncMock()


class TestSyncContentPages:
    @pytest.mark.asyncio
    async def test_pages_basic(self):
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {
            "id": "p1", "title": "Page1",
            "space": {"id": "sp1", "key": "S1"},
            "version": {"number": 1, "when": "2024-01-01T10:00:00Z"},
            "history": {"createdDate": "2024-01-01T09:00:00Z",
                        "lastUpdated": {"when": "2024-01-01T10:00:00Z", "number": 1}},
            "ancestors": [],
            "_links": {"webui": "/spaces/S1/pages/p1",
                       "self": "https://x.atlassian.net/wiki/rest/api/content/p1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_blogpost_uses_blogpost_api(self):
        c = _mk_connector()
        _setup_sync_content(c)
        blog_data = {
            "id": "b1", "title": "Blog1",
            "space": {"id": "sp1", "key": "S1"},
            "version": {"number": 1, "when": "2024-01-01T10:00:00Z"},
            "history": {"createdDate": "2024-01-01T09:00:00Z",
                        "lastUpdated": {"when": "2024-01-01T10:00:00Z", "number": 1}},
            "ancestors": [],
            "_links": {"webui": "/spaces/S1/blog/b1",
                       "self": "https://x.atlassian.net/wiki/rest/api/content/b1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_blogposts_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [blog_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-b1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_BLOGPOST)
        ds.get_blogposts_v1.assert_called_once()
        assert not ds.get_pages_v1.called

    @pytest.mark.asyncio
    async def test_failed_response_breaks(self):
        c = _mk_connector()
        _setup_sync_content(c)
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_item_id_is_skipped(self):
        c = _mk_connector()
        _setup_sync_content(c)
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [{"id": None, "title": "NoId"}], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_transform_none_skips_item(self):
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {"id": "p1", "title": "P1",
                     "childTypes": {"comment": {"value": False}},
                     "children": {"attachment": {"results": []}}}
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=None))
        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_indexing_disabled_sets_auto_index_off(self):
        c = _mk_connector()
        _setup_sync_content(c)
        c.indexing_filters.is_enabled = MagicMock(return_value=False)
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True, indexing_status=None)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_read_permission_sets_inherit_false(self):
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        read_perm = Permission(type=PermissionType.READ, entity_type=EntityType.USER, external_id="u1")
        c._fetch_page_permissions = AsyncMock(return_value=[read_perm])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        assert mock_record.inherit_permissions is False

    @pytest.mark.asyncio
    async def test_page_sync_does_not_create_comment_records(self):
        """Comments are embedded in page blocks at stream time, not synced as CommentRecords."""
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": True}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True, indexing_status=None)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_called_once()
        synced_records = c.data_entities_processor.on_new_records.call_args[0][0]
        for record, _perms in synced_records:
            record_type = getattr(record, "record_type", None)
            if record_type is not None:
                assert record_type not in (RecordType.COMMENT, RecordType.INLINE_COMMENT)

    @pytest.mark.asyncio
    async def test_attachment_without_id_skipped(self):
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": [{"id": None, "title": "file.pdf"}]}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        assert len(saved) == 1  # only page, no attachment

    @pytest.mark.asyncio
    async def test_attachment_indexing_disabled(self):
        c = _mk_connector()
        _setup_sync_content(c)
        from app.connectors.core.registry.filters import IndexingFilterKey
        c.indexing_filters.is_enabled = MagicMock(
            side_effect=lambda key: key != IndexingFilterKey.PAGE_ATTACHMENTS
        )
        att = {"id": "att-1", "title": "doc.pdf", "extensions": {"fileSize": 100}}
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": [att]}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))
        att_record = MagicMock(indexing_status=None)
        c._transform_to_attachment_file_record = MagicMock(return_value=att_record)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        assert att_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_attachment_exception_is_caught(self):
        c = _mk_connector()
        _setup_sync_content(c)
        att = {"id": "att-1", "title": "doc.pdf"}
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": [att]}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [page_data], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))
        c._transform_to_attachment_file_record = MagicMock(side_effect=RuntimeError("att error"))

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        # Page still saved despite attachment error
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_outer_exception_is_reraised(self):
        c = _mk_connector()
        _setup_sync_content(c)
        c.pages_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("sync point fail"))
        with pytest.raises(RuntimeError, match="sync point fail"):
            await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)

    @pytest.mark.asyncio
    async def test_content_ids_not_in_filter_logging(self):
        """Content IDs NOT_IN filter passes correct operator string."""
        c = _mk_connector()
        content_filter = MagicMock()
        content_filter.get_value.return_value = ["excl-1"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        content_filter.get_operator.return_value = mock_op

        c.sync_filters = MagicMock()
        def get_filter(key):
            return content_filter if key == SyncFilterKey.PAGE_IDS else None
        c.sync_filters.get = MagicMock(side_effect=get_filter)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        assert call_kwargs["page_ids"] == ["excl-1"]
        assert call_kwargs["page_ids_operator"] == "not_in"

    @pytest.mark.asyncio
    async def test_modified_after_and_checkpoint_uses_max(self):
        """When both filter and checkpoint exist, use the later date."""
        c = _mk_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00.000Z", None)

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(
            side_effect=lambda key: modified_filter if key == SyncFilterKey.MODIFIED else None
        )
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-06-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-06-01T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_cursor_extraction_fail_stops_pagination(self):
        c = _mk_connector()
        _setup_sync_content(c)
        page_data = {
            "id": "p1", "title": "P1", "space": {"id": "sp1"},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(
            200, {"results": [page_data], "_links": {"next": "NO_CURSOR"}}
        ))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))
        c._extract_cursor_from_next_link = MagicMock(return_value=None)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        assert ds.get_pages_v1.call_count == 1


# ===========================================================================
# _sync_permission_changes_from_audit_log() – outer exception
# ===========================================================================


class TestSyncAuditLogException:
    @pytest.mark.asyncio
    async def test_outer_exception_is_reraised(self):
        c = _mk_connector()
        c.audit_log_sync_point = MagicMock()
        c.audit_log_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("audit fail")
        )
        with pytest.raises(RuntimeError, match="audit fail"):
            await c._sync_permission_changes_from_audit_log()


# ===========================================================================
# _fetch_permission_audit_logs() – empty results
# ===========================================================================


class TestFetchPermissionAuditLogsEmpty:
    @pytest.mark.asyncio
    async def test_empty_audit_results(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_audit_logs = AsyncMock(return_value=_mk_resp(200, {"results": [], "size": 0}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        titles = await c._fetch_permission_audit_logs(1000, 2000)
        assert titles == []


# ===========================================================================
# _fetch_space_permissions() – failure paths
# ===========================================================================


class TestFetchSpacePermissions:
    @pytest.mark.asyncio
    async def test_failed_response_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_space_permissions("sp1", "Space1")
        assert result == []

    @pytest.mark.asyncio
    async def test_cursor_null_stops_pagination(self):
        c = _mk_connector()
        perm_data = {
            "principal": {"type": "user", "id": "u1"},
            "operation": {"key": "read", "targetType": "space"}
        }
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(
            return_value=_mk_resp(200, {"results": [perm_data], "_links": {"next": "NO_CURSOR"}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._transform_space_permission = AsyncMock(return_value=None)
        c._extract_cursor_from_next_link = MagicMock(return_value=None)
        await c._fetch_space_permissions("sp1", "Space1")
        assert ds.get_space_permissions_assignments.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        result = await c._fetch_space_permissions("sp1", "Space1")
        assert result == []


# ===========================================================================
# _fetch_page_permissions() – error paths
# ===========================================================================


class TestFetchPagePermissionsErrors:
    @pytest.mark.asyncio
    async def test_failed_response_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_permissions_v1 = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_permissions("page-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        result = await c._fetch_page_permissions("page-1")
        assert result == []


# ===========================================================================
# _fetch_page_comments_recursive() – additional branches
# ===========================================================================


class TestFetchPageCommentsRecursiveAdditional:
    @pytest.mark.asyncio
    async def test_unsupported_record_type_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_comments_recursive(
            "p1", RecordType.FILE, "footer"
        )
        assert result == []
        ds.get_page_footer_comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_blogpost_footer_comments(self):
        c = _mk_connector()
        comment_data = {"id": "100", "body": {}}
        ds = MagicMock()
        ds.get_blog_post_footer_comments = AsyncMock(
            return_value=_mk_resp(200, {"results": [comment_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        result = await c._fetch_page_comments_recursive(
            "200", RecordType.CONFLUENCE_BLOGPOST, "footer"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_blogpost_inline_comments(self):
        c = _mk_connector()
        comment_data = {"id": "201", "body": {}}
        ds = MagicMock()
        ds.get_blog_post_inline_comments = AsyncMock(
            return_value=_mk_resp(200, {"results": [comment_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        result = await c._fetch_page_comments_recursive(
            "202", RecordType.CONFLUENCE_BLOGPOST, "inline"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_page_footer_includes_comment_dicts(self):
        c = _mk_connector()
        comment_data = {"id": "300", "body": {}}
        ds = MagicMock()
        ds.get_page_footer_comments = AsyncMock(
            return_value=_mk_resp(200, {"results": [comment_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        result = await c._fetch_page_comments_recursive(
            "100", RecordType.CONFLUENCE_PAGE, "footer"
        )
        assert len(result) == 1
        assert result[0]["id"] == "300"

    @pytest.mark.asyncio
    async def test_cursor_extraction_fail_stops(self):
        c = _mk_connector()
        comment_data = {"id": "400", "body": {}}
        ds = MagicMock()
        ds.get_page_footer_comments = AsyncMock(
            return_value=_mk_resp(
                200, {"results": [comment_data], "_links": {"next": "NO_CURSOR"}}
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])
        c._extract_cursor_from_next_link = MagicMock(return_value=None)

        await c._fetch_page_comments_recursive(
            "100", RecordType.CONFLUENCE_PAGE, "footer"
        )
        assert ds.get_page_footer_comments.call_count == 1


# ===========================================================================
# _fetch_comment_children_recursive() – inline, edge cases
# ===========================================================================


class TestFetchCommentChildrenRecursiveAdditional:
    @pytest.mark.asyncio
    async def test_inline_children(self):
        c = _mk_connector()
        child_data = {"id": "500", "body": {}}
        ds = MagicMock()
        ds.get_inline_comment_children = AsyncMock(
            return_value=_mk_resp(200, {"results": [child_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        # Stub only grandchild recursion to avoid infinite loops
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        from app.connectors.sources.atlassian.confluence_cloud.connector import (
            ConfluenceConnector,
        )
        result = await ConfluenceConnector._fetch_comment_children_recursive(
            c, "100", "inline", RecordType.CONFLUENCE_PAGE
        )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_child_without_id_still_appended(self):
        """Child dicts without id are still collected; recursion uses None id safely."""
        c = _mk_connector()
        child_data = {"id": None, "body": {}}
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(
            return_value=_mk_resp(200, {"results": [child_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        from app.connectors.sources.atlassian.confluence_cloud.connector import (
            ConfluenceConnector,
        )
        result = await ConfluenceConnector._fetch_comment_children_recursive(
            c, "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_exception_returns_partial(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(side_effect=RuntimeError("fail"))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c._fetch_comment_children_recursive(
            "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert result == []


# ===========================================================================
# _construct_web_url()
# ===========================================================================


class TestConstructWebUrl:
    def test_with_base_url_param(self):
        c = _mk_connector()
        result = c._construct_web_url(
            {"webui": "/spaces/TEST/pages/1"},
            base_url="https://example.atlassian.net/wiki"
        )
        assert result == "https://example.atlassian.net/wiki/spaces/TEST/pages/1"

    def test_with_self_link_fallback(self):
        c = _mk_connector()
        links = {
            "webui": "/spaces/TEST/pages/1",
            "self": "https://example.atlassian.net/wiki/rest/api/content/1",
        }
        result = c._construct_web_url(links)
        assert result == "https://example.atlassian.net/wiki/spaces/TEST/pages/1"

    def test_no_webui_returns_none(self):
        c = _mk_connector()
        result = c._construct_web_url({})
        assert result is None

    def test_no_base_no_self_returns_none(self):
        c = _mk_connector()
        result = c._construct_web_url({"webui": "/path"})
        assert result is None


# ===========================================================================
# _extract_cursor_from_next_link() – exception path
# ===========================================================================


class TestExtractCursorException:
    def test_exception_returns_none(self):
        c = _mk_connector()
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.urlparse",
            side_effect=RuntimeError("parse fail"),
        ):
            result = c._extract_cursor_from_next_link("https://example.com?cursor=abc")
        assert result is None


# ===========================================================================
# _sync_content_permissions_by_titles() – batch exception raises ValueError
# ===========================================================================


class TestSyncContentPermissionsByTitlesBatchError:
    @pytest.mark.asyncio
    async def test_batch_exception_raises_valueerror(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("ds fail"))
        with pytest.raises(ValueError, match="Failed to sync permissions"):
            await c._sync_content_permissions_by_titles(["Title1"])


# ===========================================================================
# _get_app_users_by_emails() – missing and exception paths
# ===========================================================================


class TestGetAppUsersByEmailsMissing:
    @pytest.mark.asyncio
    async def test_missing_users_debug(self):
        c = _mk_connector()
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        result = await c._get_app_users_by_emails(["missing@x.com"])
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _mk_connector()
        c.data_entities_processor.get_all_app_users = AsyncMock(
            side_effect=RuntimeError("db fail")
        )
        result = await c._get_app_users_by_emails(["u@x.com"])
        assert result == []


# ===========================================================================
# _check_and_fetch_updated_blogpost()
# ===========================================================================


class TestCheckAndFetchUpdatedBlogpost:
    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="3001")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(return_value=_mk_resp(200, {"id": "3002", "version": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="3002")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "3003", "version": {"number": 5}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="3003", external_revision_id="5")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_changed_returns_result(self):
        c = _mk_connector()
        blogpost_data = {
            "id": "3004", "title": "Blog",
            "version": {"number": 7},
            "space": {"id": "sp1"},
            "_links": {},
        }
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(return_value=_mk_resp(200, blogpost_data))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_rec = MagicMock(inherit_permissions=True)
        c._transform_to_webpage_record = MagicMock(return_value=mock_rec)
        record = MagicMock(external_record_id="3004", external_revision_id="3")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is not None
        assert result[0] is mock_rec

    @pytest.mark.asyncio
    async def test_transform_none_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "3005", "version": {"number": 7}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._transform_to_webpage_record = MagicMock(return_value=None)
        record = MagicMock(external_record_id="3005", external_revision_id="3")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        record = MagicMock(external_record_id="3006")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_attachment()
# ===========================================================================


class TestCheckAndFetchUpdatedAttachment:
    @pytest.mark.asyncio
    async def test_no_parent_returns_none(self):
        c = _mk_connector()
        record = MagicMock(external_record_id="att-1", parent_external_record_id=None)
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(external_record_id="att-1", parent_external_record_id="p1")
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "att-1", "version": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(external_record_id="att-1", parent_external_record_id="p1")
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "att-1", "version": {"number": 3}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(
            external_record_id="att-1",
            parent_external_record_id="p1",
            external_revision_id="3",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_changed_returns_data(self):
        c = _mk_connector()
        att_data = {"id": "att-1", "title": "doc.pdf", "version": {"number": 5}, "_links": {}}
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(200, att_data))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_att_rec = MagicMock()
        c._transform_to_attachment_file_record = MagicMock(return_value=mock_att_rec)
        record = MagicMock(
            external_record_id="att-1",
            parent_external_record_id="p1",
            external_revision_id="2",
            external_record_group_id="sp1",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_transform_none_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "att-1", "version": {"number": 5}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c._transform_to_attachment_file_record = MagicMock(return_value=None)
        record = MagicMock(
            external_record_id="att-1",
            parent_external_record_id="p1",
            external_revision_id="2",
            external_record_group_id="sp1",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        record = MagicMock(external_record_id="att-1", parent_external_record_id="p1")
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None


# ===========================================================================
# get_signed_url()
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_empty_string(self):
        c = _mk_connector()
        result = await c.get_signed_url(MagicMock())
        assert result == ""


# ===========================================================================
# stream_record() – comment and unsupported types
# ===========================================================================


class TestStreamRecordAdditional:
    @pytest.mark.asyncio
    async def test_comment_streaming_raises_404_when_not_found(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(
            record_type=RecordType.COMMENT,
            record_name="comment",
            external_record_id="321748993",
        )
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 404
        assert "321748993" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_comment_streaming_returns_html(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(
            return_value=_mk_resp(
                200,
                {"body": {"storage": {"value": "<p>Comment body</p>"}}},
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(
            record_type=RecordType.COMMENT,
            record_name="comment",
            external_record_id="321748993",
        )
        result = await c.stream_record(record)
        assert result is not None
        assert result.media_type == MimeTypes.HTML.value

    @pytest.mark.asyncio
    async def test_inline_comment_streaming_raises_404_when_not_found(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_inline_comment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(
            record_type=RecordType.INLINE_COMMENT,
            record_name="inline comment",
            external_record_id="321814529",
        )
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 404
        assert "321814529" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_http_exception(self):
        c = _mk_connector()
        record = MagicMock(
            record_type="UNKNOWN_TYPE",
            record_name="unknown",
            external_record_id="x1",
        )
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400


# ===========================================================================
# _fetch_page_content() – blogpost path and exception wrapping
# ===========================================================================


class TestFetchPageContentAdditional:
    @pytest.mark.asyncio
    async def test_blogpost_fetch(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blogpost_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {
                "body": {"styled_view": {"value": "<html>Blog content</html>"}}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert "Blog content" in result

    @pytest.mark.asyncio
    async def test_unsupported_record_type_raises_400(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("x1", RecordType.FILE)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_http_exception_raises_500(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("network fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_body_returns_placeholder(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {"body": {"styled_view": {"value": ""}}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content available" in result


# ===========================================================================
# reindex_records() – not initialized path
# ===========================================================================


class TestReindexRecordsNotInitialized:
    @pytest.mark.asyncio
    async def test_not_initialized_raises(self):
        c = _mk_connector()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])


# ===========================================================================
# _map_confluence_permission() – remaining branches
# ===========================================================================


class TestMapConfluencePermissionBranches:
    def test_delete_space(self):
        c = _mk_connector()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_comment_defaults_to_read(self):
        c = _mk_connector()
        assert c._map_confluence_permission("create", "comment") == PermissionType.READ

    def test_delete_comment_defaults_to_read(self):
        c = _mk_connector()
        assert c._map_confluence_permission("delete", "comment") == PermissionType.READ

    def test_archive_blogpost(self):
        c = _mk_connector()
        assert c._map_confluence_permission("archive", "blogpost") == PermissionType.WRITE

    def test_restrict_content_returns_read(self):
        c = _mk_connector()
        assert c._map_confluence_permission("restrict_content", "space") == PermissionType.READ


# ===========================================================================
# _create_pseudo_group() – success and exception
# ===========================================================================


class TestCreatePseudoGroup:
    @pytest.mark.asyncio
    async def test_creates_pseudo_group(self):
        c = _mk_connector()
        c.data_entities_processor.on_new_user_groups = AsyncMock()
        result = await c._create_pseudo_group("acc-123")
        assert result is not None
        assert result.source_user_group_id == "acc-123"
        assert PSEUDO_USER_GROUP_PREFIX in result.name

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c.data_entities_processor.on_new_user_groups = AsyncMock(
            side_effect=RuntimeError("db fail")
        )
        result = await c._create_pseudo_group("acc-err")
        assert result is None


# ===========================================================================
# _fetch_group_members() – member without email and pagination
# ===========================================================================


class TestFetchGroupMembersAdditional:
    @pytest.mark.asyncio
    async def test_member_without_email_is_skipped(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_group_members = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [
                    {"email": "valid@x.com", "displayName": "Valid"},
                    {"email": "", "displayName": "NoEmail"},
                ],
                "size": 2,
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        emails, account_ids = await c._fetch_group_members("g1", "Group1")
        assert emails == ["valid@x.com"]
        assert account_ids == []

    @pytest.mark.asyncio
    async def test_pagination_fetches_all_members(self):
        c = _mk_connector()
        full_batch = [{"email": f"u{i}@x.com", "displayName": f"U{i}"} for i in range(100)]
        partial = [{"email": "last@x.com", "displayName": "Last"}]
        ds = MagicMock()
        ds.get_group_members = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": full_batch, "size": 100}),
                _mk_resp(200, {"results": partial, "size": 1}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        emails, account_ids = await c._fetch_group_members("g1", "Group1")
        assert len(emails) == 101
        assert account_ids == []


# ===========================================================================
# Additional targeted tests for remaining uncovered lines
# ===========================================================================


class TestSyncUserGroupsTransformNone:
    """Test _sync_user_groups when _transform_to_user_group returns None (line 652)."""

    @pytest.mark.asyncio
    async def test_transform_none_skips_group(self):
        c = _mk_connector()
        # Missing name → transform returns None
        group_data = {"id": "grp-1", "name": None}
        ds = MagicMock()
        ds.get_groups = AsyncMock(
            return_value=_mk_resp(200, {"results": [group_data], "size": 1})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_group_members = AsyncMock(return_value=(["u@x.com"], []))
        await c._sync_user_groups()
        c.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination_continues_when_size_equals_batch(self):
        c = _mk_connector()
        group_page_1 = {"id": "grp-1", "name": "Team A"}
        ds = MagicMock()
        ds.get_groups = AsyncMock(side_effect=[
            _mk_resp(200, {"results": [group_page_1], "size": 50}),
            _mk_resp(200, {"results": [], "size": 0}),
        ])
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_group_members = AsyncMock(return_value=([], []))

        await c._sync_user_groups()

        assert ds.get_groups.await_count == 2
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()


class TestSyncSpacesFailedResponse:
    """Test _sync_spaces failure response break (lines 728-729)."""

    @pytest.mark.asyncio
    async def test_failed_response_breaks_loop(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        spaces = await c._sync_spaces()
        assert spaces == []
        c.data_entities_processor.on_new_record_groups.assert_not_called()


class TestSyncContentFilterPaths:
    """Cover remaining filter logging paths in _sync_content."""

    @pytest.mark.asyncio
    async def test_modified_after_only_no_checkpoint(self):
        """modified_after from filter, no checkpoint (line 1033)."""
        c = _mk_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = ("2024-03-01T00:00:00.000Z", None)

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(
            side_effect=lambda key: modified_filter if key == SyncFilterKey.MODIFIED else None
        )
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-03-01T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_checkpoint_only_no_filter(self):
        """checkpoint exists, no modified filter (lines 1035-1036)."""
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-05-01T00:00:00.000Z"}
        )
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        assert call_kwargs["modified_after"] == "2024-05-01T00:00:00.000Z"

    @pytest.mark.asyncio
    async def test_modified_before_and_created_dates_logged(self):
        """modified_before, created_after, created_before logging paths."""
        c = _mk_connector()
        modified_filter = MagicMock()
        modified_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")
        created_filter = MagicMock()
        created_filter.get_datetime_iso.return_value = ("2024-02-01T00:00:00Z", "2024-11-30T23:59:59Z")

        c.sync_filters = MagicMock()
        def get_filter(key):
            if key == SyncFilterKey.MODIFIED:
                return modified_filter
            if key == SyncFilterKey.CREATED:
                return created_filter
            return None
        c.sync_filters.get = MagicMock(side_effect=get_filter)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        assert call_kwargs["modified_before"] == "2024-12-31T23:59:59Z"
        assert call_kwargs["created_after"] == "2024-02-01T00:00:00Z"
        assert call_kwargs["created_before"] == "2024-11-30T23:59:59Z"


class TestSyncUsersNoMigration:
    """Test _sync_users user without email@ doesn't trigger migration (line 568->567)."""

    @pytest.mark.asyncio
    async def test_user_without_source_id_skips_migration(self):
        c = _mk_connector()
        # Create a user where source_user_id is None → migration skipped
        user_result = {
            "user": {"accountId": "", "email": "noid@x.com", "displayName": "NoId User"}
        }
        ds = MagicMock()
        ds.search_users = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": [user_result]}),
                _mk_resp(200, {"results": []}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        # No email → skip; but user with no accountId → transform returns None
        # To trigger the migration skip branch, we need a user with email but no source_user_id
        # Make transform return an AppUser with no source_user_id
        mock_user = MagicMock()
        mock_user.email = "noid@x.com"
        mock_user.source_user_id = None  # No source_user_id → migration skipped
        with patch.object(c, "_transform_to_app_user", return_value=mock_user):
            await c._sync_users()
        # Migration should not be called since source_user_id is None
        c.data_entities_processor.migrate_group_to_user_by_external_id.assert_not_called()


class TestCheckAndFetchUpdatedPageAdditional:
    """Additional tests for _check_and_fetch_updated_page (lines 3559-3590)."""

    @pytest.mark.asyncio
    async def test_no_version_number_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {"id": "p1", "version": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="p1", external_revision_id="3")
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_transform_none_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {"id": "p1", "version": {"number": 7}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._transform_to_webpage_record = MagicMock(return_value=None)
        record = MagicMock(external_record_id="p1", external_revision_id="3")
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_permissions_set_inherit_false(self):
        c = _mk_connector()
        page_data = {"id": "p1", "version": {"number": 7}, "_links": {}}
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(return_value=_mk_resp(200, page_data))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        read_perm = Permission(type=PermissionType.READ, entity_type=EntityType.USER, external_id="u1")
        c._fetch_page_permissions = AsyncMock(return_value=[read_perm])
        mock_rec = MagicMock(inherit_permissions=True)
        c._transform_to_webpage_record = MagicMock(return_value=mock_rec)
        record = MagicMock(external_record_id="p1", external_revision_id="3")
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is not None
        assert mock_rec.inherit_permissions is False

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        record = MagicMock(external_record_id="p1")
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedBlogpostReadPerms:
    """Test read permissions setting inherit_permissions=False (line 3639)."""

    @pytest.mark.asyncio
    async def test_read_permissions_set_inherit_false(self):
        c = _mk_connector()
        blogpost_data = {
            "id": "3010", "title": "Blog",
            "version": {"number": 8},
            "space": {"id": "sp1"}, "_links": {},
        }
        ds = MagicMock()
        ds.get_blog_post_by_id = AsyncMock(return_value=_mk_resp(200, blogpost_data))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        read_perm = Permission(type=PermissionType.READ, entity_type=EntityType.USER, external_id="u1")
        c._fetch_page_permissions = AsyncMock(return_value=[read_perm])
        mock_rec = MagicMock(inherit_permissions=True)
        c._transform_to_webpage_record = MagicMock(return_value=mock_rec)
        record = MagicMock(external_record_id="3010", external_revision_id="4")
        result = await c._check_and_fetch_updated_blogpost("org-1", record)
        assert result is not None
        assert mock_rec.inherit_permissions is False


class TestCheckAndFetchUpdatedAttachmentParentNode:
    """Test parent_node_id is set when parent record found (line 3751)."""

    @pytest.mark.asyncio
    async def test_parent_node_id_set_when_parent_found(self):
        c = _mk_connector()
        att_data = {"id": "att-10", "title": "doc.pdf", "version": {"number": 5}, "_links": {}}
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(200, att_data))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        parent_record = MagicMock(id="internal-parent-id")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=parent_record)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_att_rec = MagicMock()
        c._transform_to_attachment_file_record = MagicMock(return_value=mock_att_rec)

        record = MagicMock(
            external_record_id="att-10",
            parent_external_record_id="p-10",
            external_revision_id="2",
            external_record_group_id="sp1",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is not None
        # Verify parent_node_id was passed
        call_kwargs = c._transform_to_attachment_file_record.call_args[1]
        assert call_kwargs.get("parent_node_id") == "internal-parent-id"


class TestFetchSpacePermissionsAdditional:
    """Additional fetch_space_permissions paths."""

    @pytest.mark.asyncio
    async def test_empty_results_breaks(self):
        """Empty permissions_data breaks the loop (line 1594)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(
            return_value=_mk_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_space_permissions("sp1", "Space1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_next_url_stops_pagination(self):
        """No next URL stops pagination (line 1599)."""
        c = _mk_connector()
        perm_data = {
            "principal": {"type": "user", "id": "u1"},
            "operation": {"key": "read", "targetType": "space"}
        }
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(
            return_value=_mk_resp(200, {"results": [perm_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._transform_space_permission = AsyncMock(return_value=None)
        result = await c._fetch_space_permissions("sp1", "Space1")
        assert ds.get_space_permissions_assignments.call_count == 1


class TestFetchPageCommentsRecursiveResponseFail:
    """Test _fetch_page_comments_recursive when response fails."""

    @pytest.mark.asyncio
    async def test_failed_response_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_footer_comments = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_comments_recursive(
            "100", RecordType.CONFLUENCE_PAGE, "footer"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_footer_comments = AsyncMock(
            return_value=_mk_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_comments_recursive(
            "100", RecordType.CONFLUENCE_PAGE, "footer"
        )
        assert result == []


class TestFetchCommentChildrenResponseFail:
    """Test _fetch_comment_children_recursive when response fails."""

    @pytest.mark.asyncio
    async def test_failed_response_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_comment_children_recursive(
            "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(
            return_value=_mk_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_comment_children_recursive(
            "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_child_included_in_results(self):
        c = _mk_connector()
        child_data = {"id": "700", "body": {}}
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(
            return_value=_mk_resp(200, {"results": [child_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])

        from app.connectors.sources.atlassian.confluence_cloud.connector import (
            ConfluenceConnector,
        )
        result = await ConfluenceConnector._fetch_comment_children_recursive(
            c, "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_cursor_extraction_fail_stops(self):
        c = _mk_connector()
        child_data = {"id": "800", "body": {}}
        ds = MagicMock()
        ds.get_footer_comment_children = AsyncMock(
            return_value=_mk_resp(
                200, {"results": [child_data], "_links": {"next": "NO_CURSOR"}}
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_comment_children_recursive = AsyncMock(return_value=[])
        c._extract_cursor_from_next_link = MagicMock(return_value=None)

        from app.connectors.sources.atlassian.confluence_cloud.connector import (
            ConfluenceConnector,
        )
        await ConfluenceConnector._fetch_comment_children_recursive(
            c, "100", "footer", RecordType.CONFLUENCE_PAGE
        )
        assert ds.get_footer_comment_children.call_count == 1


class TestSyncContentPermissionsByTitlesAdditional:
    """Additional coverage for _sync_content_permissions_by_titles."""

    @pytest.mark.asyncio
    async def test_failed_search_continues(self):
        """When search fails, continue to next batch (lines 1465-1466)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        # Should not raise ValueError since has_failures will be set only for Exception, not for soft failure
        # Actually looking at the code: if not response or response.status != 200: continue (no has_failures)
        # So this should complete without raising
        await c._sync_content_permissions_by_titles(["Title1"])
        # No exception = soft failure handled

    @pytest.mark.asyncio
    async def test_no_content_found_continues(self):
        """When no content found, continue (lines 1472-1473)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(
            return_value=_mk_resp(200, {"results": []})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_content_permissions_by_titles(["Title1"])

    @pytest.mark.asyncio
    async def test_item_without_id_skipped(self):
        """Item without ID skipped (line 1484)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(
            return_value=_mk_resp(200, {"results": [{"id": None, "title": "T1", "type": "page"}]})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_content_permissions_by_titles(["T1"])

    @pytest.mark.asyncio
    async def test_unknown_type_skipped(self):
        """Unknown content type skipped (lines 1489-1493)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(
            return_value=_mk_resp(200, {"results": [{"id": "x1", "title": "T1", "type": "unknown"}]})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_content_permissions_by_titles(["T1"])

    @pytest.mark.asyncio
    async def test_record_not_in_db_skipped(self):
        """Record not in DB is skipped (lines 1501-1508)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(
            return_value=_mk_resp(200, {"results": [{"id": "p1", "title": "T1", "type": "page"}]})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await c._sync_content_permissions_by_titles(["T1"])

    @pytest.mark.asyncio
    async def test_transform_none_skips_item(self):
        """Transform returns None, item skipped (line 1515)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(
            return_value=_mk_resp(200, {"results": [{"id": "p1", "title": "T1", "type": "page"}]})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        existing = MagicMock()
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        c._transform_to_webpage_record = MagicMock(return_value=None)
        await c._sync_content_permissions_by_titles(["T1"])


class TestFetchPermissionAuditLogsPagination:
    """Test _fetch_permission_audit_logs pagination (line 1390)."""

    @pytest.mark.asyncio
    async def test_continues_when_size_equals_batch(self):
        """When size = batch_size (100), continue to next page."""
        c = _mk_connector()
        audit_record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "TestSpace"},
                {"objectType": "Page", "name": "TestPage"},
            ],
        }
        full_page = [audit_record] * 100
        partial_page = [audit_record]

        ds = MagicMock()
        ds.get_audit_logs = AsyncMock(
            side_effect=[
                _mk_resp(200, {"results": full_page, "size": 100}),
                _mk_resp(200, {"results": partial_page, "size": 1}),
            ]
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        titles = await c._fetch_permission_audit_logs(1000, 2000)
        # Both pages were fetched
        assert ds.get_audit_logs.call_count == 2


class TestTransformToSpaceRecordGroupAdditional:
    """Test _transform_to_space_record_group additional paths (lines 2431-2450)."""

    def test_with_base_url_and_webui(self):
        c = _mk_connector()
        space_data = {
            "id": "sp-1",
            "name": "TestSpace",
            "key": "TEST",
            "description": "A test space",
            "createdAt": "2024-01-01T00:00:00Z",
            "_links": {"webui": "/spaces/TEST"},
        }
        result = c._transform_to_space_record_group(space_data, base_url="https://x.atlassian.net/wiki")
        assert result is not None
        assert result.name == "TestSpace"
        assert result.short_name == "TEST"

    def test_missing_id_returns_none(self):
        c = _mk_connector()
        result = c._transform_to_space_record_group({"id": None, "name": "Test"})
        assert result is None


class TestTransformToWebpageRecordException:
    """Test _transform_to_webpage_record exception (line 2475)."""

    def test_exception_returns_none(self):
        c = _mk_connector()
        # Pass data that will fail the transform (no required structure)
        # by patching the org_id to raise
        with patch.object(c.data_entities_processor, "org_id", new_callable=lambda: property(lambda self: (_ for _ in ()).throw(RuntimeError("org fail")))):
            result = c._transform_to_webpage_record({"id": "p1"}, RecordType.CONFLUENCE_PAGE)
        assert result is None


class TestTransformToFolderFileRecordException:
    """Test _transform_to_folder_file_record exception (lines 2774-2776)."""

    def test_exception_returns_none(self):
        c = _mk_connector()
        with patch.object(c.data_entities_processor, "org_id", new_callable=lambda: property(lambda self: (_ for _ in ()).throw(RuntimeError("org fail")))):
            result = c._transform_to_folder_file_record({"id": "f1"})
        assert result is None


class TestTransformToAttachmentFileRecordException:
    """Test _transform_to_attachment_file_record exception (lines 2927-2929)."""

    def test_exception_returns_none(self):
        c = _mk_connector()
        with patch.object(c.data_entities_processor, "org_id", new_callable=lambda: property(lambda self: (_ for _ in ()).throw(RuntimeError("org fail")))):
            result = c._transform_to_attachment_file_record(
                {"id": "att-1", "title": "doc.pdf"}, "p1", "sp1"
            )
        assert result is None


class TestTransformToUserGroupException:
    """Test _transform_to_user_group exception (lines 3168-3170)."""

    def test_exception_returns_none(self):
        c = _mk_connector()
        with patch.object(c.data_entities_processor, "org_id", new_callable=lambda: property(lambda self: (_ for _ in ()).throw(RuntimeError("org fail")))):
            result = c._transform_to_user_group({"id": "g1", "name": "Group1"})
        assert result is None


class TestCreatePermissionFromPrincipalPseudoGroup:
    """Test _create_permission_from_principal pseudo-group path (line 2143->2153)."""

    @pytest.mark.asyncio
    async def test_user_not_found_creates_pseudo_group(self):
        c = _mk_connector()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        pseudo_group = MagicMock()
        pseudo_group.source_user_group_id = "acc-xyz"
        c._create_pseudo_group = AsyncMock(return_value=pseudo_group)

        result = await c._create_permission_from_principal(
            "user", "acc-xyz", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert result is not None
        assert result.entity_type == EntityType.GROUP


class TestMapConfluencePermissionSpaceWrite:
    """Test _map_confluence_permission space write operations (2293->2297)."""

    def test_page_write_operations(self):
        c = _mk_connector()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE
        assert c._map_confluence_permission("delete", "page") == PermissionType.WRITE
        assert c._map_confluence_permission("archive", "page") == PermissionType.WRITE


class TestTransformPageRestrictionAdditional:
    """Test _transform_page_restriction_to_permissions paths."""

    @pytest.mark.asyncio
    async def test_user_restriction_with_accountid(self):
        """User restriction using accountId key (line 2366->2363)."""
        c = _mk_connector()
        mock_tx = MagicMock()
        user = MagicMock()
        user.email = "user@x.com"
        mock_tx.get_user_by_source_id = AsyncMock(return_value=user)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        restriction_data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "acc-1"}]},
                "group": {"results": []},
            },
        }
        permissions = await c._transform_page_restriction_to_permissions(restriction_data)
        assert len(permissions) == 1
        assert permissions[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_group_restriction(self):
        """Group restriction (lines 2382->2380)."""
        c = _mk_connector()
        mock_tx = MagicMock()
        group = MagicMock()
        group.source_user_group_id = "grp-1"
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=group)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        restriction_data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": []},
                "group": {"results": [{"id": "grp-1"}]},
            },
        }
        permissions = await c._transform_page_restriction_to_permissions(restriction_data)
        assert len(permissions) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self):
        """Exception returns empty list (lines 2392-2393)."""
        c = _mk_connector()
        c.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("tx fail"))
        restriction_data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "acc-1"}]},
                "group": {"results": []},
            },
        }
        permissions = await c._transform_page_restriction_to_permissions(restriction_data)
        assert permissions == []


class TestFetchAttachmentContent:
    """Test _fetch_attachment_content (lines 3411-3439)."""

    @pytest.mark.asyncio
    async def test_no_attachment_id_raises_400(self):
        c = _mk_connector()
        record = MagicMock()
        record.external_record_id = None
        record.parent_external_record_id = "p1"
        record.id = "r1"
        gen = c._fetch_attachment_content(record)
        with pytest.raises(HTTPException) as exc_info:
            async for _ in gen:
                pass
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_parent_id_raises_400(self):
        c = _mk_connector()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = None
        record.id = "r1"
        gen = c._fetch_attachment_content(record)
        with pytest.raises(HTTPException) as exc_info:
            async for _ in gen:
                pass
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_streams_attachment_content(self):
        c = _mk_connector()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p-1"
        record.id = "r1"

        async def fake_download(**kwargs):
            yield b"chunk1"
            yield b"chunk2"

        ds = MagicMock()
        ds.download_attachment = fake_download
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        chunks = []
        async for chunk in c._fetch_attachment_content(record):
            chunks.append(chunk)
        assert chunks == [b"chunk1", b"chunk2"]

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        c = _mk_connector()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p-1"
        record.id = "r1"
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("download fail"))
        gen = c._fetch_attachment_content(record)
        with pytest.raises(HTTPException) as exc_info:
            async for _ in gen:
                pass
        assert exc_info.value.status_code == 500

class TestGetFreshDatasourceExtra:
    """Test _get_fresh_datasource paths (lines 382-416)."""

    @pytest.mark.asyncio
    async def test_no_external_client_raises(self):
        c = _mk_connector()
        c.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_api_token_auth_returns_datasource(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ) as mock_ds_cls:
            mock_ds_cls.return_value = MagicMock(name="ds")
            ds = await c._get_fresh_datasource()
        assert ds is not None
        mock_ds_cls.assert_called_once_with(c.external_client)

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="configuration not found"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_oauth_token_raises(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": ""}}
        )
        with pytest.raises(Exception, match="No OAuth access token"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_oauth_token_same_no_update(self):
        c = _mk_connector()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "token-123"
        mock_internal.set_token = MagicMock()
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = mock_internal
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": "token-123"}}
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ):
            await c._get_fresh_datasource()
        mock_internal.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_token_different_updates(self):
        c = _mk_connector()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_internal.set_token = MagicMock()
        c.external_client = MagicMock()
        c.external_client.get_client.return_value = mock_internal
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {"access_token": "new-token"}}
        )
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ConfluenceDataSource"
        ):
            await c._get_fresh_datasource()
        mock_internal.set_token.assert_called_once_with("new-token")


class TestTestConnectionAndAccess:
    """Test test_connection_and_access (lines 420-440)."""

    @pytest.mark.asyncio
    async def test_no_external_client_returns_false(self):
        c = _mk_connector()
        c.external_client = None
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_failed_response_returns_false(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_mk_resp(401))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("network fail"))
        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_connection_returns_true(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_mk_resp(200, {"results": [{"id": "sp1"}]}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c.test_connection_and_access()
        assert result is True


class TestStreamRecord:
    """Test stream_record (lines 3215-3262)."""

    @pytest.mark.asyncio
    async def test_stream_page(self):
        c = _mk_connector()
        record = MagicMock(
            record_type=RecordType.CONFLUENCE_PAGE,
            record_name="My Page",
            external_record_id="p1",
            id="rec-1",
            external_record_group_id="sp1",
            weburl="https://example.atlassian.net/wiki/spaces/TEST/pages/p1",
        )
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(
            return_value=_mk_resp(200, {"results": [], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_data_with_adf = AsyncMock(
            return_value={"id": "p1", "body": {"atlas_doc_format": {"value": "{}"}}}
        )
        c._process_page_attachments_for_children = AsyncMock(return_value={})
        c._parse_confluence_page_to_blocks = AsyncMock(
            return_value=MagicMock(model_dump_json=MagicMock(return_value="{}"))
        )
        result = await c.stream_record(record)
        assert result is not None  # StreamingResponse

    @pytest.mark.asyncio
    async def test_stream_comment_raises_404_when_not_found(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(
            record_type=RecordType.COMMENT,
            record_name="Comment1",
            external_record_id="321748993",
        )
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 404
        assert "321748993" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_stream_comment_returns_html(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(
            return_value=_mk_resp(
                200,
                {"body": {"storage": {"value": "<p>Footer comment</p>"}}},
            )
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(
            record_type=RecordType.COMMENT,
            record_name="Comment1",
            external_record_id="321748993",
        )
        result = await c.stream_record(record)
        assert result is not None
        assert result.media_type == MimeTypes.HTML.value

    @pytest.mark.asyncio
    async def test_stream_file(self):
        c = _mk_connector()
        record = MagicMock(
            record_type=RecordType.FILE,
            record_name="doc.pdf",
            external_record_id="att-1",
            mime_type="application/pdf",
            id="r1",
        )
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_400(self):
        c = _mk_connector()
        record = MagicMock(
            record_type=RecordType.MESSAGE,
            record_name="msg",
            external_record_id="m1",
        )
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        c = _mk_connector()
        record = MagicMock(
            record_type=RecordType.CONFLUENCE_PAGE,
            record_name="Page",
            external_record_id="p1",
        )
        c._fetch_page_content = AsyncMock(side_effect=RuntimeError("network fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 500


class TestFetchPageContent:
    """Test _fetch_page_content (lines 3284-3328)."""

    @pytest.mark.asyncio
    async def test_blogpost_type_calls_blogpost_api(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blogpost_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {"body": {"styled_view": {"value": "<h1>Blog</h1>"}}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert result == "<h1>Blog</h1>"

    @pytest.mark.asyncio
    async def test_no_body_returns_placeholder(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(
            return_value=_mk_resp(200, {"body": {"styled_view": {"value": ""}}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert result == "<p>No content available</p>"

    @pytest.mark.asyncio
    async def test_unsupported_record_type_raises_400(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("x1", RecordType.FILE)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_failed_response_raises_404(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 500


class TestGetFilterOptions:
    """Test get_filter_options (lines 3834-3841)."""

    @pytest.mark.asyncio
    async def test_unsupported_filter_key_raises(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        with pytest.raises(ValueError):
            await c.get_filter_options("unknown_key")


class TestGetSpaceOptions:
    """Test _get_space_options (lines 3856-3945)."""

    @pytest.mark.asyncio
    async def test_search_term_uses_cql(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_spaces_cql = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"space": {"key": "TS", "name": "Test Space"}}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_space_options(1, 20, "Test", None)
        assert len(result.options) == 1
        assert result.options[0].id == "TS"

    @pytest.mark.asyncio
    async def test_no_search_lists_spaces(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"key": "SP1", "name": "Space One"}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_space_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_failed_search_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_spaces_cql = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_space_options(1, 20, "Test", None)

    @pytest.mark.asyncio
    async def test_failed_list_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_space_options(1, 20, None, None)

    @pytest.mark.asyncio
    async def test_cursor_extraction_from_next_link(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"key": "SP1", "name": "Space One"}],
                "_links": {"next": "/wiki/api/v2/spaces?cursor=abc123&limit=20"}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "abc123"

    @pytest.mark.asyncio
    async def test_cursor_extraction_exception_is_silent(self):
        """Exception in cursor extraction is swallowed (line 3923-3924)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"key": "SP1", "name": "Space One"}],
                "_links": {"next": None},  # will trigger exception in parse
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        # Should not raise
        result = await c._get_space_options(1, 20, None, None)
        assert result.has_more is False


class TestGetPageOptions:
    """Test _get_page_options (lines 3965-4038)."""

    @pytest.mark.asyncio
    async def test_search_term_uses_cql(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_pages_cql = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"content": {"id": "p1", "title": "My Page", "type": "page"}}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_page_options(1, 20, "My Page", None)
        assert len(result.options) == 1
        assert result.options[0].id == "p1"

    @pytest.mark.asyncio
    async def test_no_search_lists_pages(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_pages = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"id": "p2", "title": "Page Two"}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_page_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_failed_search_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_pages_cql = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_page_options(1, 20, "foo", None)

    @pytest.mark.asyncio
    async def test_failed_list_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_pages = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_page_options(1, 20, None, None)

    @pytest.mark.asyncio
    async def test_cursor_extraction_from_next_link(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_pages = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"id": "p3", "title": "Page Three"}],
                "_links": {"next": "/wiki/api/v2/pages?cursor=page-cursor&limit=20"}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_page_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "page-cursor"

    @pytest.mark.asyncio
    async def test_content_with_wrong_type_not_included(self):
        """CQL results with non-page type are excluded (line 3983->3981)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_pages_cql = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [
                    {"content": {"id": "b1", "title": "Blog", "type": "blogpost"}},
                    {"content": {"id": "p1", "title": "Page", "type": "page"}},
                ],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_page_options(1, 20, "search", None)
        assert len(result.options) == 1
        assert result.options[0].id == "p1"


class TestGetBlogpostOptions:
    """Test _get_blogpost_options (lines 4058-4131)."""

    @pytest.mark.asyncio
    async def test_search_term_uses_cql(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_blogposts_cql = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"content": {"id": "b1", "title": "My Blog", "type": "blogpost"}}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_blogpost_options(1, 20, "My Blog", None)
        assert len(result.options) == 1
        assert result.options[0].id == "b1"

    @pytest.mark.asyncio
    async def test_no_search_lists_blogposts(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_posts = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"id": "b2", "title": "Blog Two"}],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_failed_search_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.search_blogposts_cql = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_blogpost_options(1, 20, "foo", None)

    @pytest.mark.asyncio
    async def test_failed_list_raises_runtime_error(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_posts = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(RuntimeError):
            await c._get_blogpost_options(1, 20, None, None)

    @pytest.mark.asyncio
    async def test_cursor_extraction_from_next_link(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blog_posts = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [{"id": "b3", "title": "Blog Three"}],
                "_links": {"next": "/wiki/api/v2/blogposts?cursor=blog-cursor&limit=20"}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "blog-cursor"

    @pytest.mark.asyncio
    async def test_content_with_wrong_type_not_included(self):
        """CQL results with non-blogpost type are excluded (line 4076->4074)."""
        c = _mk_connector()
        ds = MagicMock()
        ds.search_blogposts_cql = AsyncMock(
            return_value=_mk_resp(200, {
                "results": [
                    {"content": {"id": "p1", "title": "Page", "type": "page"}},
                    {"content": {"id": "b1", "title": "Blog", "type": "blogpost"}},
                ],
                "_links": {}
            })
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._get_blogpost_options(1, 20, "search", None)
        assert len(result.options) == 1
        assert result.options[0].id == "b1"


class TestCheckAndFetchUpdatedAttachmentAdditional:
    """Test _check_and_fetch_updated_attachment additional paths (lines 3769-3801)."""

    @pytest.mark.asyncio
    async def test_no_parent_id_returns_none(self):
        c = _mk_connector()
        record = MagicMock(
            external_record_id="att-5",
            parent_external_record_id=None,
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_not_found_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(
            external_record_id="att-6",
            parent_external_record_id="p-6",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(200, {"id": "att-7", "version": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(
            external_record_id="att-7",
            parent_external_record_id="p-7",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "att-8", "version": {"number": 3}, "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(
            external_record_id="att-8",
            parent_external_record_id="p-8",
            external_revision_id="3",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_transform_none_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(
            return_value=_mk_resp(200, {"id": "att-9", "version": {"number": 5}, "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c._transform_to_attachment_file_record = MagicMock(return_value=None)
        record = MagicMock(
            external_record_id="att-9",
            parent_external_record_id="p-9",
            external_revision_id="2",
            external_record_group_id="sp1",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock(
            external_record_id="att-10",
            parent_external_record_id="p-10",
        )
        result = await c._check_and_fetch_updated_attachment("org-1", record)
        assert result is None


class TestInitAdditional:
    """Test init() paths (lines 347-365)."""

    @pytest.mark.asyncio
    async def test_init_exception_returns_false(self):
        c = _mk_connector()
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient.build_from_services",
            side_effect=RuntimeError("build fail"),
        ):
            result = await c.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_success_returns_true(self):
        c = _mk_connector()
        mock_client = MagicMock()
        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.ExternalConfluenceClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            result = await c.init()
        assert result is True


class TestReindexRecordsAdditional:
    """Test reindex_records paths (lines 3459-3505)."""

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self):
        c = _mk_connector()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_empty_records_returns(self):
        c = _mk_connector()
        await c.reindex_records([])
        c.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_with_exception_continues(self):
        c = _mk_connector()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        record1 = MagicMock(id="r1")
        record2 = MagicMock(id="r2")
        c._check_and_fetch_updated_record = AsyncMock(
            side_effect=[RuntimeError("fail"), None]
        )
        c.data_entities_processor.reindex_existing_records = AsyncMock()
        await c.reindex_records([record1, record2])
        # record2 returned None → goes to non_updated → reindex
        c.data_entities_processor.reindex_existing_records.assert_called_once()


class TestSyncContentCommentIndexingOff:
    """Cloud sync embeds comments at stream time; PAGE_COMMENTS filter does not create CommentRecords."""

    @pytest.mark.asyncio
    async def test_sync_completes_without_comment_records(self):
        c = _mk_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()

        def mock_is_enabled(key):
            if key == IndexingFilterKey.PAGES:
                return True
            if key == IndexingFilterKey.PAGE_COMMENTS:
                return False
            return True

        c.indexing_filters.is_enabled = MagicMock(side_effect=mock_is_enabled)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "p1", "title": "Page1", "version": {"number": 1}, "_links": {},
            "space": {"id": "sp1", "key": "SP1"},
            "childTypes": {"comment": {"value": True}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [page_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_record = MagicMock(id="rec-1")
        c._process_webpage_with_update = AsyncMock(return_value=MagicMock(record=mock_record))

        await c._sync_content("SP1", RecordType.CONFLUENCE_PAGE)
        c.data_entities_processor.on_new_records.assert_called_once()


class TestSyncContentPageIDFilter:
    """Test _sync_content when page_ids filter is set (lines 1018-1019)."""

    @pytest.mark.asyncio
    async def test_page_ids_filter_logged(self):
        c = _mk_connector()
        page_ids_filter = MagicMock()
        page_ids_filter.get_values.return_value = ["p1", "p2"]
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(
            side_effect=lambda key: page_ids_filter if key == SyncFilterKey.PAGE_IDS else None
        )
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        c.pages_sync_point = MagicMock()
        c.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_mk_resp(200, {"results": [], "_links": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)
        call_kwargs = ds.get_pages_v1.call_args[1]
        # page_ids filter should pass page_ids to the API
        assert "page_ids" in call_kwargs


class TestSyncSpacesNotInFilter:
    """Test _sync_spaces with NOT_IN space filter (line 705->710)."""

    @pytest.mark.asyncio
    async def test_not_in_filter_excludes_spaces(self):
        c = _mk_connector()

        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.NOT_IN
        space_filter.get_value.return_value = ["EXCLUDED"]

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(
            side_effect=lambda key: space_filter if key == SyncFilterKey.SPACE_KEYS else None
        )
        c.indexing_filters = MagicMock()

        space1 = {"id": "sp1", "key": "INCLUDED", "name": "Space 1", "_links": {}}
        space2 = {"id": "sp2", "key": "EXCLUDED", "name": "Excluded Space", "_links": {}}

        ds = MagicMock()
        ds.get_spaces = AsyncMock(
            return_value=_mk_resp(200, {"results": [space1, space2], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=MagicMock())

        await c._sync_spaces()
        # Only space1 should be processed (space2 is excluded)
        assert c._transform_to_space_record_group.call_count == 1


class TestExtractMediaFromAdf:
    def test_invalid_input_returns_empty(self):
        assert extract_media_from_adf(None) == []
        assert extract_media_from_adf({}) == []
        assert extract_media_from_adf("not-a-dict") == []

    def test_extracts_media_with_all_attrs(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "media",
                    "attrs": {
                        "id": "media-uuid-1",
                        "alt": "photo.png",
                        "__fileName": "report.pdf",
                        "type": "file",
                        "width": 100,
                        "height": 200,
                        "collection": "contentId-123",
                    },
                }
            ],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "media-uuid-1"
        assert result[0]["filename"] == "report.pdf"
        assert result[0]["alt"] == "photo.png"
        assert result[0]["width"] == 100
        assert result[0]["height"] == 200
        assert result[0]["collection"] == "contentId-123"

    def test_prefers_filename_over_alt(self):
        adf = {
            "content": [
                {"type": "media", "attrs": {"id": "m1", "alt": "image.jpg", "__fileName": "doc.pdf"}},
            ]
        }
        result = extract_media_from_adf(adf)
        assert result[0]["filename"] == "doc.pdf"

    def test_skips_media_without_id(self):
        adf = {"content": [{"type": "media", "attrs": {"alt": "orphan.png"}}]}
        assert extract_media_from_adf(adf) == []

    def test_nested_content_traversal(self):
        adf = {
            "content": [
                {
                    "type": "panel",
                    "content": [
                        {"type": "media", "attrs": {"id": "nested-1", "alt": "nested.png"}},
                    ],
                }
            ],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "nested-1"

    def test_direct_node_without_content_key(self):
        adf = {"type": "media", "attrs": {"id": "direct-1", "alt": "direct.png"}}
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "direct-1"

    def test_multiple_media_nodes(self):
        adf = {
            "content": [
                {"type": "media", "attrs": {"id": "a", "alt": "a.png"}},
                {"type": "media", "attrs": {"id": "b", "alt": "b.png"}},
            ]
        }
        assert len(extract_media_from_adf(adf)) == 2

    def test_non_dict_node_in_content_skipped(self):
        adf = {
            "content": [
                "bad-node",
                {"type": "media", "attrs": {"id": "ok-1", "alt": "ok.png"}},
            ],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "ok-1"


class TestLinkPlatformUsersViaJiraFullFlow:
    @pytest.mark.asyncio
    async def test_no_unlinked_users_early_return(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN", "email": "a@b.com", "apiToken": "tok"}}
        )
        c.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        mock_ds.find_user_account_id_by_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_invalid_and_already_linked_emails(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        active = [
            User(email="no-at-sign", org_id="org-1"),
            User(email="  ", org_id="org-1"),
            User(email="linked@corp.com", org_id="org-1", full_name="Linked"),
            User(email="new@corp.com", org_id="org-1", full_name="New User"),
        ]
        linked = [AppUser(
            app_name=Connectors.CONFLUENCE,
            connector_id="conn-new-1",
            source_user_id="acc-linked",
            org_id="org-1",
            email="linked@corp.com",
            full_name="Linked",
            is_active=False,
        )]
        c.data_entities_processor.get_all_active_users = AsyncMock(return_value=active)
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=linked)

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(return_value="acc-new")
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        mock_ds.find_user_account_id_by_email.assert_awaited_once_with("new@corp.com")
        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        c.data_entities_processor.migrate_group_to_user_by_external_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_not_found_in_jira(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email="missing@corp.com", org_id="org-1")]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_rate_limit_api_error_continues(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email="fail@corp.com", org_id="org-1")]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(side_effect=RuntimeError("network blip"))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rate_limit_stops_processing(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        users = [User(email=f"u{i}@corp.com", org_id="org-1") for i in range(3)]
        c.data_entities_processor.get_all_active_users = AsyncMock(return_value=users)
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(side_effect=Exception("HTTP 429 rate limit"))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_save_failure(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email="save-fail@corp.com", org_id="org-1")]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        c.data_entities_processor.on_new_app_users = AsyncMock(side_effect=RuntimeError("db fail"))

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(return_value="acc-1")
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        c.data_entities_processor.migrate_group_to_user_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_migrate_failure_logs_but_links(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email="migrate-fail@corp.com", org_id="org-1")]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock(
            side_effect=RuntimeError("migrate fail")
        )

        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(return_value="acc-migrate")
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oauth_without_jira_scope_skips(self):
        c = _mk_connector()
        c.config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH", "includeJiraScope": "no"}}
        )
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await c._link_platform_users_via_jira()

        mock_ds.find_user_account_id_by_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_gather_exception_breaks(self):
        import asyncio

        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email=f"u{i}@corp.com", org_id="org-1") for i in range(3)]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        mock_ds = MagicMock()
        mock_ds.find_user_account_id_by_email = AsyncMock(return_value="acc-1")
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(asyncio, "gather", side_effect=RuntimeError("gather fail")):
            await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_results_loop_rate_limit(self):
        import asyncio

        c = _mk_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {"authType": "API_TOKEN"}})
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[User(email="u@corp.com", org_id="org-1")]
        )
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())

        with patch.object(asyncio, "gather", new_callable=AsyncMock, return_value=[Exception("429")]):
            await c._link_platform_users_via_jira()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()


class TestResolveConfluenceAttachmentId:
    def test_match_by_file_id(self):
        c = _mk_connector()
        media = {"id": "file-uuid", "filename": "x.png"}
        attachments = [{"id": "att-1", "fileId": "file-uuid", "title": "other.png"}]
        assert c._resolve_confluence_attachment_id(media, attachments) == "att-1"

    def test_match_by_exact_title(self):
        c = _mk_connector()
        media = {"filename": "report.pdf"}
        attachments = [{"id": "att-2", "title": "report.pdf"}]
        assert c._resolve_confluence_attachment_id(media, attachments) == "att-2"

    def test_match_by_case_insensitive_title(self):
        c = _mk_connector()
        media = {"alt": "IMAGE.PNG"}
        attachments = [{"id": "att-3", "title": "image.png"}]
        assert c._resolve_confluence_attachment_id(media, attachments) == "att-3"

    def test_no_match_returns_none(self):
        c = _mk_connector()
        assert c._resolve_confluence_attachment_id({"id": "x"}, []) is None
        assert c._resolve_confluence_attachment_id({}, [{"id": "1", "title": "a"}]) is None


class TestFetchConfluenceMediaAsBase64:
    @pytest.mark.asyncio
    async def test_page_image_success(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [{"id": "att-1", "title": "logo.png", "mediaType": "image/png"}],
        }))

        async def _download(**_kwargs):
            yield b"\x89PNG"

        ds.download_attachment = _download
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c._fetch_confluence_media_as_base64("100", "file-id", "logo.png", "page")
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        decoded = base64.b64decode(result.split(",", 1)[1])
        assert decoded == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_blogpost_attachments_api(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blogpost_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [{"id": "att-b", "title": "chart.png", "mediaType": "image/png"}],
        }))

        async def _download(**_kwargs):
            yield b"img"

        ds.download_attachment = _download
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c._fetch_confluence_media_as_base64("200", "fid", "chart.png", "blogpost")
        assert result is not None
        ds.get_blogpost_attachments.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_success_response(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(404, {}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        assert await c._fetch_confluence_media_as_base64("1", "m", "x.png") is None

    @pytest.mark.asyncio
    async def test_no_matching_attachment(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        assert await c._fetch_confluence_media_as_base64("1", "m", "missing.png") is None

    @pytest.mark.asyncio
    async def test_non_image_mime_skipped(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [{"id": "att-pdf", "title": "doc.pdf", "mediaType": "application/pdf"}],
        }))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        assert await c._fetch_confluence_media_as_base64("1", "m", "doc.pdf") is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))
        assert await c._fetch_confluence_media_as_base64("1", "m", "x.png") is None

    @pytest.mark.asyncio
    async def test_media_fetcher_delegates(self):
        c = _mk_connector()
        attachments = [{"id": "att-1", "title": "alt.png", "mediaType": "image/png"}]
        c._fetch_page_attachments_list = AsyncMock(return_value=attachments)
        c._fetch_confluence_media_as_base64 = AsyncMock(return_value="data:image/png;base64,abc")
        fetcher = c._create_confluence_media_fetcher("page-1", "page")
        result = await fetcher("media-id", "alt.png")
        assert result == "data:image/png;base64,abc"
        c._fetch_page_attachments_list.assert_awaited_once()
        c._fetch_confluence_media_as_base64.assert_awaited_once_with(
            "page-1", "media-id", "alt.png", "page", attachments=attachments
        )

    @pytest.mark.asyncio
    async def test_media_fetcher_uses_preloaded_attachments(self):
        c = _mk_connector()
        attachments = [{"id": "att-1", "title": "alt.png"}]
        c._fetch_page_attachments_list = AsyncMock()
        c._fetch_confluence_media_as_base64 = AsyncMock(return_value=None)
        fetcher = c._create_confluence_media_fetcher(
            "page-1", "page", attachments_data=attachments
        )
        await fetcher("media-id", "alt.png")
        c._fetch_page_attachments_list.assert_not_awaited()
        c._fetch_confluence_media_as_base64.assert_awaited_once_with(
            "page-1", "media-id", "alt.png", "page", attachments=attachments
        )

    @pytest.mark.asyncio
    async def test_comment_media_fetcher_delegates(self):
        c = _mk_connector()
        attachments = [{"id": "att-b", "title": "file.png"}]
        c._fetch_page_attachments_list = AsyncMock(return_value=attachments)
        c._fetch_confluence_media_as_base64 = AsyncMock(return_value="data:image/png;base64,xyz")
        fetcher = c._create_comment_media_fetcher("page-2", "blogpost")
        result = await fetcher("mid", "file.png")
        assert result == "data:image/png;base64,xyz"
        c._fetch_confluence_media_as_base64.assert_awaited_once_with(
            "page-2", "mid", "file.png", "blogpost", attachments=attachments
        )

    @pytest.mark.asyncio
    async def test_page_image_resolves_by_file_id(self):
        c = _mk_connector()
        ds = MagicMock()
        file_id = "adb61214-e78f-409e-935f-d6e1fdfb059d"
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [{
                "id": "att-1",
                "title": "different-name.png",
                "fileId": file_id,
                "mediaType": "image/png",
            }],
        }))

        async def _download(**_kwargs):
            yield b"\x89PNG"

        ds.download_attachment = _download
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c._fetch_confluence_media_as_base64(
            "100", file_id, "wrong-filename.png", "page"
        )
        assert result is not None
        assert result.startswith("data:image/png;base64,")


class TestCloudHtmlAttachmentHelpers:
    def test_rest_attachment_id_from_linked_resource(self):
        assert ConfluenceConnector._rest_attachment_id_from_linked_resource("128221190") == "att128221190"
        assert ConfluenceConnector._rest_attachment_id_from_linked_resource("att999") == "att999"
        assert ConfluenceConnector._rest_attachment_id_from_linked_resource("") == ""
        assert ConfluenceConnector._rest_attachment_id_from_linked_resource("  ") == ""

    def test_find_attachment_in_list_by_file_id_and_filename(self):
        c = _mk_connector()
        attachments = [
            {"id": "att-1", "title": "photo.png", "fileId": "uuid-1"},
            {"id": "att-2", "title": "Chart.PNG", "fileId": "uuid-2"},
        ]
        assert c._find_attachment_in_list(attachments, file_id="uuid-2") == attachments[1]
        assert c._find_attachment_in_list(attachments, filename="photo.png") == attachments[0]
        assert c._find_attachment_in_list(attachments, filename="chart.png") == attachments[1]
        assert c._find_attachment_in_list(attachments, filename="missing.png") is None

    @pytest.mark.asyncio
    async def test_get_attachment_metadata_by_id_caches_negative_lookup(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(404, {}))
        meta_cache: dict[str, dict[str, Any] | None] = {}

        assert await c._get_attachment_metadata_by_id(ds, "att-missing", meta_cache) is None
        assert await c._get_attachment_metadata_by_id(ds, "att-missing", meta_cache) is None
        ds.get_attachment_by_id.assert_awaited_once_with(id="att-missing")
        assert meta_cache == {"att-missing": None}

    @pytest.mark.asyncio
    async def test_cloud_html_image_downloader_dedupes_failed_metadata_lookup(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(404, {}))
        download = c._create_cloud_html_image_downloader(
            ds, "100", RecordType.CONFLUENCE_PAGE
        )
        url = "https://site.atlassian.net/wiki/download/thumbnails/100/photo.png"
        image_context = HtmlImageContext(
            linked_resource_id="999",
            linked_resource_type="attachment",
        )
        assert await download(url, image_context) is None
        assert await download(url, image_context) is None
        ds.get_attachment_by_id.assert_awaited_once_with(id="att999")

    @pytest.mark.asyncio
    async def test_cloud_html_image_downloader_linked_resource(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(200, {
            "id": "att128221190",
            "mediaType": "image/png",
            "title": "photo.png",
        }))
        download_calls: list[dict[str, Any]] = []

        async def _download(**kwargs):
            download_calls.append(kwargs)
            yield b"\x89PNG"

        ds.download_attachment = _download
        download = c._create_cloud_html_image_downloader(
            ds, "358416386", RecordType.CONFLUENCE_PAGE
        )
        url = (
            "https://site.atlassian.net/wiki/download/thumbnails/358416386/"
            "photo.png?version=1"
        )
        image_context = HtmlImageContext(
            alt_text="photo.png",
            linked_resource_id="128221190",
            linked_resource_type="attachment",
        )
        result = await download(url, image_context)
        assert result == (b"\x89PNG", "image/png")
        ds.get_attachment_by_id.assert_awaited_once_with(id="att128221190")
        assert len(download_calls) == 1
        assert download_calls[0]["parent_page_id"] == "358416386"
        assert download_calls[0]["attachment_id"] == "att128221190"

    @pytest.mark.asyncio
    async def test_cloud_html_image_downloader_skips_non_image(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_attachment_by_id = AsyncMock(return_value=_mk_resp(200, {
            "id": "att1",
            "mediaType": "application/pdf",
            "title": "doc.pdf",
        }))
        download_calls: list[dict[str, Any]] = []

        async def _download(**kwargs):
            download_calls.append(kwargs)
            yield b"pdf"

        ds.download_attachment = _download
        download = c._create_cloud_html_image_downloader(
            ds, "100", RecordType.CONFLUENCE_PAGE
        )
        url = "https://site.atlassian.net/wiki/download/attachments/100/doc.pdf"
        image_context = HtmlImageContext(
            linked_resource_id="1",
            linked_resource_type="attachment",
        )
        assert await download(url, image_context) is None
        assert download_calls == []


class TestOrganizeConfluenceCommentsToThreads:
    def test_empty_returns_empty(self):
        c = _mk_connector()
        assert c._organize_confluence_comments_to_threads([]) == []

    def test_top_level_comments_form_threads(self):
        c = _mk_connector()
        comments = [
            {"id": "c2", "version": {"createdAt": "2025-02-01T00:00:00.000Z"}},
            {"id": "c1", "version": {"createdAt": "2025-01-01T00:00:00.000Z"}},
        ]
        threads = c._organize_confluence_comments_to_threads(comments)
        assert len(threads) == 2
        assert threads[0][0]["id"] == "c1"

    def test_replies_grouped_under_parent(self):
        c = _mk_connector()
        comments = [
            {"id": "parent", "version": {"createdAt": "2025-01-01T00:00:00.000Z"}},
            {
                "id": "reply",
                "parentCommentId": "parent",
                "version": {"createdAt": "2025-01-02T00:00:00.000Z"},
            },
        ]
        threads = c._organize_confluence_comments_to_threads(comments)
        assert len(threads) == 1
        assert len(threads[0]) == 2
        assert threads[0][0]["id"] == "parent"


class TestFetchPageDataWithAdf:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(return_value=_mk_resp(200, {"id": "p1", "body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        data = await c._fetch_page_data_with_adf("p1", RecordType.CONFLUENCE_PAGE)
        assert data["id"] == "p1"

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_blogpost_content_v2 = AsyncMock(return_value=_mk_resp(200, {"id": "b1"}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        data = await c._fetch_page_data_with_adf("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert data["id"] == "b1"

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        with pytest.raises(ValueError, match="Unsupported record type"):
            await c._fetch_page_data_with_adf("x", RecordType.TICKET)

    @pytest.mark.asyncio
    async def test_not_found_raises_http_exception(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_page_content_v2 = AsyncMock(return_value=_mk_resp(404, {}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        with pytest.raises(HTTPException) as exc:
            await c._fetch_page_data_with_adf("p-missing", RecordType.CONFLUENCE_PAGE)
        assert exc.value.status_code == 404


class TestProcessPageAttachmentsForChildren:
    @pytest.mark.asyncio
    async def test_maps_existing_attachment(self):
        c = _mk_connector()
        existing = MagicMock()
        existing.id = "rec-att-1"
        existing.record_name = "file.pdf"
        mock_tx = c.data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        result = await c._process_page_attachments_for_children(
            [{"id": "att-1", "title": "file.pdf", "_links": {}}],
            "page-1",
            "node-1",
            "space-1",
            None,
        )
        assert "att-1" in result
        assert result["att-1"].child_id == "rec-att-1"
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_file_record(self):
        c = _mk_connector()
        mock_tx = c.data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        attachments = [{
            "id": "att-new",
            "title": "new.docx",
            "mediaType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "_links": {},
        }]
        result = await c._process_page_attachments_for_children(
            attachments, "page-1", "node-1", "space-1", None,
        )
        assert "att-new" in result
        c.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_attachment_without_id(self):
        c = _mk_connector()
        result = await c._process_page_attachments_for_children(
            [{"title": "no-id.pdf"}], "page-1", "node-1", "space-1", None,
        )
        assert result == {}


class TestFetchAttachmentContentStreaming:
    @pytest.mark.asyncio
    async def test_streams_chunks(self):
        c = _mk_connector()
        ds = MagicMock()

        async def _download(**_kwargs):
            yield b"chunk1"
            yield b"chunk2"

        ds.download_attachment = _download
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "page-1"
        record.id = "rec-1"

        data = await _collect_bytes(c._fetch_attachment_content(record))
        assert data == b"chunk1chunk2"

    @pytest.mark.asyncio
    async def test_missing_attachment_id_raises_400(self):
        c = _mk_connector()
        record = MagicMock(external_record_id=None, parent_external_record_id="p1", id="r1")
        with pytest.raises(HTTPException) as exc:
            await _collect_bytes(c._fetch_attachment_content(record))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_parent_raises_400(self):
        c = _mk_connector()
        record = MagicMock(external_record_id="att-1", parent_external_record_id=None, id="r1")
        with pytest.raises(HTTPException) as exc:
            await _collect_bytes(c._fetch_attachment_content(record))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_generic_error_raises_500(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("download fail"))
        record = MagicMock(external_record_id="att-1", parent_external_record_id="p1", id="r1")
        with pytest.raises(HTTPException) as exc:
            await _collect_bytes(c._fetch_attachment_content(record))
        assert exc.value.status_code == 500


class TestStreamRecordLegacyHtml:
    @pytest.mark.asyncio
    async def test_legacy_html_mime_streams_html(self):
        c = _mk_connector()
        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.mime_type = MimeTypes.HTML.value
        record.external_record_id = "legacy-1"
        record.record_name = "Legacy Page"
        c._fetch_page_content = AsyncMock(return_value="<p>Legacy</p>")

        response = await c.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert b"<p>Legacy</p>" in body


class TestFetchCommentData:
    @pytest.mark.asyncio
    async def test_footer_comment_success(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(return_value=_mk_resp(200, {"id": "c1"}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="101", record_type=RecordType.COMMENT)
        data = await c._fetch_comment_data(record)
        assert data["id"] == "c1"

    @pytest.mark.asyncio
    async def test_inline_comment_success(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_inline_comment_by_id = AsyncMock(return_value=_mk_resp(200, {"id": "ic1"}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="202", record_type=RecordType.INLINE_COMMENT)
        data = await c._fetch_comment_data(record)
        assert data["id"] == "ic1"

    @pytest.mark.asyncio
    async def test_unsupported_type_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        record = MagicMock(external_record_id="1", record_type=RecordType.CONFLUENCE_PAGE)
        assert await c._fetch_comment_data(record) is None

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_footer_comment_by_id = AsyncMock(return_value=_mk_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        record = MagicMock(external_record_id="999", record_type=RecordType.COMMENT)
        assert await c._fetch_comment_data(record) is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("ds fail"))
        record = MagicMock(external_record_id="101", record_type=RecordType.COMMENT)
        assert await c._fetch_comment_data(record) is None


class TestBatchFetchUserDisplayNames:
    @pytest.mark.asyncio
    async def test_empty_set(self):
        c = _mk_connector()
        assert await c._batch_fetch_user_display_names(set()) == {}

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_user_v1 = AsyncMock(return_value=_mk_resp(200, {"displayName": "Alice"}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._batch_fetch_user_display_names({"acc-1"})
        assert result["acc-1"] == "Alice"

    @pytest.mark.asyncio
    async def test_exception_maps_unknown(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_user_v1 = AsyncMock(side_effect=RuntimeError("fail"))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._batch_fetch_user_display_names({"acc-err"})
        assert result["acc-err"] == "Unknown"

    @pytest.mark.asyncio
    async def test_http_error_maps_unknown(self):
        c = _mk_connector()
        ds = MagicMock()
        ds.get_user_v1 = AsyncMock(return_value=_mk_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._batch_fetch_user_display_names({"acc-500"})
        assert result["acc-500"] == "Unknown"

    @pytest.mark.asyncio
    async def test_json_parse_failure_maps_unknown(self):
        c = _mk_connector()
        bad_resp = _mk_resp(200, {})
        bad_resp.json = MagicMock(side_effect=ValueError("bad json"))
        ds = MagicMock()
        ds.get_user_v1 = AsyncMock(return_value=bad_resp)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        result = await c._batch_fetch_user_display_names({"acc-json"})
        assert result["acc-json"] == "Unknown"


class TestTransformToCommentRecord:
    def _comment_data(self, **overrides):
        data = {
            "id": "comment-1",
            "title": "A comment",
            "version": {
                "authorId": "author-1",
                "createdAt": "2025-01-01T00:00:00.000Z",
                "number": 1,
            },
            "_links": {"webui": "/comments/1"},
        }
        data.update(overrides)
        return data

    def test_footer_comment(self):
        c = _mk_connector()
        rec = c._transform_to_comment_record(
            self._comment_data(), "page-1", "space-1", "footer", None,
            base_url="https://wiki.example.com",
        )
        assert rec is not None
        assert rec.record_type == RecordType.COMMENT
        assert rec.parent_external_record_id == "page-1"
        assert rec.parent_record_type == RecordType.WEBPAGE
        assert rec.author_source_id == "author-1"

    def test_inline_comment_resolution_open(self):
        c = _mk_connector()
        data = self._comment_data(resolutionStatus=False)
        rec = c._transform_to_comment_record(data, "page-1", "space-1", "inline", None)
        assert rec.record_type == RecordType.INLINE_COMMENT
        assert rec.resolution_status == "open"

    def test_inline_comment_resolution_resolved(self):
        c = _mk_connector()
        data = self._comment_data(
            resolutionStatus=True,
            properties={"inlineOriginalSelection": "selected text"},
        )
        rec = c._transform_to_comment_record(data, "page-1", "space-1", "inline", None)
        assert rec.resolution_status == "resolved"
        assert rec.comment_selection == "selected text"

    def test_nested_comment_uses_parent(self):
        c = _mk_connector()
        rec = c._transform_to_comment_record(
            self._comment_data(), "page-1", "space-1", "footer", "parent-comment",
        )
        assert rec.parent_external_record_id == "parent-comment"
        assert rec.parent_record_type == RecordType.COMMENT

    def test_missing_id_returns_none(self):
        c = _mk_connector()
        assert c._transform_to_comment_record({}, "p", "s", "footer", None) is None

    def test_missing_author_returns_none(self):
        c = _mk_connector()
        data = self._comment_data()
        data["version"] = {"number": 1}
        assert c._transform_to_comment_record(data, "p", "s", "footer", None) is None

    def test_existing_record_version_bump(self):
        c = _mk_connector()
        existing = MagicMock()
        existing.id = "existing-c1"
        existing.version = 2
        existing.external_revision_id = "1"
        data = self._comment_data()
        data["version"]["number"] = 3
        rec = c._transform_to_comment_record(
            data, "page-1", "space-1", "footer", None, existing_record=existing,
        )
        assert rec.id == "existing-c1"
        assert rec.version == 3

    def test_existing_record_same_version(self):
        c = _mk_connector()
        existing = MagicMock()
        existing.id = "existing-c2"
        existing.version = 2
        existing.external_revision_id = "2"
        data = self._comment_data()
        data["version"]["number"] = 2
        rec = c._transform_to_comment_record(
            data, "page-1", "space-1", "footer", None, existing_record=existing,
        )
        assert rec.version == 2

    def test_exception_returns_none(self):
        c = _mk_connector()
        with patch.object(c, "_parse_confluence_datetime", side_effect=Exception("bad date")):
            data = self._comment_data()
            assert c._transform_to_comment_record(data, "p", "s", "footer", None) is None


class TestCheckAndFetchUpdatedComment:
    @pytest.mark.asyncio
    async def test_not_found_returns_none(self):
        c = _mk_connector()
        c._fetch_comment_data = AsyncMock(return_value=None)
        record = MagicMock(external_record_id="c1")
        assert await c._check_and_fetch_updated_comment("org-1", record) is None

    @pytest.mark.asyncio
    async def test_no_version_returns_none(self):
        c = _mk_connector()
        c._fetch_comment_data = AsyncMock(return_value={"id": "c1", "version": {}})
        record = MagicMock(external_record_id="c1")
        assert await c._check_and_fetch_updated_comment("org-1", record) is None

    @pytest.mark.asyncio
    async def test_version_unchanged_returns_none(self):
        c = _mk_connector()
        c._fetch_comment_data = AsyncMock(return_value={"id": "c1", "version": {"number": 2}})
        record = MagicMock(external_record_id="c1", external_revision_id="2")
        assert await c._check_and_fetch_updated_comment("org-1", record) is None

    @pytest.mark.asyncio
    async def test_version_changed_returns_record_and_permissions(self):
        c = _mk_connector()
        comment_data = {
            "id": "c1",
            "title": "Updated",
            "version": {"authorId": "a1", "number": 3, "createdAt": "2025-01-01T00:00:00.000Z"},
            "_links": {},
        }
        c._fetch_comment_data = AsyncMock(return_value=comment_data)
        c._fetch_page_permissions = AsyncMock(return_value=[
            Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"),
        ])

        record = MagicMock(
            external_record_id="c1",
            external_revision_id="2",
            record_type=RecordType.COMMENT,
            parent_external_record_id="page-1",
            external_record_group_id="space-1",
            parent_node_id="node-1",
            id="rec-c1",
            version=1,
        )
        result = await c._check_and_fetch_updated_comment("org-1", record)
        assert result is not None
        updated, perms = result
        assert updated.external_revision_id == "3"
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_permission_fetch_failure_still_returns_record(self):
        c = _mk_connector()
        comment_data = {
            "id": "c2",
            "title": "Comment",
            "version": {"authorId": "a1", "number": 2, "createdAt": "2025-01-01T00:00:00.000Z"},
            "_links": {},
        }
        c._fetch_comment_data = AsyncMock(return_value=comment_data)
        c._fetch_page_permissions = AsyncMock(side_effect=RuntimeError("perm fail"))

        record = MagicMock(
            external_record_id="c2",
            external_revision_id="1",
            record_type=RecordType.COMMENT,
            parent_external_record_id="page-1",
            external_record_group_id="space-1",
            parent_node_id="node-1",
            id="rec-c2",
            version=0,
        )
        result = await c._check_and_fetch_updated_comment("org-1", record)
        assert result is not None
        assert result[1] == []

    @pytest.mark.asyncio
    async def test_transform_none_returns_none(self):
        c = _mk_connector()
        comment_data = {
            "id": "c3",
            "title": "Bad",
            "version": {"authorId": "a1", "number": 2, "createdAt": "2025-01-01T00:00:00.000Z"},
            "_links": {},
        }
        c._fetch_comment_data = AsyncMock(return_value=comment_data)
        c._transform_to_comment_record = MagicMock(return_value=None)
        record = MagicMock(
            external_record_id="c3",
            external_revision_id="1",
            record_type=RecordType.COMMENT,
        )
        assert await c._check_and_fetch_updated_comment("org-1", record) is None

    @pytest.mark.asyncio
    async def test_outer_exception_returns_none(self):
        c = _mk_connector()
        c._fetch_comment_data = AsyncMock(side_effect=RuntimeError("fetch boom"))
        record = MagicMock(external_record_id="c4", record_type=RecordType.COMMENT)
        assert await c._check_and_fetch_updated_comment("org-1", record) is None

    @pytest.mark.asyncio
    async def test_dispatches_comment_types(self):
        c = _mk_connector()
        c._check_and_fetch_updated_comment = AsyncMock(return_value=None)

        for rtype in (RecordType.COMMENT, RecordType.INLINE_COMMENT):
            record = MagicMock(record_type=rtype)
            await c._check_and_fetch_updated_record("org-1", record)
        assert c._check_and_fetch_updated_comment.await_count == 2


class TestFixLegacyMimeTypes:
    """Tests for deprecated _fix_legacy_mime_types method (no longer called in production)."""
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Method deprecated - tests backward compatibility only")
    async def test_updates_html_page_to_blocks(self):
        c = _mk_connector()
        page = MagicMock()
        page.record_type = RecordType.CONFLUENCE_PAGE
        page.mime_type = MimeTypes.HTML.value
        page.record_name = "Old Page"
        page.external_record_id = "p1"

        other = MagicMock()
        other.record_type = RecordType.FILE
        other.mime_type = MimeTypes.HTML.value

        to_fix, ok = await c._fix_legacy_mime_types([page, other])
        assert page in to_fix
        assert page.mime_type == MimeTypes.BLOCKS.value
        assert other in ok
        mock_tx = c.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_records.assert_awaited_once_with(to_fix)

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Method deprecated - tests backward compatibility only")
    async def test_no_fix_needed(self):
        c = _mk_connector()
        page = MagicMock()
        page.record_type = RecordType.CONFLUENCE_PAGE
        page.mime_type = MimeTypes.BLOCKS.value

        to_fix, ok = await c._fix_legacy_mime_types([page])
        assert to_fix == []
        assert ok == [page]
        mock_tx = c.data_entities_processor.data_store_provider.transaction.return_value
        mock_tx.batch_upsert_records.assert_not_awaited()


class TestTransformSpacePermissionException:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _mk_connector()
        c._create_permission_from_principal = AsyncMock(side_effect=RuntimeError("boom"))
        perm_data = {
            "principal": {"type": "user", "id": "u1"},
            "operation": {"key": "read", "targetType": "space"},
        }
        assert await c._transform_space_permission(perm_data) is None


class TestParseConfluencePageToBlocks:
    @pytest.mark.asyncio
    async def test_minimal_adf_parses_blocks_container(self):
        c = _mk_connector()
        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
        }
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        c._batch_fetch_user_display_names = AsyncMock(return_value={})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-1",
            page_title="Test Page",
            weburl="https://wiki.example.com/page",
            record_type=RecordType.CONFLUENCE_PAGE,
        )
        assert container is not None
        assert len(container.blocks) >= 1

    @pytest.mark.asyncio
    async def test_invalid_adf_json_still_returns_container(self):
        c = _mk_connector()
        page_data = {"body": {"atlas_doc_format": {"value": "not-json"}}}
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        c._batch_fetch_user_display_names = AsyncMock(return_value={})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-2",
            page_title="Broken ADF",
            record_type=RecordType.CONFLUENCE_PAGE,
        )
        assert container is not None

    @pytest.mark.asyncio
    async def test_footer_comment_thread_processing(self):
        c = _mk_connector()
        adf = {"type": "doc", "content": []}
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        comment_adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Nice"}]}],
        }
        footer_comments = [{
            "id": "fc1",
            "version": {"authorId": "author-1", "createdAt": "2025-01-01T00:00:00.000Z"},
            "body": {"atlas_doc_format": {"value": json.dumps(comment_adf)}},
            "_links": {"webui": "/comments/fc1"},
        }]
        c._fetch_page_comments_recursive = AsyncMock(side_effect=[footer_comments, []])
        c._batch_fetch_user_display_names = AsyncMock(return_value={"author-1": "Author One"})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-3",
            page_title="With Comments",
            weburl="https://wiki.example.com/wiki/spaces/ENG/pages/3",
            record_type=RecordType.CONFLUENCE_PAGE,
        )
        assert any(bg.sub_type == GroupSubType.COMMENT_THREAD for bg in container.block_groups)

    @pytest.mark.asyncio
    async def test_embedded_image_excluded_from_remaining_attachments(self):
        c = _mk_connector()
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "media",
                    "attrs": {"id": "fid-1", "alt": "photo.png", "type": "file"},
                }
            ],
        }
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        attachment_children_map = {
            "img-1": ChildRecord(
                child_type=ChildType.RECORD, child_id="rec-img", child_name="photo.png"
            ),
            "pdf-1": ChildRecord(
                child_type=ChildType.RECORD, child_id="rec-pdf", child_name="doc.pdf"
            ),
        }
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        c._batch_fetch_user_display_names = AsyncMock(return_value={})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-4",
            page_title="Attachments Page",
            weburl="https://wiki.example.com/wiki/spaces/ENG/pages/4",
            attachment_children_map=attachment_children_map,
            attachment_mime_types={"img-1": "image/png", "pdf-1": "application/pdf"},
            attachments_data=[
                {"id": "img-1", "fileId": "fid-1", "title": "photo.png"},
                {"id": "pdf-1", "title": "doc.pdf"},
            ],
            record_type=RecordType.CONFLUENCE_PAGE,
        )

        content_groups = [
            bg for bg in container.block_groups if bg.sub_type == GroupSubType.CONTENT
        ]
        assert content_groups
        child_names = [
            cr.child_name
            for bg in content_groups
            if bg.children_records
            for cr in bg.children_records
        ]
        assert "doc.pdf" in child_names
        assert "photo.png" not in child_names

    @pytest.mark.asyncio
    async def test_inline_comments_attached_to_blocks(self):
        from app.connectors.sources.atlassian.confluence_cloud.block_parser import (
            ConfluenceBlockParser,
        )

        c = _mk_connector()
        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}],
        }
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        inline_comments = [{
            "id": "ic1",
            "version": {"authorId": "author-1"},
            "body": {"atlas_doc_format": {"value": json.dumps(adf)}},
        }]
        c._fetch_page_comments_recursive = AsyncMock(side_effect=[[], inline_comments])
        c._batch_fetch_user_display_names = AsyncMock(return_value={"author-1": "Author"})

        with patch.object(
            ConfluenceBlockParser,
            "attach_inline_comments_to_blocks",
            new_callable=AsyncMock,
        ) as mock_attach:
            await c._parse_confluence_page_to_blocks(
                page_data=page_data,
                page_id="page-5",
                page_title="Inline Page",
                weburl="https://wiki.example.com/wiki/spaces/ENG/pages/5",
                record_type=RecordType.CONFLUENCE_PAGE,
            )
            mock_attach.assert_awaited_once()
            assert mock_attach.call_args.kwargs["inline_comments"] == inline_comments

    @pytest.mark.asyncio
    async def test_footer_comment_invalid_adf_skipped(self):
        c = _mk_connector()
        adf = {"type": "doc", "content": []}
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        footer_comments = [{
            "id": "fc-bad",
            "version": {"authorId": "author-1"},
            "body": {"atlas_doc_format": {"value": "not-json"}},
            "_links": {"webui": "/comments/fc-bad"},
        }]
        c._fetch_page_comments_recursive = AsyncMock(side_effect=[footer_comments, []])
        c._batch_fetch_user_display_names = AsyncMock(return_value={"author-1": "Author"})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-6",
            page_title="Bad Comment ADF",
            weburl="https://wiki.example.com/wiki/spaces/ENG/pages/6",
            record_type=RecordType.CONFLUENCE_PAGE,
        )
        assert not any(bg.sub_type == GroupSubType.COMMENT for bg in container.block_groups)

    @pytest.mark.asyncio
    async def test_footer_comment_with_non_image_attachment(self):
        c = _mk_connector()
        adf = {"type": "doc", "content": []}
        page_data = {"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        comment_adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "See attached PDF"}],
                },
                {
                    "type": "media",
                    "attrs": {"id": "fid-pdf", "alt": "doc.pdf", "type": "file"},
                },
            ],
        }
        footer_comments = [{
            "id": "fc-pdf",
            "version": {"authorId": "author-1", "createdAt": "2025-01-01T00:00:00.000Z"},
            "body": {"atlas_doc_format": {"value": json.dumps(comment_adf)}},
            "_links": {"webui": "/comments/fc-pdf"},
        }]
        attachment_children_map = {
            "pdf-1": ChildRecord(
                child_type=ChildType.RECORD, child_id="rec-pdf", child_name="doc.pdf"
            ),
        }
        c._fetch_page_comments_recursive = AsyncMock(side_effect=[footer_comments, []])
        c._batch_fetch_user_display_names = AsyncMock(return_value={"author-1": "Author"})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-7",
            page_title="Comment Attachment",
            weburl="https://wiki.example.com/wiki/spaces/ENG/pages/7",
            attachment_children_map=attachment_children_map,
            attachment_mime_types={"pdf-1": "application/pdf"},
            attachments_data=[{"id": "pdf-1", "fileId": "fid-pdf", "title": "doc.pdf"}],
            record_type=RecordType.CONFLUENCE_PAGE,
        )

        comment_groups = [
            bg for bg in container.block_groups if bg.sub_type == GroupSubType.COMMENT
        ]
        assert comment_groups
        assert any(
            cr.child_name == "doc.pdf"
            for bg in comment_groups
            if bg.children_records
            for cr in bg.children_records
        )

    @pytest.mark.asyncio
    async def test_remaining_attachments_creates_content_wrapper_when_no_groups(self):
        c = _mk_connector()
        page_data = {"body": {"atlas_doc_format": {"value": "not-json"}}}
        attachment_children_map = {
            "pdf-1": ChildRecord(
                child_type=ChildType.RECORD, child_id="rec-pdf", child_name="doc.pdf"
            ),
        }
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        c._batch_fetch_user_display_names = AsyncMock(return_value={})

        container = await c._parse_confluence_page_to_blocks(
            page_data=page_data,
            page_id="page-8",
            page_title="Wrapper Page",
            weburl="https://wiki.example.com/wiki/spaces/ENG/pages/8",
            attachment_children_map=attachment_children_map,
            attachment_mime_types={"pdf-1": "application/pdf"},
            attachments_data=[{"id": "pdf-1", "title": "doc.pdf"}],
            record_type=RecordType.CONFLUENCE_PAGE,
        )

        content_groups = [
            bg for bg in container.block_groups if bg.sub_type == GroupSubType.CONTENT
        ]
        assert len(content_groups) == 1
        assert content_groups[0].children_records
        assert content_groups[0].children_records[0].child_name == "doc.pdf"


class TestSyncContentEmbeddedImages:
    """Cover _sync_content embedded image detection (connector.py 1488-1649)."""

    @pytest.mark.asyncio
    async def test_page_skips_embedded_image_saves_pdf(self):
        c = _mk_connector()
        _setup_sync_content(c)
        v1_attachments = [
            {"id": "img-1", "title": "photo.png", "mediaType": "image/png", "fileId": "fid-1"},
            {"id": "pdf-1", "title": "doc.pdf", "mediaType": "application/pdf"},
        ]
        page_data = _sync_page_data_with_attachments(v1_attachments)
        adf_body = {
            "type": "doc",
            "content": [
                {"type": "media", "attrs": {"id": "fid-1", "alt": "photo.png", "type": "file"}},
            ],
        }
        ds = _mock_ds_for_embedded_images(
            content_type="page",
            attachments_v2=v1_attachments,
            adf_body=adf_body,
        )
        ds.get_pages_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [page_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        page_record = MagicMock(id="rec-p1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(
            return_value=MagicMock(record=page_record)
        )

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)

        ds.get_page_attachments.assert_awaited_once()
        ds.get_page_content_v2.assert_awaited_once()
        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        attachment_ids = [
            r.external_record_id
            for r, _ in saved
            if getattr(r, "record_type", None) == RecordType.FILE
        ]
        assert "pdf-1" in attachment_ids
        assert "img-1" not in attachment_ids

    @pytest.mark.asyncio
    async def test_blogpost_embedded_image_detection(self):
        c = _mk_connector()
        _setup_sync_content(c)
        v1_attachments = [
            {"id": "img-b1", "title": "banner.png", "mediaType": "image/png", "fileId": "fid-b1"},
            {"id": "pdf-b1", "title": "notes.pdf", "mediaType": "application/pdf"},
        ]
        blog_data = _sync_page_data_with_attachments(v1_attachments, item_id="200002", title="Blog1")
        adf_body = {
            "type": "doc",
            "content": [
                {"type": "media", "attrs": {"id": "fid-b1", "alt": "banner.png", "type": "file"}},
            ],
        }
        ds = _mock_ds_for_embedded_images(
            content_type="blogpost",
            attachments_v2=v1_attachments,
            adf_body=adf_body,
        )
        ds.get_blogposts_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [blog_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        blog_record = MagicMock(id="rec-b1", inherit_permissions=True)
        c._process_webpage_with_update = AsyncMock(
            return_value=MagicMock(record=blog_record)
        )

        await c._sync_content("S1", RecordType.CONFLUENCE_BLOGPOST)

        ds.get_blogpost_attachments.assert_awaited_once()
        ds.get_blogpost_content_v2.assert_awaited_once()
        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        attachment_ids = [
            r.external_record_id
            for r, _ in saved
            if getattr(r, "record_type", None) == RecordType.FILE
        ]
        assert "pdf-b1" in attachment_ids
        assert "img-b1" not in attachment_ids

    @pytest.mark.asyncio
    async def test_footer_and_inline_comments_contribute_embedded_ids(self):
        c = _mk_connector()
        _setup_sync_content(c)
        v1_attachments = [
            {"id": "img-2", "title": "footer.png", "mediaType": "image/png", "fileId": "fid-2"},
            {"id": "img-3", "title": "inline.png", "mediaType": "image/png", "fileId": "fid-3"},
            {"id": "pdf-1", "title": "doc.pdf", "mediaType": "application/pdf"},
        ]
        page_data = _sync_page_data_with_attachments(v1_attachments)
        footer_adf = {
            "type": "doc",
            "content": [
                {"type": "media", "attrs": {"id": "fid-2", "alt": "footer.png", "type": "file"}},
            ],
        }
        inline_adf = {
            "type": "doc",
            "content": [
                {"type": "media", "attrs": {"id": "fid-3", "alt": "inline.png", "type": "file"}},
            ],
        }
        footer_comments = [{"body": {"atlas_doc_format": {"value": json.dumps(footer_adf)}}}]
        inline_comments = [{"body": {"atlas_doc_format": {"value": json.dumps(inline_adf)}}}]
        ds = _mock_ds_for_embedded_images(
            content_type="page",
            attachments_v2=v1_attachments,
            adf_body={"type": "doc", "content": []},
            footer_comments=footer_comments,
            inline_comments=inline_comments,
        )
        ds.get_pages_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [page_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        c._process_webpage_with_update = AsyncMock(
            return_value=MagicMock(record=MagicMock(id="rec-p1", inherit_permissions=True))
        )

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)

        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        attachment_ids = [
            r.external_record_id
            for r, _ in saved
            if getattr(r, "record_type", None) == RecordType.FILE
        ]
        assert attachment_ids == ["pdf-1"]

    @pytest.mark.asyncio
    async def test_adf_fetch_failure_fallback_creates_all_attachments(self):
        c = _mk_connector()
        _setup_sync_content(c)
        v1_attachments = [
            {"id": "img-1", "title": "photo.png", "mediaType": "image/png", "fileId": "fid-1"},
            {"id": "pdf-1", "title": "doc.pdf", "mediaType": "application/pdf"},
        ]
        page_data = _sync_page_data_with_attachments(v1_attachments)
        ds = _mock_ds_for_embedded_images(
            content_type="page",
            attachments_v2=v1_attachments,
            adf_body={"type": "doc", "content": []},
            adf_status=500,
        )
        ds.get_pages_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [page_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        c._process_webpage_with_update = AsyncMock(
            return_value=MagicMock(record=MagicMock(id="rec-p1", inherit_permissions=True))
        )

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)

        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        attachment_ids = [
            r.external_record_id
            for r, _ in saved
            if getattr(r, "record_type", None) == RecordType.FILE
        ]
        assert set(attachment_ids) == {"img-1", "pdf-1"}

    @pytest.mark.asyncio
    async def test_v2_attachments_fetch_exception_uses_v1_list(self):
        c = _mk_connector()
        _setup_sync_content(c)
        v1_attachments = [
            {"id": "pdf-1", "title": "doc.pdf", "mediaType": "application/pdf"},
        ]
        page_data = _sync_page_data_with_attachments(v1_attachments)
        ds = _mock_ds_for_embedded_images(
            content_type="page",
            attachments_v2=v1_attachments,
            adf_body={"type": "doc", "content": []},
            adf_status=500,
        )
        ds.get_page_attachments = AsyncMock(side_effect=RuntimeError("v2 down"))
        ds.get_pages_v1 = AsyncMock(
            return_value=_mk_resp(200, {"results": [page_data], "_links": {}})
        )
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        c._process_webpage_with_update = AsyncMock(
            return_value=MagicMock(record=MagicMock(id="rec-p1", inherit_permissions=True))
        )

        await c._sync_content("S1", RecordType.CONFLUENCE_PAGE)

        saved = c.data_entities_processor.on_new_records.call_args[0][0]
        attachment_ids = [
            r.external_record_id
            for r, _ in saved
            if getattr(r, "record_type", None) == RecordType.FILE
        ]
        assert "pdf-1" in attachment_ids


class TestStreamRecordBlocksPath:
    """Cover stream_record blocks path without mocking _parse_confluence_page_to_blocks."""

    @pytest.mark.asyncio
    async def test_stream_page_blocks_end_to_end(self):
        c = _mk_connector()
        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.mime_type = MimeTypes.BLOCKS.value
        record.external_record_id = "100001"
        record.record_name = "Blocks Page"
        record.id = "rec-1"
        record.external_record_group_id = "sp1"
        record.weburl = "https://wiki.example.com/wiki/spaces/ENG/pages/100001"

        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]}],
        }
        c._fetch_page_data_with_adf = AsyncMock(
            return_value={"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        )
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [{
                "id": "att-1",
                "title": "file.pdf",
                "metadata": {"mediaType": "application/pdf"},
            }],
            "_links": {"base": "https://wiki.example.com/wiki"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        response = await c.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        payload = json.loads(body.decode("utf-8"))

        assert response.media_type == MimeTypes.BLOCKS.value
        assert "blocks" in payload

    @pytest.mark.asyncio
    async def test_stream_blogpost_uses_blogpost_attachments(self):
        c = _mk_connector()
        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.CONFLUENCE_BLOGPOST
        record.mime_type = MimeTypes.BLOCKS.value
        record.external_record_id = "200002"
        record.record_name = "Blog"
        record.id = "rec-b1"
        record.external_record_group_id = "sp1"
        record.weburl = "https://wiki.example.com/wiki/spaces/ENG/blog/200002"

        adf = {"type": "doc", "content": []}
        c._fetch_page_data_with_adf = AsyncMock(
            return_value={"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        )
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        ds = MagicMock()
        ds.get_blogpost_attachments = AsyncMock(return_value=_mk_resp(200, {
            "results": [],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        response = await c.stream_record(record)
        ds.get_blogpost_attachments.assert_awaited_once()
        assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_attachment_fetch_failure_continues(self):
        c = _mk_connector()
        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.mime_type = MimeTypes.BLOCKS.value
        record.external_record_id = "100002"
        record.record_name = "Page"
        record.id = "rec-2"
        record.external_record_group_id = "sp1"
        record.weburl = "https://wiki.example.com/wiki/spaces/ENG/pages/100002"

        adf = {"type": "doc", "content": []}
        c._fetch_page_data_with_adf = AsyncMock(
            return_value={"body": {"atlas_doc_format": {"value": json.dumps(adf)}}}
        )
        c._fetch_page_comments_recursive = AsyncMock(return_value=[])
        ds = MagicMock()
        ds.get_page_attachments = AsyncMock(side_effect=RuntimeError("attachments fail"))
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        response = await c.stream_record(record)
        assert response.media_type == MimeTypes.BLOCKS.value


class TestFilterOptionsCursorParseFailure:
    @pytest.mark.asyncio
    async def test_page_options_cursor_parse_failure(self):
        c = _mk_connector()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_mk_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {"next": "https://example.com/pages?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.urlparse",
            side_effect=ValueError("bad url"),
        ):
            result = await c._get_page_options(1, 20, None, None)

        assert result.has_more is False
        assert result.cursor is None

    @pytest.mark.asyncio
    async def test_blogpost_options_cursor_parse_failure(self):
        c = _mk_connector()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_mk_resp(200, {
            "results": [{"id": "b1", "title": "Blog 1"}],
            "_links": {"next": "https://example.com/blogposts?cursor=xyz"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.urlparse",
            side_effect=ValueError("bad url"),
        ):
            result = await c._get_blogpost_options(1, 20, None, None)

        assert result.has_more is False
        assert result.cursor is None
