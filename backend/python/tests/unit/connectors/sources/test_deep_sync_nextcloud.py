"""Deep sync loop tests for NextcloudConnector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.nextcloud.connector import (
    NextcloudConnector,
    extract_response_body,
    get_file_extension,
    get_mimetype_enum_for_nextcloud,
    get_parent_path_from_path,
    get_path_depth,
    is_response_successful,
    nextcloud_permissions_to_permission_type,
    parse_webdav_propfind_response,
)
from app.models.entities import AppUser, RecordGroupType
from app.models.permission import EntityType, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mock_data_store_provider(existing_record=None, existing_group=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=existing_group)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


@pytest.fixture
def connector():
    with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"), \
         patch("app.connectors.sources.nextcloud.connector.SyncPoint") as MockSP:
        mock_sp = AsyncMock()
        mock_sp.read_sync_point = AsyncMock(return_value=None)
        mock_sp.update_sync_point = AsyncMock()
        MockSP.return_value = mock_sp

        from app.connectors.sources.nextcloud.connector import NextcloudConnector

        logger = logging.getLogger("test_nc_deep")
        dep = AsyncMock()
        dep.org_id = "org-nc"
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
            "auth": {
                "baseUrl": "https://nc.example.com",
                "username": "admin",
                "password": "pass123",
            },
        })

        conn = NextcloudConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="nc-1",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.data_source = AsyncMock()
        conn.current_user_id = "admin"
        conn.current_user_email = "admin@nc.example.com"
        conn.connector_name = "Nextcloud"
        conn.activity_sync_point = mock_sp
        conn._path_to_external_id_cache = {}
        yield conn


# ---------------------------------------------------------------------------
# run_sync decision logic
# ---------------------------------------------------------------------------

class TestNextcloudRunSync:
    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_full_sync_when_no_cursor(self, mock_filters, connector):
        connector.activity_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock) as m_full:
            await connector.run_sync()
            m_full.assert_called_once()

    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_incremental_sync_when_cursor_exists(self, mock_filters, connector):
        connector.activity_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "12345"}
        )
        with patch.object(connector, "_run_incremental_sync_internal", new_callable=AsyncMock) as m_inc:
            await connector.run_sync()
            m_inc.assert_called_once()

    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_no_user_id_returns(self, mock_filters, connector):
        connector.current_user_id = None
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock) as m_full:
            await connector.run_sync()
            m_full.assert_not_called()

    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_data_source_none_tries_init(self, mock_filters, connector):
        connector.data_source = None
        with patch.object(connector, "init", new_callable=AsyncMock, return_value=False):
            await connector.run_sync()
            # Should return without error

    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_error_propagates(self, mock_filters, connector):
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock,
                          side_effect=RuntimeError("fail")):
            connector.activity_sync_point.read_sync_point = AsyncMock(return_value=None)
            with pytest.raises(RuntimeError, match="fail"):
                await connector.run_sync()

    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters",
           new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()))
    async def test_cache_cleared_after_sync(self, mock_filters, connector):
        connector._path_to_external_id_cache = {"key": "val"}
        connector.activity_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock):
            await connector.run_sync()
        assert connector._path_to_external_id_cache == {}


# ---------------------------------------------------------------------------
# _run_full_sync_internal
# ---------------------------------------------------------------------------

class TestNextcloudFullSync:
    async def test_full_sync_creates_user_and_group(self, connector):
        with patch.object(connector, "_sync_user_files", new_callable=AsyncMock), \
             patch.object(connector, "_parse_activity_response", return_value=[]):
            resp = MagicMock()
            resp.success = True
            connector.data_source.get_activities = AsyncMock(return_value=resp)
            await connector._run_full_sync_internal()

        connector.data_entities_processor.on_new_app_users.assert_called_once()
        connector.data_entities_processor.on_new_record_groups.assert_called_once()

    async def test_full_sync_no_user_id_returns(self, connector):
        connector.current_user_id = None
        await connector._run_full_sync_internal()
        connector.data_entities_processor.on_new_app_users.assert_not_called()

    async def test_full_sync_anchors_cursor(self, connector):
        with patch.object(connector, "_sync_user_files", new_callable=AsyncMock):
            resp = MagicMock()
            resp.success = True
            connector.data_source.get_activities = AsyncMock(return_value=resp)
            with patch.object(connector, "_parse_activity_response",
                              return_value=[{"activity_id": "999"}]):
                await connector._run_full_sync_internal()
        connector.activity_sync_point.update_sync_point.assert_called()


# ---------------------------------------------------------------------------
# _run_incremental_sync_internal
# ---------------------------------------------------------------------------

class TestNextcloudIncrementalSync:
    async def test_no_data_source_returns(self, connector):
        connector.data_source = None
        await connector._run_incremental_sync_internal()
        # Should return without errors

    async def test_no_user_id_returns(self, connector):
        connector.current_user_id = None
        await connector._run_incremental_sync_internal()

    async def test_no_record_group_falls_back_to_full(self, connector):
        connector.activity_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "123"}
        )
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock) as m_full:
            await connector._run_incremental_sync_internal()
            m_full.assert_called_once()

    async def test_no_cursor_falls_back_to_full(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_group={"id": "g1"})
        connector.activity_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_run_full_sync_internal", new_callable=AsyncMock) as m_full:
            await connector._run_incremental_sync_internal()
            m_full.assert_called_once()


# ---------------------------------------------------------------------------
# Helper functions deep tests
# ---------------------------------------------------------------------------

class TestNextcloudHelperFunctions:
    def test_get_parent_path_deep_nesting(self):
        assert get_parent_path_from_path("/a/b/c/d/e.txt") == "/a/b/c/d"

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_path("/a/b/") == "/a"

    def test_get_path_depth_trailing_slash(self):
        assert get_path_depth("/a/b/c/") == 3

    def test_get_file_extension_double_dot(self):
        assert get_file_extension("file.backup.tar.gz") == "gz"

    def test_get_file_extension_single_char(self):
        assert get_file_extension("file.c") == "c"

    def test_mimetype_text_plain(self):
        result = get_mimetype_enum_for_nextcloud("text/plain", False)
        assert result == MimeTypes.PLAIN_TEXT

    def test_permissions_create_is_write(self):
        # CREATE = 4
        assert nextcloud_permissions_to_permission_type(4) == PermissionType.WRITE

    def test_permissions_read_create_is_write(self):
        # READ (1) | CREATE (4) = 5
        assert nextcloud_permissions_to_permission_type(5) == PermissionType.WRITE


# ---------------------------------------------------------------------------
# parse_webdav_propfind_response edge cases
# ---------------------------------------------------------------------------

class TestWebdavParseDeep:
    def test_multiple_entries(self):
        xml = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/a.txt</d:href>
                <d:propstat><d:prop>
                    <oc:fileid>1</oc:fileid>
                    <d:displayname>a.txt</d:displayname>
                    <d:resourcetype/>
                </d:prop></d:propstat>
            </d:response>
            <d:response>
                <d:href>/remote.php/dav/files/admin/b.txt</d:href>
                <d:propstat><d:prop>
                    <oc:fileid>2</oc:fileid>
                    <d:displayname>b.txt</d:displayname>
                    <d:resourcetype/>
                </d:prop></d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml)
        assert len(entries) == 2
        assert entries[0]["file_id"] == "1"
        assert entries[1]["file_id"] == "2"

    def test_entry_without_fileid(self):
        xml = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/x.txt</d:href>
                <d:propstat><d:prop>
                    <d:displayname>x.txt</d:displayname>
                    <d:resourcetype/>
                </d:prop></d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml)
        # Entry may still be returned (depends on implementation)
        # but should not crash
        assert isinstance(entries, list)

    def test_is_response_successful_status_201(self):
        resp = MagicMock(spec=[])
        resp.status = 201
        assert is_response_successful(resp) is True

    def test_extract_response_body_none_resp(self):
        result = extract_response_body(None)
        assert result is None
