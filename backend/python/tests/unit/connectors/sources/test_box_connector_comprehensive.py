"""Comprehensive coverage tests for Box connector - additional untested methods."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.connectors.sources.box.connector import (
    BoxConnector,
    get_file_extension,
    get_mimetype_enum_for_box,
    get_parent_path_from_path,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_deps():
    logger = logging.getLogger("test.box.comp")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-box-comp"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)

    class FakeTx:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTx())
    config_service = AsyncMock()

    return {
        "logger": logger,
        "data_entities_processor": data_entities_processor,
        "data_store_provider": data_store_provider,
        "config_service": config_service,
        "mock_tx_store": mock_tx_store,
    }


@pytest.fixture()
def box_connector(mock_deps):
    with patch("app.connectors.sources.box.connector.BoxApp"):
        c = BoxConnector(
            logger=mock_deps["logger"],
            data_entities_processor=mock_deps["data_entities_processor"],
            data_store_provider=mock_deps["data_store_provider"],
            config_service=mock_deps["config_service"],
            connector_id="box-comp-1",
        )
    return c


# ===========================================================================
# Module-level helper functions
# ===========================================================================
class TestGetParentPathFromPathExtended:
    def test_deeply_nested(self):
        assert get_parent_path_from_path("/a/b/c/d/file.txt") == "/a/b/c/d"

    def test_single_file_in_root(self):
        """A file directly in root: '/file.txt' should return None."""
        assert get_parent_path_from_path("/file.txt") is None

    def test_two_level_path(self):
        assert get_parent_path_from_path("/folder/file.txt") == "/folder"

    def test_trailing_slash(self):
        result = get_parent_path_from_path("/folder/subfolder/")
        assert result == "/folder"


class TestGetFileExtensionExtended:
    def test_tar_gz(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_no_dot(self):
        assert get_file_extension("Makefile") is None

    def test_empty(self):
        assert get_file_extension("") is None

    def test_dot_only(self):
        # "." splits to ['', ''], parts[-1] = '' which is empty string
        assert get_file_extension(".") == ""

    def test_leading_dot_file(self):
        result = get_file_extension(".gitignore")
        assert result == "gitignore"


class TestGetMimetypeEnumForBoxExtended:
    def test_folder_type(self):
        result = get_mimetype_enum_for_box("folder")
        assert result == MimeTypes.FOLDER

    def test_known_pdf(self):
        result = get_mimetype_enum_for_box("file", "report.pdf")
        assert result == MimeTypes.PDF

    def test_unknown_extension(self):
        result = get_mimetype_enum_for_box("file", "data.xyz123")
        assert result == MimeTypes.BIN

    def test_no_filename(self):
        result = get_mimetype_enum_for_box("file")
        assert result == MimeTypes.BIN

    def test_docx_file(self):
        result = get_mimetype_enum_for_box("file", "document.docx")
        # docx maps to application/vnd.openxmlformats-officedocument.wordprocessingml.document
        assert result is not None


# ===========================================================================
# _parse_box_timestamp
# ===========================================================================
class TestParseBoxTimestamp:
    def test_valid_iso_timestamp(self, box_connector):
        result = box_connector._parse_box_timestamp("2024-06-15T12:30:00Z", "test", "file.txt")
        assert result > 0

    def test_iso_with_timezone(self, box_connector):
        result = box_connector._parse_box_timestamp("2024-06-15T12:30:00+05:30", "test", "file.txt")
        assert result > 0

    def test_none_timestamp_fallback(self, box_connector):
        result = box_connector._parse_box_timestamp(None, "test", "file.txt")
        assert result > 0  # Falls back to current time

    def test_invalid_timestamp_fallback(self, box_connector):
        result = box_connector._parse_box_timestamp("invalid", "test", "file.txt")
        assert result > 0  # Falls back to current time

    def test_empty_string_fallback(self, box_connector):
        result = box_connector._parse_box_timestamp("", "test", "file.txt")
        assert result > 0  # Falls back to current time


# ===========================================================================
# _to_dict
# ===========================================================================
class TestToDict:
    def test_dict_passthrough(self, box_connector):
        d = {"key": "value"}
        assert box_connector._to_dict(d) == d

    def test_none_returns_empty(self, box_connector):
        assert box_connector._to_dict(None) == {}

    def test_object_with_to_dict(self, box_connector):
        obj = MagicMock()
        obj.to_dict.return_value = {"a": 1}
        assert box_connector._to_dict(obj) == {"a": 1}

    def test_object_with_response_object(self, box_connector):
        obj = MagicMock(spec=[])
        obj.response_object = {"b": 2}
        assert box_connector._to_dict(obj) == {"b": 2}

    def test_fallback_empty(self, box_connector):
        # Object with no to_dict, no response_object, not a dict
        obj = 42
        assert box_connector._to_dict(obj) == {}


# ===========================================================================
# _should_include_file
# ===========================================================================
class TestShouldIncludeFile:
    def test_non_file_always_included(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        box_connector._cached_date_filters = (None, None, None, None)
        entry = {"type": "folder", "name": "my_folder"}
        assert box_connector._should_include_file(entry) is True

    def test_no_extension_filter_allows_all(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        box_connector._cached_date_filters = (None, None, None, None)
        entry = {"type": "file", "name": "document.pdf"}
        assert box_connector._should_include_file(entry) is True

    def test_modified_date_filter_excludes_older(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        box_connector._cached_date_filters = (cutoff, None, None, None)
        entry = {
            "type": "file",
            "name": "old.txt",
            "modified_at": "2024-01-01T00:00:00Z",
        }
        assert box_connector._should_include_file(entry) is False

    def test_modified_date_filter_includes_newer(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        box_connector._cached_date_filters = (cutoff, None, None, None)
        entry = {
            "type": "file",
            "name": "new.txt",
            "modified_at": "2024-06-15T00:00:00Z",
        }
        assert box_connector._should_include_file(entry) is True

    def test_created_date_filter_excludes(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        box_connector._cached_date_filters = (None, None, cutoff, None)
        entry = {
            "type": "file",
            "name": "old.txt",
            "created_at": "2024-01-01T00:00:00Z",
        }
        assert box_connector._should_include_file(entry) is False

    def test_modified_before_filter(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        before = datetime(2024, 3, 1, tzinfo=timezone.utc)
        box_connector._cached_date_filters = (None, before, None, None)
        entry = {
            "type": "file",
            "name": "too_new.txt",
            "modified_at": "2024-06-15T00:00:00Z",
        }
        assert box_connector._should_include_file(entry) is False

    def test_no_modified_date_with_filter_excludes(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        box_connector._cached_date_filters = (cutoff, None, None, None)
        entry = {"type": "file", "name": "no_date.txt"}
        assert box_connector._should_include_file(entry) is False

    def test_extension_filter_in_operator(self, box_connector):
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["pdf", "docx"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_ext_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_ext_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        box_connector.sync_filters = mock_filters
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file({"type": "file", "name": "report.pdf"}) is True
        assert box_connector._should_include_file({"type": "file", "name": "image.png"}) is False

    def test_extension_filter_not_in_operator(self, box_connector):
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["exe", "bat"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_ext_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_ext_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        box_connector.sync_filters = mock_filters
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file({"type": "file", "name": "report.pdf"}) is True
        assert box_connector._should_include_file({"type": "file", "name": "virus.exe"}) is False

    def test_file_without_extension_not_in_allows(self, box_connector):
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["exe"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.NOT_IN
        mock_ext_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_ext_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        box_connector.sync_filters = mock_filters
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file({"type": "file", "name": "Makefile"}) is True


# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestGetDateFilters:
    def test_no_filters(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        result = box_connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_modified_after(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", None)

        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        box_connector.sync_filters = mock_filters

        mod_after, mod_before, _, _ = box_connector._get_date_filters()
        assert mod_after is not None
        assert mod_before is None

    def test_both_filters(self, box_connector):
        mock_mod_filter = MagicMock()
        mock_mod_filter.is_empty.return_value = False
        mock_mod_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")

        mock_created_filter = MagicMock()
        mock_created_filter.is_empty.return_value = False
        mock_created_filter.get_datetime_iso.return_value = ("2024-03-01T00:00:00", None)

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_mod_filter
            if key == SyncFilterKey.CREATED:
                return mock_created_filter
            return None

        mock_filters = MagicMock()
        mock_filters.get.side_effect = side_effect
        box_connector.sync_filters = mock_filters

        mod_after, mod_before, created_after, created_before = box_connector._get_date_filters()
        assert mod_after is not None
        assert mod_before is not None
        assert created_after is not None
        assert created_before is None


# ===========================================================================
# _process_box_entry
# ===========================================================================
class TestProcessBoxEntry:
    async def test_entry_without_id(self, box_connector):
        entry = {"name": "test.txt", "type": "file"}
        result = await box_connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is None

    async def test_entry_without_name(self, box_connector):
        entry = {"id": "123", "type": "file"}
        result = await box_connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is None

    async def test_file_entry_success(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        box_connector._cached_date_filters = (None, None, None, None)
        entry = {
            "id": "123",
            "name": "document.pdf",
            "type": "file",
            "size": 2048,
            "created_at": "2024-01-01T00:00:00Z",
            "modified_at": "2024-01-02T00:00:00Z",
            "path_collection": {"entries": [{"id": "0", "name": "All Files"}, {"id": "10", "name": "Docs"}]},
        }
        result = await box_connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None
        assert result.record.record_name == "document.pdf"
        assert result.record.size_in_bytes == 2048

    async def test_folder_entry(self, box_connector):
        box_connector.sync_filters = FilterCollection()
        box_connector._cached_date_filters = (None, None, None, None)
        entry = {
            "id": "456",
            "name": "My Folder",
            "type": "folder",
            "path_collection": {"entries": []},
        }
        result = await box_connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is not None

    async def test_file_filtered_by_extension(self, box_connector):
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["txt"]
        mock_operator = MagicMock()
        mock_operator.value = FilterOperator.IN
        mock_ext_filter.get_operator.return_value = mock_operator

        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_ext_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        box_connector.sync_filters = mock_filters
        box_connector._cached_date_filters = (None, None, None, None)

        entry = {
            "id": "789",
            "name": "image.png",
            "type": "file",
            "path_collection": {"entries": []},
        }
        result = await box_connector._process_box_entry(entry, "user1", "user@test.com", "rg-1")
        assert result is None


# ===========================================================================
# get_filter_options
# ===========================================================================
class TestGetFilterOptions:
    async def test_raises_not_implemented(self, box_connector):
        with pytest.raises(NotImplementedError):
            await box_connector.get_filter_options("some_key")


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    async def test_no_op(self, box_connector):
        """handle_webhook_notification creates asyncio task, so we mock run_incremental_sync."""
        box_connector.run_incremental_sync = AsyncMock()
        with patch("app.connectors.sources.box.connector.asyncio.create_task") as mock_create_task:
            box_connector.handle_webhook_notification({})
            mock_create_task.assert_called_once()


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestTestConnectionAndAccess:
    async def test_success(self, box_connector):
        box_connector.data_source = MagicMock()
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=True)
        )
        result = await box_connector.test_connection_and_access()
        assert result is True

    async def test_no_data_source(self, box_connector):
        box_connector.data_source = None
        result = await box_connector.test_connection_and_access()
        assert result is False

    async def test_api_failure(self, box_connector):
        box_connector.data_source = MagicMock()
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=False)
        )
        result = await box_connector.test_connection_and_access()
        assert result is False

    async def test_exception(self, box_connector):
        box_connector.data_source = MagicMock()
        box_connector.data_source.get_current_user = AsyncMock(side_effect=Exception("Network error"))
        result = await box_connector.test_connection_and_access()
        assert result is False
