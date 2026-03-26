"""Extended tests for DropboxIndividualConnector covering helper functions and init."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.models.entities import RecordType
from app.models.permission import PermissionType


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestGetParentPathFromPath:
    def test_none(self):
        assert get_parent_path_from_path(None) is None

    def test_root(self):
        assert get_parent_path_from_path("/") is None

    def test_empty(self):
        assert get_parent_path_from_path("") is None

    def test_single_level(self):
        assert get_parent_path_from_path("/file.txt") is None

    def test_nested(self):
        assert get_parent_path_from_path("/folder/file.txt") == "/folder"

    def test_deeply_nested(self):
        assert get_parent_path_from_path("/a/b/c/file.txt") == "/a/b/c"


class TestGetFileExtension:
    def test_with_extension(self):
        assert get_file_extension("file.txt") == "txt"

    def test_no_extension(self):
        assert get_file_extension("noext") is None

    def test_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"


class TestGetMimetypeEnumForDropbox:
    def test_folder(self):
        from dropbox.files import FolderMetadata
        entry = MagicMock(spec=FolderMetadata)
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_file_txt(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.name = "test.txt"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PLAIN_TEXT

    def test_file_pdf(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.name = "doc.pdf"
        result = get_mimetype_enum_for_dropbox(entry)
        assert result is not None

    def test_paper_file(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.name = "doc.paper"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_unknown_extension(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.name = "file.xyz_unknown_ext"
        result = get_mimetype_enum_for_dropbox(entry)
        assert result == MimeTypes.BIN

    def test_zip_file_falls_to_bin(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.name = "archive.zip"
        result = get_mimetype_enum_for_dropbox(entry)
        # zip may or may not be in MimeTypes enum
        assert result is not None

    def test_unknown_type_falls_to_bin(self):
        entry = MagicMock()
        # Not FileMetadata or FolderMetadata
        type(entry).__name__ = "UnknownMetadata"
        result = get_mimetype_enum_for_dropbox(entry)
        assert result == MimeTypes.BIN


# ---------------------------------------------------------------------------
# Connector fixture
# ---------------------------------------------------------------------------

def _make_mock_data_store_provider(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    return provider


@pytest.fixture
def connector():
    with patch("app.connectors.sources.dropbox_individual.connector.SyncPoint") as MockSP:
        mock_sp = AsyncMock()
        mock_sp.read_sync_point = AsyncMock(return_value=None)
        mock_sp.update_sync_point = AsyncMock()
        MockSP.return_value = mock_sp

        logger = logging.getLogger("test_dropbox_ind")
        dep = AsyncMock()
        dep.org_id = "org-1"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        with patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp"):
            conn = DropboxIndividualConnector(
                logger=logger,
                data_entities_processor=dep,
                data_store_provider=ds_provider,
                config_service=config_service,
                connector_id="dbx-conn-1",
            )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.data_source = AsyncMock()
        yield conn


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestDropboxInit:
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    async def test_init_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "t"}, "auth": {},
        })
        result = await connector.init()
        assert result is False

    @patch("app.connectors.sources.dropbox_individual.connector.fetch_oauth_config_by_id")
    async def test_init_oauth_config_not_found(self, mock_fetch, connector):
        mock_fetch.return_value = None
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "t", "refresh_token": "r"},
            "auth": {"oauthConfigId": "oc-1"},
        })
        result = await connector.init()
        assert result is False


# ---------------------------------------------------------------------------
# _get_current_user_info
# ---------------------------------------------------------------------------

class TestGetCurrentUserInfo:
    async def test_cached_info(self, connector):
        connector.current_user_id = "cached-id"
        connector.current_user_email = "cached@example.com"
        uid, email = await connector._get_current_user_info()
        assert uid == "cached-id"
        assert email == "cached@example.com"

    async def test_empty_response(self, connector):
        connector.data_source.users_get_current_account = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="empty response"):
            await connector._get_current_user_info()

    async def test_error_response(self, connector):
        resp = MagicMock()
        resp.success = False
        resp.error = "Auth failed"
        resp.data = None
        connector.data_source.users_get_current_account = AsyncMock(return_value=resp)
        with pytest.raises(ValueError, match="Auth failed"):
            await connector._get_current_user_info()

    async def test_no_data(self, connector):
        resp = MagicMock()
        resp.success = True
        resp.data = None
        connector.data_source.users_get_current_account = AsyncMock(return_value=resp)
        with pytest.raises(ValueError, match="no payload"):
            await connector._get_current_user_info()

    async def test_success(self, connector):
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock()
        resp.data.account_id = "dbx-uid"
        resp.data.email = "user@example.com"
        connector.data_source.users_get_current_account = AsyncMock(return_value=resp)
        uid, email = await connector._get_current_user_info()
        assert uid == "dbx-uid"
        assert email == "user@example.com"


# ---------------------------------------------------------------------------
# _get_current_user_as_app_user
# ---------------------------------------------------------------------------

class TestGetCurrentUserAsAppUser:
    def test_with_display_name(self, connector):
        account = MagicMock()
        account.account_id = "dbx-uid"
        account.email = "user@example.com"
        account.name = MagicMock()
        account.name.display_name = "John Doe"
        result = connector._get_current_user_as_app_user(account)
        assert result.full_name == "John Doe"

    def test_fallback_to_email(self, connector):
        account = MagicMock()
        account.account_id = "dbx-uid"
        account.email = "user@example.com"
        account.name = MagicMock()
        account.name.display_name = None
        result = connector._get_current_user_as_app_user(account)
        assert result.full_name == "user"


# ---------------------------------------------------------------------------
# _process_dropbox_entry
# ---------------------------------------------------------------------------

class TestProcessDropboxEntry:
    def _mock_sharing(self, connector, url="https://dropbox.com/s/abc"):
        """Setup sharing mocks with proper string values."""
        shared_result = MagicMock()
        shared_result.success = True
        shared_result.data = MagicMock()
        shared_result.data.url = url
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=shared_result
        )

    def _mock_temp_link(self, connector, link="https://dl.dropbox.com/temp"):
        temp_link = MagicMock()
        temp_link.success = True
        temp_link.data = MagicMock()
        temp_link.data.link = link
        connector.data_source.files_get_temporary_link = AsyncMock(return_value=temp_link)

    def _make_file_entry(self, name="test.txt", file_id="file-id-1", rev="rev-1",
                         path_lower=None, path_display=None):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.id = file_id
        entry.name = name
        entry.path_lower = path_lower or f"/{name}"
        entry.path_display = path_display or f"/{name}"
        entry.rev = rev
        entry.server_modified = datetime(2025, 1, 15, tzinfo=timezone.utc)
        entry.size = 1024
        entry.content_hash = "abc123hash"
        return entry

    def _make_folder_entry(self, name="My Folder", folder_id="folder-id-1"):
        from dropbox.files import FolderMetadata
        entry = MagicMock(spec=FolderMetadata)
        entry.id = folder_id
        entry.name = name
        entry.path_lower = f"/{name.lower()}"
        entry.path_display = f"/{name}"
        return entry

    async def test_new_file_entry(self, connector):
        entry = self._make_file_entry()
        self._mock_temp_link(connector)
        self._mock_sharing(connector)

        result = await connector._process_dropbox_entry(
            entry, "user-1", "user@example.com", "rg-1",
        )
        assert result is not None
        assert result.is_new is True

    async def test_folder_entry(self, connector):
        entry = self._make_folder_entry()
        self._mock_sharing(connector)

        result = await connector._process_dropbox_entry(
            entry, "user-1", "user@example.com", "rg-1",
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_existing_record_no_changes(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "test.txt"
        existing.external_revision_id = "rev-1"
        existing.version = 0
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = ProgressStatus.COMPLETED.value
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        entry = self._make_file_entry()
        self._mock_temp_link(connector)
        self._mock_sharing(connector)

        result = await connector._process_dropbox_entry(
            entry, "user-1", "user@example.com", "rg-1",
        )
        assert result is not None
        assert result.is_updated is False

    async def test_existing_record_name_changed(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "old_name.txt"
        existing.external_revision_id = "rev-1"
        existing.version = 0
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        entry = self._make_file_entry(name="new_name.txt")
        self._mock_temp_link(connector)
        self._mock_sharing(connector)

        result = await connector._process_dropbox_entry(
            entry, "user-1", "user@example.com", "rg-1",
        )
        assert result.is_updated is True
        assert result.metadata_changed is True
