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
from datetime import datetime, timezone
from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.nextcloud.connector import (
    NEXTCLOUD_PERM_MASK_ALL,
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
            scope="team",
            created_by="test-user",
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

# =============================================================================
# Merged from test_nextcloud_connector_full_coverage.py
# =============================================================================

@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.nextcloud.full")


@pytest.fixture()
def mock_data_entities_processor_fullcov():
    proc = MagicMock()
    proc.org_id = "org-nc-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider_fullcov():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_path = AsyncMock(return_value=None)
    mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    mock_tx.get_file_record_by_id = AsyncMock(return_value=None)
    mock_tx.get_record_path = AsyncMock(return_value=None)
    mock_tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
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
def nextcloud_connector(mock_logger_fullcov, mock_data_entities_processor_fullcov,
                        mock_data_store_provider_fullcov, mock_config_service):
    with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"):
        connector = NextcloudConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_data_entities_processor_fullcov,
            data_store_provider=mock_data_store_provider_fullcov,
            config_service=mock_config_service,
            connector_id="nc-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


class TestGetParentPathEdges:
    def test_trailing_slash(self):
        assert get_parent_path_from_path("/a/b/") == "/a"

    def test_none_input(self):
        assert get_parent_path_from_path(None) is None

    def test_deep_path(self):
        assert get_parent_path_from_path("/a/b/c/d/e.txt") == "/a/b/c/d"


class TestGetPathDepthEdges:
    def test_none_input(self):
        assert get_path_depth(None) == 0

    def test_trailing_slash(self):
        assert get_path_depth("/a/b/") == 2


class TestGetFileExtensionEdges:
    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_multiple_dots(self):
        assert get_file_extension("my.file.backup.tar.gz") == "gz"


class TestGetMimetypeEnumNextcloud:
    def test_known_mime(self):
        assert get_mimetype_enum_for_nextcloud("text/plain", False) == MimeTypes.PLAIN_TEXT

    def test_none_mime(self):
        assert get_mimetype_enum_for_nextcloud(None, False) == MimeTypes.BIN


class TestNextcloudPermissionsFullCoverage:
    def test_create_is_write(self):
        assert nextcloud_permissions_to_permission_type(4) == PermissionType.WRITE

    def test_share_only_is_read(self):
        assert nextcloud_permissions_to_permission_type(16) == PermissionType.READ


class TestParseWebdavResponse:
    def test_no_file_id_excluded(self):
        xml_response = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/noid.txt</d:href>
                <d:propstat>
                    <d:prop>
                        <d:displayname>noid.txt</d:displayname>
                        <d:resourcetype/>
                    </d:prop>
                </d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml_response)
        assert len(entries) == 0

    def test_content_length_parsed(self):
        xml_response = b"""<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:response>
                <d:href>/remote.php/dav/files/admin/doc.pdf</d:href>
                <d:propstat>
                    <d:prop>
                        <oc:fileid>101</oc:fileid>
                        <d:getcontentlength>2048</d:getcontentlength>
                        <d:displayname>doc.pdf</d:displayname>
                        <d:resourcetype/>
                    </d:prop>
                </d:propstat>
            </d:response>
        </d:multistatus>"""
        entries = parse_webdav_propfind_response(xml_response)
        assert entries[0]["content_length"] == 2048


class TestParseShareResponseEdges:
    def test_single_dict_data(self):
        data = {
            "ocs": {
                "meta": {},
                "data": {"share_type": 0, "share_with": "user1", "permissions": 1}
            }
        }
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert len(result) == 1

    def test_invalid_share_type(self):
        data = {
            "ocs": {
                "meta": {},
                "data": [{"share_type": "invalid", "share_with": "user1"}]
            }
        }
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert len(result) == 1
        assert "share_type" not in result[0]

    def test_permissions_out_of_range(self):
        data = {
            "ocs": {
                "meta": {},
                "data": [{"share_type": 0, "share_with": "user1", "permissions": 999}]
            }
        }
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert result[0]["permissions"] == 1

    def test_non_dict_data(self):
        data = {"ocs": {"meta": {}, "data": "not-a-list"}}
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert result == []

    def test_non_dict_items_skipped(self):
        data = {"ocs": {"meta": {}, "data": ["not-a-dict", 42]}}
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert result == []

    def test_empty_share_with_stripped(self):
        data = {
            "ocs": {
                "meta": {},
                "data": [{"share_type": 0, "share_with": "  "}]
            }
        }
        result = parse_share_response(json.dumps(data).encode("utf-8"))
        assert len(result) == 1


