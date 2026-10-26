"""Comprehensive tests for Nextcloud connector – targets uncovered lines."""

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.nextcloud.connector import (
    NextcloudConnector,
    NextcloudSyncError,
    extract_response_body,
    get_file_extension,
    get_mimetype_enum_for_nextcloud,
    get_parent_path_from_path,
    get_path_depth,
    get_response_error,
    is_response_successful,
    parse_webdav_propfind_response,
)
from app.models.permission import PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.nextcloud.full")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-nc-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    proc.get_file_record_by_id = AsyncMock(return_value=None)
    proc.get_record_path = AsyncMock(return_value=None)
    proc.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
    return proc


@pytest.fixture()
def mock_data_store_provider():
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
def nextcloud_connector(mock_logger, mock_data_entities_processor,
                        mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"):
        connector = NextcloudConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
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


class TestIsResponseSuccessful:
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


class TestGetResponseError:
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
        with patch("app.connectors.sources.nextcloud.connector.NextcloudClient"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource") as mock_ds:
            mock_ds_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.success = True
            mock_body = MagicMock()
            mock_body.bytes.return_value = json.dumps({
                "ocs": {"data": {"id": "admin", "email": "admin@nc.com"}}
            }).encode()
            mock_ds_instance.get_current_user = AsyncMock(return_value=mock_body)
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
            mock_ds_instance.get_current_user = AsyncMock(return_value=mock_resp)
            mock_ds.return_value = mock_ds_instance
            result = await nextcloud_connector.init()
            assert result is True
            assert nextcloud_connector.current_user_email is None, "a failed read must not invent an owner"

    @pytest.mark.asyncio
    async def test_init_user_details_exception(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"baseUrl": "https://nc.example.com", "username": "admin", "password": "pass"},
        })
        with patch("app.connectors.sources.nextcloud.connector.NextcloudRESTClientViaUsernamePassword"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudClient"), \
             patch("app.connectors.sources.nextcloud.connector.NextcloudDataSource") as mock_ds:
            mock_ds_instance = MagicMock()
            mock_ds_instance.get_current_user = AsyncMock(side_effect=Exception("network"))
            mock_ds.return_value = mock_ds_instance
            result = await nextcloud_connector.init()
            assert result is True
            assert nextcloud_connector.current_user_email is None, "a failed read must not invent an owner"

    @pytest.mark.asyncio
    async def test_init_exception(self, nextcloud_connector):
        nextcloud_connector.config_service.get_config = AsyncMock(side_effect=Exception("boom"))
        result = await nextcloud_connector.init()
        assert result is False


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_no_data_source(self, nextcloud_connector):
        nextcloud_connector.data_source = None
        with pytest.raises(ConnectorInitError, match="Could not connect"):
            await nextcloud_connector.test_connection_and_access()

    @pytest.mark.asyncio
    async def test_success_lists_the_users_home(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.data_source.list_directory = AsyncMock(return_value=MagicMock(success=True, status=207))
        nextcloud_connector.data_source.get_activities = AsyncMock(return_value=MagicMock(status=200))
        assert await nextcloud_connector.test_connection_and_access() is True
        assert nextcloud_connector.data_source.list_directory.await_args.kwargs["depth"] == 0
        assert nextcloud_connector.data_source.list_directory.await_args.kwargs["user_id"] == "admin"

    @pytest.mark.asyncio
    async def test_a_missing_activity_app_only_warns(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.data_source.list_directory = AsyncMock(return_value=MagicMock(success=True, status=207))
        nextcloud_connector.data_source.get_activities = AsyncMock(return_value=MagicMock(status=404))
        assert await nextcloud_connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.data_source.list_directory = AsyncMock(
            return_value=MagicMock(success=False, status=404, error="not found")
        )
        with pytest.raises(ConnectorInitError, match="did not let admin list their files"):
            await nextcloud_connector.test_connection_and_access()

    @pytest.mark.asyncio
    async def test_exception(self, nextcloud_connector):
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.data_source.list_directory = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(ConnectorInitError, match="Could not reach Nextcloud"):
            await nextcloud_connector.test_connection_and_access()

class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self, nextcloud_connector):
        existing = MagicMock()
        existing.id = "rec-del"
        nextcloud_connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

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
        nextcloud_connector.data_entities_processor.on_records_moved = AsyncMock()
        await nextcloud_connector._handle_record_updates(update)
        nextcloud_connector.data_entities_processor.on_records_moved.assert_awaited_once()
        nextcloud_connector.data_entities_processor.on_new_records.assert_not_awaited()

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
        nextcloud_connector._cached_date_filters = (None, None)
        assert nextcloud_connector._should_include_file({"is_collection": True}) is True

    def test_no_filter_allows_all(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        assert nextcloud_connector._should_include_file({"is_collection": False, "display_name": "test.pdf"}) is True

    def test_modified_after_filter_excludes(self, nextcloud_connector):
        cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
        nextcloud_connector._cached_date_filters = (cutoff, None)
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
        nextcloud_connector._cached_date_filters = (None, cutoff)
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
        nextcloud_connector._cached_date_filters = (cutoff, None)
        nextcloud_connector.sync_filters = MagicMock()
        nextcloud_connector.sync_filters.get = MagicMock(return_value=None)
        entry = {"is_collection": False, "display_name": "nodate.txt"}
        assert nextcloud_connector._should_include_file(entry) is False

    def test_extension_in_filter(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None)
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
        nextcloud_connector._cached_date_filters = (None, None)
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
        nextcloud_connector._cached_date_filters = (None, None)
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


    def test_invalid_filter_value_allows(self, nextcloud_connector):
        nextcloud_connector._cached_date_filters = (None, None)
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


class TestUpdatesKeepTheOldParentForTheProcessor:
    @pytest.mark.asyncio
    async def test_an_update_leaves_the_parent_edge_to_the_processor(self, nextcloud_connector):
        # The processor reads the record's old storage path from its parent edge
        # before it repoints it; deleting the edge first left the content behind.
        proc = nextcloud_connector.data_entities_processor
        proc.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
        proc.on_new_records = AsyncMock()
        update = MagicMock(is_deleted=False, is_updated=True, record=MagicMock(id="rec-1"), new_permissions=[])
        assert await nextcloud_connector._handle_record_updates(update) is True
        proc.delete_parent_child_edge_to_record.assert_not_awaited()
        proc.on_new_records.assert_awaited_once()


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_data_source_raises(self, nextcloud_connector):
        from fastapi import HTTPException
        nextcloud_connector.data_source = None
        with pytest.raises(HTTPException):
            await nextcloud_connector.stream_record(MagicMock())

    @pytest.mark.asyncio
    async def test_no_file_record_raises(self, nextcloud_connector):
        from fastapi import HTTPException
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.data_entities_processor.get_file_record_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await nextcloud_connector.stream_record(MagicMock(id="r1"))

    @pytest.mark.asyncio
    async def test_folder_raises(self, nextcloud_connector):
        from fastapi import HTTPException
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = "admin"
        file_rec = MagicMock()
        file_rec.mime_type = MimeTypes.FOLDER.value  # records store the MIME text, not the enum
        file_rec.record_name = "Documents"
        nextcloud_connector.data_entities_processor.get_file_record_by_id = AsyncMock(return_value=file_rec)
        nextcloud_connector.data_entities_processor.get_record_path = AsyncMock(return_value="/Documents")
        with pytest.raises(HTTPException, match="Cannot download folders"):
            await nextcloud_connector.stream_record(MagicMock(id="r1", record_name="Documents"))


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
        with pytest.raises(NextcloudSyncError, match="Could not connect"):
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
        nextcloud_connector._read_current_user = AsyncMock(return_value=(None, None))
        with pytest.raises(NextcloudSyncError, match="signed-in Nextcloud user"):
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
            return_value={"cursor": "100", "last_full_sync_ms": get_epoch_timestamp_in_ms()}
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
    async def test_deletes_existing_record(self, nextcloud_connector):
        existing = MagicMock()
        existing.id = "del-rec"
        existing.record_name = "deleted.txt"
        nextcloud_connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await nextcloud_connector._process_deletions({"file-123"})
        nextcloud_connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_not_found(self, nextcloud_connector):
        nextcloud_connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await nextcloud_connector._process_deletions({"nonexistent"})
        nextcloud_connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_handled(self, nextcloud_connector):
        nextcloud_connector.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=Exception("fail"))
        await nextcloud_connector._process_deletions({"bad-id"})


class TestCreateConnectorFactory:
    @pytest.mark.asyncio
    async def test_factory_method(self):
        with patch("app.connectors.sources.nextcloud.connector.NextcloudApp"):
            processor = MagicMock()
            processor.org_id = "org-1"
            logger = MagicMock()
            dsp = MagicMock()
            cs = AsyncMock()
            conn = await NextcloudConnector.create_connector(
                logger, dsp, cs, "nc-factory", "team", "test-user-id",
                data_entities_processor=processor,
            )
            assert isinstance(conn, NextcloudConnector)


class TestGetDateFilters:
    def test_no_filters(self, nextcloud_connector):
        from app.connectors.core.registry.filters import FilterCollection
        nextcloud_connector.sync_filters = FilterCollection()
        result = nextcloud_connector._get_date_filters()
        assert result == (None, None)

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
    async def test_no_connected_user_skips(self, nextcloud_connector) -> None:
        nextcloud_connector.data_entities_processor.get_file_record_by_id = AsyncMock(return_value=MagicMock())
        nextcloud_connector.data_entities_processor.get_record_path = AsyncMock(return_value="/test.txt")
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.current_user_id = None
        nextcloud_connector.current_user_email = "admin@example.com"
        record = MagicMock(id="r1", record_name="test.txt")
        await nextcloud_connector.reindex_records([record])
        nextcloud_connector.data_source.list_directory.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unread_profile_email_does_not_stop_the_reindex(self, nextcloud_connector) -> None:
        nextcloud_connector.data_entities_processor.get_file_record_by_id = AsyncMock(return_value=MagicMock())
        nextcloud_connector.data_entities_processor.get_record_path = AsyncMock(return_value="/test.txt")
        nextcloud_connector.data_source = MagicMock()
        nextcloud_connector.data_source.list_directory = AsyncMock(return_value=MagicMock(status=500))
        nextcloud_connector.current_user_id = "admin"
        nextcloud_connector.current_user_email = None
        nextcloud_connector._find_in_nextcloud_by_id = AsyncMock(return_value=(True, "/remote.php/dav/files/admin/test.txt"))
        record = MagicMock(id="r1", record_name="test.txt")
        await nextcloud_connector.reindex_records([record])
        nextcloud_connector.data_source.list_directory.assert_awaited_once()
        assert nextcloud_connector.data_source.list_directory.await_args.kwargs["path"] == "test.txt"
