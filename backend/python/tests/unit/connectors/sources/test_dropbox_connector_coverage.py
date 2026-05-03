"""Comprehensive coverage tests for the Dropbox connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.connectors.sources.dropbox.connector import (
    DropboxConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.models.entities import AppUser, FileRecord, RecordType
from app.models.permission import EntityType, Permission, PermissionType


def _make_connector():
    logger = logging.getLogger("test.dropbox")
    dep = MagicMock()
    dep.org_id = "org-dbx-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()
    cs = AsyncMock()
    return DropboxConnector(logger, dep, dsp, cs, "conn-dbx-1", "team", "test-user-id")


def _make_file_metadata(name="test.pdf"):
    from dropbox.files import FileMetadata
    meta = MagicMock(spec=FileMetadata)
    meta.__class__ = FileMetadata
    meta.name = name
    meta.path_lower = f"/{name}"
    meta.id = f"id:{uuid4()}"
    meta.size = 1024
    meta.server_modified = datetime(2024, 6, 1, tzinfo=timezone.utc)
    meta.client_modified = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return meta


def _make_folder_metadata(name="folder"):
    from dropbox.files import FolderMetadata
    meta = MagicMock(spec=FolderMetadata)
    meta.__class__ = FolderMetadata
    meta.name = name
    meta.path_lower = f"/{name}"
    meta.id = f"id:{uuid4()}"
    return meta


class TestGetParentPathFromPath:
    def test_root(self):
        assert get_parent_path_from_path("/") is None

    def test_empty(self):
        assert get_parent_path_from_path("") is None

    def test_top_level(self):
        assert get_parent_path_from_path("/file.txt") is None

    def test_nested(self):
        assert get_parent_path_from_path("/folder/file.txt") == "/folder"

    def test_deeply_nested(self):
        assert get_parent_path_from_path("/a/b/c/file.txt") == "/a/b/c"

    def test_none(self):
        assert get_parent_path_from_path(None) is None


class TestGetFileExtension:
    def test_pdf(self):
        assert get_file_extension("file.pdf") == "pdf"

    def test_no_extension(self):
        assert get_file_extension("file") is None

    def test_multiple_dots(self):
        assert get_file_extension("file.tar.gz") == "gz"


class TestGetMimetypeEnumForDropbox:
    def test_folder(self):
        from dropbox.files import FolderMetadata
        entry = MagicMock(spec=FolderMetadata)
        entry.__class__ = FolderMetadata
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_paper_file(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.__class__ = FileMetadata
        entry.name = "doc.paper"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_pdf_file(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.__class__ = FileMetadata
        entry.name = "doc.pdf"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF


class TestDropboxConnectorInit:
    def test_initialization(self):
        c = _make_connector()
        assert c.connector_name == Connectors.DROPBOX
        assert c.connector_id == "conn-dbx-1"
        assert c.data_source is None


class TestDropboxInitMethod:
    @pytest.mark.asyncio
    async def test_init_no_config(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value=None)
        result = await c.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref"},
            "auth": {}
        })
        result = await c.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_success(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref", "isTeam": True},
            "auth": {"oauthConfigId": "oauth-1"}
        })
        with patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock, return_value={
            "config": {"clientId": "cid", "clientSecret": "csecret"}
        }):
            with patch("app.connectors.sources.dropbox.connector.DropboxClient") as MockClient:
                MockClient.build_with_config = AsyncMock(return_value=MagicMock())
                with patch("app.connectors.sources.dropbox.connector.DropboxDataSource"):
                    result = await c.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_client_error(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref", "isTeam": True},
            "auth": {"oauthConfigId": "oauth-1"}
        })
        with patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock, return_value={
            "config": {"clientId": "cid", "clientSecret": "csecret"}
        }):
            with patch("app.connectors.sources.dropbox.connector.DropboxClient") as MockClient:
                MockClient.build_with_config = AsyncMock(side_effect=Exception("fail"))
                result = await c.init()
        assert result is False


class TestDropboxDateFilters:
    def test_folder_always_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(_make_folder_metadata()) is True

    def test_no_filters(self):
        c = _make_connector()
        assert c._pass_date_filters(_make_file_metadata()) is True

    def test_modified_after_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc)
        ) is True

    def test_modified_after_fails(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_after=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ) is False

    def test_modified_before_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_before=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ) is True

    def test_modified_before_fails(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_before=datetime(2023, 1, 1, tzinfo=timezone.utc)
        ) is False

    def test_get_date_filters_no_filters(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._get_date_filters() == (None, None, None, None)


class TestDropboxExtensionFilter:
    def test_folder_passes(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._pass_extension_filter(_make_folder_metadata()) is True

    def test_no_filter(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._pass_extension_filter(_make_file_metadata()) is True


class TestDropboxPermissionEquality:
    def test_equal(self):
        c = _make_connector()
        p1 = [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")]
        p2 = [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")]
        assert c._permissions_equal(p1, p2) is True

    def test_different_length(self):
        c = _make_connector()
        assert c._permissions_equal(
            [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")],
            []
        ) is False


class TestDropboxMisc:
    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        c = _make_connector()
        c.run_incremental_sync = AsyncMock()
        c.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _make_connector()
        await c.cleanup()

    @pytest.mark.asyncio
    async def test_reindex_empty(self):
        c = _make_connector()
        await c.reindex_records([])


class TestDropboxCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.dropbox.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await DropboxConnector.create_connector(
                logger=logging.getLogger("test"),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-dbx",
                scope="personal",
                created_by="test-user-id",
            )
            assert isinstance(connector, DropboxConnector)