class TestExtractResponseBodyEdges:
    def test_response_content(self):
        resp = MagicMock(spec=[])
        inner = MagicMock()
        inner.content = b"data"
        resp.response = inner
        assert extract_response_body(resp) == b"data"

    def test_bytes_returns_none(self):
        resp = MagicMock()
        resp.bytes.return_value = None
        resp.text = MagicMock(return_value=None)
        resp.response.content = None
        result = extract_response_body(resp)
        assert result is None

    def test_bytes_exception_falls_through(self):
        resp = MagicMock()
        resp.bytes.side_effect = Exception("fail")
        resp.text = MagicMock(return_value="fallback")
        result = extract_response_body(resp)
        assert result == b"fallback"


class TestIsResponseSuccessfulFullCoverage:
    def test_success_false(self):
        resp = MagicMock()
        resp.success = False
        assert is_response_successful(resp) is False

    def test_status_code_200(self):
        resp = MagicMock(spec=[])
        resp.status_code = 200
        assert is_response_successful(resp) is True

    def test_status_code_404(self):
        resp = MagicMock(spec=[])
        resp.status_code = 404
        assert is_response_successful(resp) is False

    def test_no_known_attributes(self):
        resp = MagicMock(spec=[])
        assert is_response_successful(resp) is False


class TestGetResponseErrorFullCoverage:
    def test_status_attr(self):
        resp = MagicMock(spec=[])
        resp.status = 503
        assert "503" in get_response_error(resp)

    def test_unknown(self):
        resp = MagicMock(spec=[])
        assert "Unknown" in get_response_error(resp)


