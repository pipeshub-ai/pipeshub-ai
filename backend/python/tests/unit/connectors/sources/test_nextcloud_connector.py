"""Tests for Nextcloud connector."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.nextcloud.connector import (
    NextcloudConnector,
    extract_response_body,
    get_file_extension,
    get_mimetype_enum_for_nextcloud,
    get_parent_path_from_path,
    get_path_depth,
    get_response_error,
    is_response_successful,
    nextcloud_permissions_to_permission_type,
    parse_share_response,
    parse_webdav_propfind_response,
)
from app.models.permission import PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.nextcloud")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-nc-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "baseUrl": "https://nextcloud.example.com",
            "username": "admin",
            "password": "app-password-123",
        },
    })
    return svc


@pytest.fixture()
def nextcloud_connector(mock_logger, mock_data_entities_processor,
                        mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"):
        connector = NextcloudConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="nc-conn-1",
        )
    return connector


# ===========================================================================
# Helper functions
# ===========================================================================

class TestNextcloudGetParentPath:
    def test_root(self):
        assert get_parent_path_from_path("/") is None

    def test_empty(self):
        assert get_parent_path_from_path("") is None

    def test_nested(self):
        assert get_parent_path_from_path("/a/b/c.txt") == "/a/b"

    def test_single_level(self):
        assert get_parent_path_from_path("/file.txt") is None


class TestGetPathDepth:
    def test_root(self):
        assert get_path_depth("/") == 0

    def test_empty(self):
        assert get_path_depth("") == 0

    def test_one_level(self):
        assert get_path_depth("/docs") == 1

    def test_multiple_levels(self):
        assert get_path_depth("/a/b/c/d") == 4


class TestNextcloudGetFileExtension:
    def test_normal(self):
        assert get_file_extension("file.pdf") == "pdf"

    def test_no_ext(self):
        assert get_file_extension("Makefile") is None

    def test_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"


class TestNextcloudMimeType:
    def test_collection(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_nextcloud("", True) == MimeTypes.FOLDER

    def test_pdf(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_nextcloud("application/pdf", False) == MimeTypes.PDF

    def test_unknown(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_nextcloud("application/x-custom-unknown", False) == MimeTypes.BIN

    def test_empty(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_nextcloud("", False) == MimeTypes.BIN


class TestNextcloudPermissions:
    def test_all_permissions_is_owner(self):
        assert nextcloud_permissions_to_permission_type(31) == PermissionType.OWNER

    def test_delete_is_write(self):
        assert nextcloud_permissions_to_permission_type(8) == PermissionType.WRITE

    def test_update_is_write(self):
        assert nextcloud_permissions_to_permission_type(2) == PermissionType.WRITE

    def test_read_only(self):
        assert nextcloud_permissions_to_permission_type(1) == PermissionType.READ

    def test_zero_is_read(self):
        assert nextcloud_permissions_to_permission_type(0) == PermissionType.READ


class TestParseWebdavPropfindResponse:
    def test_valid_xml(self):
        xml_response = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/test.txt</d:href>
                <d:propstat>
                    <d:prop>
                        <d:getlastmodified>Wed, 01 Jan 2025 00:00:00 GMT</d:getlastmodified>
                        <d:getetag>"abc123"</d:getetag>
                        <d:getcontenttype>text/plain</d:getcontenttype>
                        <oc:fileid>42</oc:fileid>
                        <oc:permissions>RDNVW</oc:permissions>
                        <oc:size>1024</oc:size>
                        <d:displayname>test.txt</d:displayname>
                        <d:resourcetype/>
                    </d:prop>
                </d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml_response)
        assert len(entries) == 1
        assert entries[0]["file_id"] == "42"
        assert entries[0]["is_collection"] is False

    def test_folder_entry(self):
        xml_response = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/Documents/</d:href>
                <d:propstat>
                    <d:prop>
                        <oc:fileid>99</oc:fileid>
                        <d:displayname>Documents</d:displayname>
                        <d:resourcetype><d:collection/></d:resourcetype>
                    </d:prop>
                </d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml_response)
        assert len(entries) == 1
        assert entries[0]["is_collection"] is True

    def test_invalid_xml_returns_empty(self):
        entries = parse_webdav_propfind_response(b"not valid xml")
        assert entries == []

    def test_empty_response_returns_empty(self):
        entries = parse_webdav_propfind_response(b"")
        assert entries == []


class TestParseShareResponse:
    def test_valid_shares(self):
        data = {
            "ocs": {
                "meta": {"status": "ok"},
                "data": [
                    {"share_type": 0, "share_with": "user1", "permissions": 1},
                    {"share_type": 1, "share_with": "group1", "permissions": 31},
                ]
            }
        }
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert len(result) == 2

    def test_empty_data(self):
        data = {"ocs": {"meta": {}, "data": []}}
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert result == []

    def test_invalid_json(self):
        result = parse_share_response(b"not json")
        assert result == []


class TestExtractResponseBody:
    def test_bytes_method(self):
        resp = MagicMock()
        resp.bytes.return_value = b"hello"
        assert extract_response_body(resp) == b"hello"

    def test_text_method(self):
        resp = MagicMock(spec=[])
        resp.text = MagicMock(return_value="hello")
        assert extract_response_body(resp) == b"hello"

    def test_no_method_returns_none(self):
        resp = MagicMock(spec=[])
        assert extract_response_body(resp) is None


class TestIsResponseSuccessful:
    def test_success_attribute(self):
        resp = MagicMock()
        resp.success = True
        assert is_response_successful(resp) is True

    def test_status_200(self):
        resp = MagicMock(spec=[])
        resp.status = 200
        assert is_response_successful(resp) is True

    def test_status_404(self):
        resp = MagicMock(spec=[])
        resp.status = 404
        assert is_response_successful(resp) is False


class TestGetResponseError:
    def test_error_attribute(self):
        resp = MagicMock()
        resp.error = "Something went wrong"
        assert "Something went wrong" in get_response_error(resp)

    def test_status_code(self):
        resp = MagicMock(spec=[])
        resp.status_code = 500
        assert "500" in get_response_error(resp)


# ===========================================================================
# NextcloudConnector
# ===========================================================================

class TestNextcloudConnectorInit:
    def test_constructor(self, nextcloud_connector):
        assert nextcloud_connector.connector_id == "nc-conn-1"
        assert nextcloud_connector.data_source is None

    @patch("app.connectors.sources.nextcloud.connector.NextcloudClient")
    @patch("app.connectors.sources.nextcloud.connector.NextcloudRESTClientViaUsernamePassword")
    @patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource")
    async def test_init_success(self, mock_ds_cls, mock_rest_client, mock_client,
                                nextcloud_connector):
        mock_ds = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_body = MagicMock()
        mock_body.bytes.return_value = json.dumps({
            "ocs": {"data": {"email": "admin@test.com"}}
        }).encode()
        mock_ds.get_user_details = AsyncMock(return_value=mock_body)
        mock_ds_cls.return_value = mock_ds

        result = await nextcloud_connector.init()
        assert result is True
        assert nextcloud_connector.current_user_id == "admin"

    async def test_init_fails_no_config(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await nextcloud_connector.init()
        assert result is False

    async def test_init_fails_no_base_url(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"username": "admin", "password": "pass"}
        })
        result = await nextcloud_connector.init()
        assert result is False

    async def test_init_fails_no_credentials(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"baseUrl": "https://nc.example.com"}
        })
        result = await nextcloud_connector.init()
        assert result is False


class TestNextcloudSortHierarchy:
    def test_sorts_folders_before_files(self, nextcloud_connector):
        entries = [
            {"path": "/docs/file.txt", "is_collection": False},
            {"path": "/docs", "is_collection": True},
            {"path": "/images/photo.jpg", "is_collection": False},
            {"path": "/images", "is_collection": True},
        ]
        sorted_entries = nextcloud_connector._sort_entries_by_hierarchy(entries)
        # Folders should come first
        assert sorted_entries[0]["is_collection"] is True
        assert sorted_entries[1]["is_collection"] is True
        assert sorted_entries[2]["is_collection"] is False
        assert sorted_entries[3]["is_collection"] is False