class TestConnectorInitEdges:
    @pytest.mark.asyncio
    async def test_init_no_auth_config(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": None,
        })
        result = await nextcloud_connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_base_url_from_credentials(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"username": "admin", "password": "pass"},
            "credentials": {"baseUrl": "https://nc.example.com"},
        })
        with patch("app.connectors.sources.nextcloud.connector.NextcloudRESTClientViaUsernamePassword"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudClient"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource") as mock_ds:
            mock_ds_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.success = True
            mock_body = MagicMock()
            mock_body.bytes.return_value = json.dumps({
                "ocs": {"data": {"email": "admin@nc.com"}}
            }).encode()
            mock_ds_instance.get_user_details = AsyncMock(return_value=mock_body)
            mock_ds.return_value = mock_ds_instance
            result = await nextcloud_connector.init()
            assert result is True
            assert nextcloud_connector.base_url == "https://nc.example.com"

    @pytest.mark.asyncio
    async def test_init_user_details_failure(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"baseUrl": "https://nc.example.com", "username": "admin", "password": "pass"},
        })
        with patch("app.connectors.sources.nextcloud.connector.NextcloudRESTClientViaUsernamePassword"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudClient"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource") as mock_ds:
            mock_ds_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.success = False
            mock_ds_instance.get_user_details = AsyncMock(return_value=mock_resp)
            mock_ds.return_value = mock_ds_instance
            result = await nextcloud_connector.init()
            assert result is True
            assert "nextcloud.local" in nextcloud_connector.current_user_email

    @pytest.mark.asyncio
    async def test_init_user_details_exception(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"baseUrl": "https://nc.example.com", "username": "admin", "password": "pass"},
        })
        with patch("app.connectors.sources.nextcloud.connector.NextcloudRESTClientViaUsernamePassword"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudClient"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource") as mock_ds:
            mock_ds_instance = MagicMock()
            mock_ds_instance.get_user_details = AsyncMock(side_effect=Exception("network"))
            mock_ds.return_value = mock_ds_instance
            result = await nextcloud_connector.init()
            assert result is True
            assert "nextcloud.local" in nextcloud_connector.current_user_email

    @pytest.mark.asyncio
    async def test_init_exception(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(side_effect=Exception("boom"))
        result = await nextcloud_connector.init()
        assert result is False


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_no_data_source(self, nextcloud_connector):
        nextcloud_connector.data_source = None
        assert await nextcloud_connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_success(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = True
        nextcloud_connector.data_source.get_capabilities = AsyncMock(return_value=resp)
        assert await nextcloud_connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.error = "auth error"
        nextcloud_connector.data_source.get_capabilities = AsyncMock(return_value=resp)
        assert await nextcloud_connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_exception(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.data_source.get_capabilities = AsyncMock(side_effect=Exception("fail"))
        assert await nextcloud_connector.test_connection_and_access() is False


class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self, nextcloud_connector, mock_data_store_provider_fullcov):
        existing = MagicMock()
        existing.id = "rec-del"
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="ext-del",
        )
        await nextcloud_connector._handle_record_updates(update)
        nextcloud_connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updated_record(self, nextcloud_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
            new_permissions=[MagicMock()],
        )
        await nextcloud_connector._handle_record_updates(update)
        nextcloud_connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_handled(self, nextcloud_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        nextcloud_connector.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("fail"))
        update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        await nextcloud_connector._handle_record_updates(update)


class TestBuildPathToExternalIdMap:
    @pytest.mark.asyncio
    async def test_maps_entries(self, nextcloud_connector):
        entries = [
            {"file_id": "10", "path": "/remote.php/dav/files/admin/docs"},
            {"file_id": "20", "path": "/remote.php/dav/files/admin/images"},
            {"file_id": None, "path": "/remote.php/dav/files/admin/bad"},
        ]
        result = await nextcloud_connector._build_path_to_external_id_map(entries)
        assert result["/remote.php/dav/files/admin/docs"] == "10"
        assert "/remote.php/dav/files/admin/bad" not in result


class TestSortEntriesByHierarchy:
    def test_empty_list(self, nextcloud_connector):
        assert nextcloud_connector._sort_entries_by_hierarchy([]) == []

    def test_deep_hierarchy(self, nextcloud_connector):
        entries = [
            {"path": "/a/b/c/file.txt", "is_collection": False},
            {"path": "/a", "is_collection": True},
            {"path": "/a/b", "is_collection": True},
            {"path": "/a/file.txt", "is_collection": False},
        ]
        sorted_entries = nextcloud_connector._sort_entries_by_hierarchy(entries)
        assert sorted_entries[0]["path"] == "/a"
        assert sorted_entries[1]["path"] == "/a/b"
        assert sorted_entries[-1]["path"] == "/a/b/c/file.txt"


class TestShouldIncludeFile:
    def test_collection_always_included(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        assert nextcloud_connector._should_include_file({"is_collection": True}) is True

    def test_no_filter_allows_all(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        assert nextcloud_connector._should_include_file({"is_collection": False, "display_name": "test.pdf"}) is True

    def test_modified_after_filter_excludes(self, nextcloud_connector):
        cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
        nextcloud_connector._cached_date_filters = (cutoff, None, None, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        entry = {
            "is_collection": False,
            "display_name": "old.txt",
            "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        }
        assert nextcloud_connector._should_include_file(entry) is False

    def test_modified_before_filter_excludes(self, nextcloud_connector):
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        nextcloud_connector._cached_date_filters = (None, cutoff, None, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        entry = {
            "is_collection": False,
            "display_name": "new.txt",
            "last_modified": "Wed, 01 Jun 2025 00:00:00 GMT",
        }
        assert nextcloud_connector._should_include_file(entry) is False

    def test_no_modified_date_with_filter_excludes(self, nextcloud_connector):
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        nextcloud_connector._cached_date_filters = (cutoff, None, None, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        entry = {"is_collection": False, "display_name": "nodate.txt"}
        assert nextcloud_connector._should_include_file(entry) is False

    def test_extension_in_filter(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf", "docx"]
        filt.get_operator = MagicMock(return_value=FilterOperator.IN)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(
            side_effect=lambda key: filt if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "file.pdf", "path": "/file.pdf"}
        ) is True
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "file.txt", "path": "/file.txt"}
        ) is False

    def test_extension_not_in_filter(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["exe", "bat"]
        filt.get_operator = MagicMock(return_value=FilterOperator.NOT_IN)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(
            side_effect=lambda key: filt if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "file.pdf", "path": "/file.pdf"}
        ) is True

    def test_file_without_extension_in_operator(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf"]
        filt.get_operator = MagicMock(return_value=FilterOperator.NOT_IN)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(
            side_effect=lambda key: filt if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "Makefile", "path": "/Makefile"}
        ) is True

    def test_created_date_filter_warning(self, nextcloud_connector):
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        nextcloud_connector._cached_date_filters = (None, None, cutoff, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "file.txt", "last_modified": "Wed, 01 Jun 2025 00:00:00 GMT"}
        ) is True

    def test_invalid_filter_value_allows(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None, None, None)
        from app.connectors.core.registry.filters import SyncFilterKey
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = "not-a-list"
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(
            side_effect=lambda key: filt if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        assert nextcloud_connector._should_include_file(
            {"is_collection": False, "display_name": "file.pdf", "path": "/file.pdf"}
        ) is True


class TestClearParentChildEdges:
    @pytest.mark.asyncio
    async def test_empty_list(self, nextcloud_connector):
        await nextcloud_connector._clear_parent_child_edges_for_records([])

    @pytest.mark.asyncio
    async def test_deletes_edges(self, nextcloud_connector, mock_data_store_provider_fullcov):
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
        record = MagicMock()
        record.id = "rec-1"
        await nextcloud_connector._clear_parent_child_edges_for_records([(record, [])])
        mock_tx.delete_parent_child_edge_to_record.assert_awaited_once()


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_data_source_raises(self, nextcloud_connector):
        from fastapi import HTTPException
        nextcloud_connector.data_source = None
        with pytest.raises(HTTPException):
            await nextcloud_connector.stream_record(MagicMock())

    @pytest.mark.asyncio
    async def test_no_file_record_raises(self, nextcloud_connector, mock_data_store_provider_fullcov):
        from fastapi import HTTPException
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_file_record_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await nextcloud_connector.stream_record(MagicMock(id="r1"))

    @pytest.mark.asyncio
    async def test_folder_raises(self, nextcloud_connector, mock_data_store_provider_fullcov):
        from fastapi import HTTPException
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        file_rec = MagicMock()
        file_rec.mime_type = MimeTypes.FOLDER
        file_rec.record_name = "Documents"
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_file_record_by_id = AsyncMock(return_value=file_rec)
        mock_tx.get_record_path = AsyncMock(return_value="/Documents")
        with pytest.raises(HTTPException, match="Cannot download folders"):
            await nextcloud_connector.stream_record(MagicMock(id="r1", record_name="Documents"))


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_cache(self, nextcloud_connector):
        nextcloud_connector._path_to_external_id_cache = {"/a": "1"}
        nextcloud_connector.data_source = MagicMock()
        await nextcloud_connector.cleanup()
        assert nextcloud_connector.data_source is None
        assert nextcloud_connector._path_to_external_id_cache == {}

    @pytest.mark.asyncio
    async def test_cleanup_with_messaging_producer(self, nextcloud_connector):
        nextcloud_connector._path_to_external_id_cache = {}
        nextcloud_connector.data_source = MagicMock()
        producer = MagicMock()
        producer.cleanup = AsyncMock()
        nextcloud_connector.data_entities_processor.messaging_producer = producer
        await nextcloud_connector.cleanup()
        producer.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_with_stop_producer(self, nextcloud_connector):
        nextcloud_connector._path_to_external_id_cache = {}
        nextcloud_connector.data_source = MagicMock()
        producer = MagicMock(spec=["stop"])
        producer.stop = AsyncMock()
        nextcloud_connector.data_entities_processor.messaging_producer = producer
        await nextcloud_connector.cleanup()
        producer.stop.assert_awaited_once()


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, nextcloud_connector):
        with pytest.raises(NotImplementedError):
            await nextcloud_connector.get_filter_options("key")


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_none(self, nextcloud_connector):
        result = await nextcloud_connector.get_signed_url(MagicMock())
        assert result is None


class TestHandleWebhookNotification:
    def test_logs_warning(self, nextcloud_connector):
        nextcloud_connector.handle_webhook_notification({})


class TestRunSyncDecision:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_data_source_attempts_init(self, mock_filters, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        nextcloud_connector.data_source = None
        nextcloud_connector.init = AsyncMock(return_value=False)
        await nextcloud_connector.run_sync()
        nextcloud_connector.init.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_user_info_returns(self, mock_filters, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = None
        nextcloud_connector.current_user_email = None
        await nextcloud_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_sync_with_cursor(self, mock_filters, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.current_user_email = "admin@nc.com"
        nextcloud_connector.activity_sync_point = MagicMock()
        nextcloud_connector.activity_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "100"}
        )
        nextcloud_connector._run_incremental_sync_internal = AsyncMock()
        await nextcloud_connector.run_sync()
        nextcloud_connector._run_incremental_sync_internal.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.nextcloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_full_sync_no_cursor(self, mock_filters, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.current_user_email = "admin@nc.com"
        nextcloud_connector.activity_sync_point = MagicMock()
        nextcloud_connector.activity_sync_point.read_sync_point = AsyncMock(return_value=None)
        nextcloud_connector._run_full_sync_internal = AsyncMock()
        await nextcloud_connector.run_sync()
        nextcloud_connector._run_full_sync_internal.assert_awaited_once()


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_public_method_delegates(self, nextcloud_connector):
        nextcloud_connector._run_incremental_sync_internal = AsyncMock()
        await nextcloud_connector.run_incremental_sync()
        nextcloud_connector._run_incremental_sync_internal.assert_awaited_once()


class TestParseActivityResponse:
    def test_valid_response(self, nextcloud_connector):
        resp = MagicMock()
        resp.bytes = MagicMock(return_value=json.dumps({
            "ocs": {
                "data": [
                    {"activity_id": 1, "type": "file_created", "object_type": "files",
                     "object_id": 10, "object_name": "/test.txt", "datetime": "2025-01-01", "subject": "created"},
                ]
            }
        }).encode())
        activities = nextcloud_connector._parse_activity_response(resp)
        assert len(activities) == 1
        assert activities[0]["activity_id"] == 1

    def test_empty_body(self, nextcloud_connector):
        resp = MagicMock()
        resp.bytes = MagicMock(return_value=None)
        resp.text = MagicMock(return_value=None)
        resp.response = MagicMock()
        resp.response.content = None
        activities = nextcloud_connector._parse_activity_response(resp)
        assert activities == []

    def test_invalid_json(self, nextcloud_connector):
        resp = MagicMock()
        resp.bytes = MagicMock(return_value=b"not json")
        activities = nextcloud_connector._parse_activity_response(resp)
        assert activities == []

    def test_non_dict_items_skipped(self, nextcloud_connector):
        resp = MagicMock()
        resp.bytes = MagicMock(return_value=json.dumps({
            "ocs": {"data": ["not-a-dict", 42]}
        }).encode())
        activities = nextcloud_connector._parse_activity_response(resp)
        assert activities == []


class TestProcessDeletions:
    @pytest.mark.asyncio
    async def test_deletes_existing_record(self, nextcloud_connector, mock_data_store_provider_fullcov):
        existing = MagicMock()
        existing.id = "del-rec"
        existing.record_name = "deleted.txt"
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        await nextcloud_connector._process_deletions({"file-123"})
        nextcloud_connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_not_found(self, nextcloud_connector, mock_data_store_provider_fullcov):
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        await nextcloud_connector._process_deletions({"nonexistent"})
        nextcloud_connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_handled(self, nextcloud_connector, mock_data_store_provider_fullcov):
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        await nextcloud_connector._process_deletions({"bad-id"})


class TestCreateConnectorFactory:
    @pytest.mark.asyncio
    async def test_factory_method(self):
        with patch("app.connectors.sources.nextcloud.connector.DataSourceEntitiesProcessor") as mock_dep, \
             patch("app.connectors.sources.nextcloud.connector.NextcloudApp"):
            mock_dep_instance = MagicMock()
            mock_dep_instance.initialize = AsyncMock()
            mock_dep_instance.org_id = "org-1"
            mock_dep.return_value = mock_dep_instance
            logger = MagicMock()
            dsp = MagicMock()
            cs = AsyncMock()
            conn = await NextcloudConnector.create_connector(logger, dsp, cs, "nc-factory")
            assert isinstance(conn, NextcloudConnector)


class TestGetDateFilters:
    def test_no_filters(self, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        nextcloud_connector.sync_filters = FilterCollection()
        result = nextcloud_connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_modified_date_filter(self, nextcloud_connector):
        from app.connectors.core.registry.filters import SyncFilterKey
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.get_datetime_iso = MagicMock(return_value=("2025-01-01T00:00:00", "2025-12-31T23:59:59"))
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(
            side_effect=lambda key: filt if key == SyncFilterKey.MODIFIED else None
        )
        result = nextcloud_connector._get_date_filters()
        assert result[0] is not None
        assert result[1] is not None


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, nextcloud_connector):
        await nextcloud_connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_user_permission_skips(self, nextcloud_connector, mock_data_store_provider_fullcov):
        mock_tx = mock_data_store_provider_fullcov.transaction.return_value
        mock_tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
        mock_tx.get_file_record_by_id = AsyncMock(return_value=MagicMock())
        mock_tx.get_record_path = AsyncMock(return_value="/test.txt")
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        record = MagicMock(id="r1", record_name="test.txt")
        await nextcloud_connector.reindex_records([record])
