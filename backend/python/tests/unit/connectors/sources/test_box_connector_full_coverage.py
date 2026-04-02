"""Full coverage tests for Box connector."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
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
from app.models.entities import AppUser, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


def _make_box_entry(entry_type="file", entry_id="f1", name="doc.pdf",
                    size=1024, created_at="2024-01-15T10:30:00Z",
                    modified_at="2024-06-15T10:30:00Z", path_parts=None,
                    shared_link=None, owned_by=None, etag="etag1", sha1="sha1hash"):
    if path_parts is None:
        path_parts = [{"id": "0", "name": "All Files"}, {"id": "p1", "name": "Folder"}]
    entry = {
        "type": entry_type, "id": entry_id, "name": name,
        "size": size, "created_at": created_at, "modified_at": modified_at,
        "path_collection": {"entries": path_parts},
        "etag": etag, "sha1": sha1,
    }
    if shared_link:
        entry["shared_link"] = shared_link
    if owned_by:
        entry["owned_by"] = owned_by
    return entry


def _make_mock_tx_store(existing_record=None, record_group=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=record_group)
    tx.create_record_group_relation = AsyncMock()
    tx.get_app_user_by_email = AsyncMock(return_value=None)
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.remove_user_access_to_record = AsyncMock()
    tx.get_app_users = AsyncMock(return_value=[])
    return tx


def _make_mock_data_store_provider(existing_record=None, record_group=None):
    tx = _make_mock_tx_store(existing_record, record_group)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.box.full")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-box-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.on_user_group_deleted = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[MagicMock(email="user@test.com")])
    proc.get_all_app_users = AsyncMock(return_value=[])
    proc.reindex_existing_records = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "clientId": "box-client-id",
            "clientSecret": "box-client-secret",
            "enterpriseId": "box-ent-123",
        },
    })
    return svc


@pytest.fixture()
def box_connector(mock_logger, mock_data_entities_processor,
                  mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.box.connector.BoxApp"):
        connector = BoxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="box-conn-1",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    connector.box_cursor_sync_point = AsyncMock()
    connector.box_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.box_cursor_sync_point.update_sync_point = AsyncMock()
    return connector


class TestGetParentPathExtended:
    def test_double_slash(self):
        result = get_parent_path_from_path("//a/b")
        assert result is not None

    def test_trailing_slash(self):
        result = get_parent_path_from_path("/a/b/")
        assert result is not None


class TestGetFileExtensionExtended:
    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_dot_only(self):
        result = get_file_extension("file.")
        assert result == ""


class TestGetMimeTypeExtended:
    def test_docx(self):
        result = get_mimetype_enum_for_box("file", "report.docx")
        assert result is not None

    def test_txt(self):
        result = get_mimetype_enum_for_box("file", "readme.txt")
        assert result is not None

    def test_unknown_type(self):
        result = get_mimetype_enum_for_box("web_link", "something")
        assert result == MimeTypes.BIN


class TestBoxRunIncrementalSync:
    async def test_incremental_sync_no_events(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-123"}
        )
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=True, data={
                "entries": [], "next_stream_position": "pos-124"
            })
        )
        box_connector._to_dict = MagicMock(return_value={
            "entries": [], "next_stream_position": "pos-124"
        })

        await box_connector.run_incremental_sync()
        box_connector._sync_users.assert_awaited_once()

    async def test_incremental_sync_with_events(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[
            MagicMock(source_user_id="u1")
        ])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )

        event = {
            "event_id": "e1", "event_type": "ITEM_UPLOAD",
            "source": {"id": "f1", "type": "file", "owned_by": {"id": "u1"}},
            "created_at": "2024-01-01T00:00:00Z",
        }
        box_connector.data_source.events_get_events = AsyncMock(side_effect=[
            MagicMock(success=True, data={
                "entries": [event], "next_stream_position": "pos-200"
            }),
            MagicMock(success=True, data={
                "entries": [], "next_stream_position": "pos-200"
            }),
        ])
        call_count = [0]

        def _to_dict_side(data):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"entries": [event], "next_stream_position": "pos-200"}
            return {"entries": [], "next_stream_position": "pos-200"}

        box_connector._to_dict = MagicMock(side_effect=_to_dict_side)
        box_connector._process_event_batch = AsyncMock()

        await box_connector.run_incremental_sync()
        box_connector._process_event_batch.assert_awaited_once()

    async def test_incremental_sync_api_failure(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )
        box_connector.data_source.events_get_events = AsyncMock(
            return_value=MagicMock(success=False, error="API failure")
        )

        await box_connector.run_incremental_sync()

    async def test_incremental_sync_stream_expired(self, box_connector):
        box_connector._sync_users = AsyncMock(return_value=[])
        box_connector._ensure_virtual_groups = AsyncMock()
        box_connector._sync_user_groups = AsyncMock()
        box_connector.box_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "pos-100"}
        )
        box_connector.data_source.events_get_events = AsyncMock(side_effect=[
            MagicMock(success=False, error="stream_position expired"),
            MagicMock(success=True, data={"entries": [], "next_stream_position": "new-pos"}),
        ])
        call_count = [0]

        def _to_dict_side(data):
            call_count[0] += 1
            return {"entries": [], "next_stream_position": "new-pos"}

        box_connector._to_dict = MagicMock(side_effect=_to_dict_side)

        await box_connector.run_incremental_sync()


class TestBoxProcessEventBatch:
    async def test_deletion_event(self, box_connector):
        events = [{
            "event_id": "e1", "event_type": "ITEM_TRASH",
            "source": {"id": "f1", "type": "file"},
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector._execute_deletions = AsyncMock()
        await box_connector._process_event_batch(events)
        box_connector._execute_deletions.assert_awaited_once()

    async def test_collaboration_grant_event(self, box_connector):
        events = [{
            "event_id": "e2", "event_type": "COLLABORATION_CREATED",
            "source": {
                "item": {"id": "f1", "type": "file"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
                "owned_by": {"id": "owner1"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[])
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()
        await box_connector._process_event_batch(events, our_org_box_user_ids={"owner1"})
        box_connector._fetch_and_sync_files_for_owner.assert_awaited_once()

    async def test_collaboration_revocation_event(self, box_connector):
        events = [{
            "event_id": "e3", "event_type": "COLLABORATION_REMOVE",
            "source": {
                "item": {"id": "f1"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        mock_user = MagicMock()
        mock_user.id = "internal-1"
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[mock_user])

        existing_record = MagicMock()
        existing_record.mime_type = "application/pdf"
        tx = _make_mock_tx_store(existing_record=existing_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        await box_connector._process_event_batch(events)

    async def test_duplicate_event_skipped(self, box_connector):
        event = {
            "event_id": "dup1", "event_type": "ITEM_UPLOAD",
            "source": {"id": "f1", "type": "file", "owned_by": {"id": "u1"}},
            "created_at": "2024-01-01T00:00:00Z",
        }
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()
        await box_connector._process_event_batch([event, event])

    async def test_event_without_source_skipped(self, box_connector):
        events = [{
            "event_id": "e5", "event_type": "ITEM_UPLOAD",
            "source": None,
            "created_at": "2024-01-01T00:00:00Z",
        }]
        await box_connector._process_event_batch(events)

    async def test_collaboration_grant_fetches_email_from_box(self, box_connector):
        events = [{
            "event_id": "e6", "event_type": "COLLABORATION_CREATED",
            "source": {
                "item": {"id": "f2", "type": "file"},
                "accessible_by": {"id": "u2"},
                "owned_by": {"id": "owner2"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        box_connector.data_source.users_get_user_by_id = AsyncMock(
            return_value=MagicMock(success=True, data={"login": "u2@test.com"})
        )
        box_connector._to_dict = MagicMock(return_value={"login": "u2@test.com"})
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[])
        box_connector._fetch_and_sync_files_for_owner = AsyncMock()

        await box_connector._process_event_batch(events, our_org_box_user_ids={"owner2"})

    async def test_revocation_folder_recursion(self, box_connector):
        events = [{
            "event_id": "e7", "event_type": "COLLABORATION_REMOVE",
            "source": {
                "item": {"id": "folder1"},
                "accessible_by": {"id": "u1", "login": "user@test.com"},
            },
            "created_at": "2024-01-01T00:00:00Z",
        }]
        mock_user = MagicMock()
        mock_user.id = "internal-1"
        box_connector._get_app_users_by_emails = AsyncMock(return_value=[mock_user])

        folder_record = MagicMock()
        folder_record.mime_type = MimeTypes.FOLDER.value
        tx = _make_mock_tx_store(existing_record=folder_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction
        box_connector._remove_user_access_from_folder_recursively = AsyncMock()

        await box_connector._process_event_batch(events)
        box_connector._remove_user_access_from_folder_recursively.assert_awaited_once()


class TestBoxFetchAndSyncFilesForOwner:
    async def test_successful_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._ensure_parent_folders_exist = AsyncMock()

        await box_connector._fetch_and_sync_files_for_owner("owner1", ["f1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited_once()
        box_connector.data_source.clear_as_user_context.assert_awaited()

    async def test_exception_clears_context(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock(
            side_effect=Exception("context error")
        )
        box_connector.data_source.clear_as_user_context = AsyncMock()
        await box_connector._fetch_and_sync_files_for_owner("owner1", ["f1"])
        box_connector.data_source.clear_as_user_context.assert_awaited()


class TestBoxFetchAndSyncFoldersForOwner:
    async def test_successful_folder_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._sync_folder_contents_recursively = AsyncMock()

        await box_connector._fetch_and_sync_folders_for_owner("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_folder_fetch_failure(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=False, error="403")
        )
        await box_connector._fetch_and_sync_folders_for_owner("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()


class TestBoxSyncFolderContentsRecursively:
    async def test_empty_folder(self, box_connector):
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(return_value={"entries": [], "total_count": 0})
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert batch == []

    async def test_with_items(self, box_connector):
        entry = _make_box_entry()
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(
            return_value={"entries": [entry], "total_count": 1}
        )
        mock_record = MagicMock()
        mock_record.mime_type = "application/pdf"
        mock_record.external_record_id = "f1"
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=mock_record, new_permissions=[]
        ))
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert len(batch) == 1

    async def test_api_failure(self, box_connector):
        box_connector.data_source.folders_get_folder_items = AsyncMock(
            return_value=MagicMock(success=False, error="API error")
        )
        batch = []
        await box_connector._sync_folder_contents_recursively("owner1", "d1", batch)
        assert batch == []


class TestBoxEnsureParentFoldersExist:
    async def test_folder_already_exists(self, box_connector):
        existing = MagicMock()
        tx = _make_mock_tx_store(existing_record=existing)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        await box_connector._ensure_parent_folders_exist("owner1", ["f1"])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_folder_not_exists_creates(self, box_connector):
        tx = _make_mock_tx_store(existing_record=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        box_connector.data_store_provider = MagicMock()
        box_connector.data_store_provider.transaction = _transaction

        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data={})
        )
        box_connector._to_dict = MagicMock(return_value={
            "type": "folder", "id": "d1", "name": "Folder",
            "path_collection": {"entries": []},
        })
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))

        await box_connector._ensure_parent_folders_exist("owner1", ["d1"])
        box_connector.data_entities_processor.on_new_records.assert_awaited()


class TestBoxExecuteDeletions:
    async def test_empty_list(self, box_connector):
        await box_connector._execute_deletions([])

    async def test_with_ids(self, box_connector):
        await box_connector._execute_deletions(["f1", "f2"])


class TestBoxGetSignedUrl:
    async def test_no_data_source(self, box_connector):
        box_connector.data_source = None
        result = await box_connector.get_signed_url(MagicMock())
        assert result is None

    async def test_success(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.record_name = "doc.pdf"
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=True, data="https://download.box.com/file")
        )
        result = await box_connector.get_signed_url(record)
        assert result == "https://download.box.com/file"
        box_connector.data_source.clear_as_user_context.assert_awaited()

    async def test_failure(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.record_name = "doc.pdf"
        record.id = "r1"
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=False, error="denied")
        )
        result = await box_connector.get_signed_url(record)
        assert result is None

    async def test_exception(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = "u1"
        record.id = "r1"
        box_connector.data_source.set_as_user_context = AsyncMock(
            side_effect=Exception("error")
        )
        box_connector.data_source.clear_as_user_context = AsyncMock()
        result = await box_connector.get_signed_url(record)
        assert result is None

    async def test_no_context_user_id(self, box_connector):
        record = MagicMock()
        record.external_record_id = "f1"
        record.external_record_group_id = None
        record.record_name = "doc.pdf"
        box_connector.data_source.downloads_get_download_file_url = AsyncMock(
            return_value=MagicMock(success=True, data="https://url.com")
        )
        result = await box_connector.get_signed_url(record)
        assert result == "https://url.com"


class TestBoxStreamRecord:
    async def test_no_signed_url_raises(self, box_connector):
        box_connector.get_signed_url = AsyncMock(return_value=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await box_connector.stream_record(MagicMock())

    @patch("app.connectors.sources.box.connector.create_stream_record_response")
    @patch("app.connectors.sources.box.connector.stream_content")
    async def test_success(self, mock_stream, mock_response, box_connector):
        box_connector.get_signed_url = AsyncMock(return_value="https://url.com")
        mock_response.return_value = MagicMock()
        record = MagicMock()
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"
        record.id = "r1"
        result = await box_connector.stream_record(record)
        assert result is not None


class TestBoxTestConnection:
    async def test_no_data_source(self, box_connector):
        box_connector.data_source = None
        assert await box_connector.test_connection_and_access() is False

    async def test_success(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=True)
        )
        assert await box_connector.test_connection_and_access() is True

    async def test_failure(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            return_value=MagicMock(success=False)
        )
        assert await box_connector.test_connection_and_access() is False

    async def test_exception(self, box_connector):
        box_connector.data_source.get_current_user = AsyncMock(
            side_effect=Exception("network error")
        )
        assert await box_connector.test_connection_and_access() is False


class TestBoxHandleWebhook:
    def test_triggers_incremental_sync(self, box_connector):
        with patch("asyncio.create_task"):
            box_connector.handle_webhook_notification({"trigger": "FILE.UPLOADED"})


class TestBoxCleanup:
    async def test_cleanup(self, box_connector):
        box_connector.data_source = MagicMock()
        await box_connector.cleanup()
        assert box_connector.data_source is None


class TestBoxReindexRecords:
    async def test_empty(self, box_connector):
        await box_connector.reindex_records([])
        box_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_with_records(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "f1"
        record.mime_type = "application/pdf"
        record.record_name = "doc.pdf"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[], is_updated=True, is_new=False
        ))

        await box_connector.reindex_records([record])
        box_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_folder_record(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "d1"
        record.mime_type = MimeTypes.FOLDER.value
        record.record_name = "MyFolder"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[], is_updated=False, is_new=False
        ))
        box_connector.data_entities_processor.reindex_existing_records = AsyncMock()

        await box_connector.reindex_records([record])
        box_connector.data_entities_processor.reindex_existing_records.assert_awaited()

    async def test_reindex_api_failure(self, box_connector):
        record = MagicMock()
        record.external_record_group_id = "u1"
        record.external_record_id = "f1"
        record.mime_type = "application/pdf"
        record.record_name = "doc.pdf"

        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=False, error="gone")
        )

        await box_connector.reindex_records([record])


class TestBoxGetDateFilters:
    def test_empty_filters(self, box_connector):
        result = box_connector._get_date_filters()
        assert all(v is None for v in result)

    def test_with_modified_filter(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2025-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00"
        )
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        result = box_connector._get_date_filters()
        assert result[0] is not None
        assert result[1] is not None

    def test_with_created_filter(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2025-01-01T00:00:00+00:00", None
        )
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        result = box_connector._get_date_filters()
        assert result[2] is not None


class TestBoxShouldIncludeFile:
    def test_non_file_always_included(self, box_connector):
        entry = {"type": "folder", "name": "test"}
        assert box_connector._should_include_file(entry) is True

    def test_no_filters_includes_all(self, box_connector):
        entry = _make_box_entry()
        assert box_connector._should_include_file(entry) is True

    def test_modified_after_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            datetime(2025, 1, 1, tzinfo=timezone.utc), None, None, None
        )
        entry = _make_box_entry(modified_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_modified_before_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, datetime(2023, 1, 1, tzinfo=timezone.utc), None, None
        )
        entry = _make_box_entry(modified_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_created_after_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, None, datetime(2025, 1, 1, tzinfo=timezone.utc), None
        )
        entry = _make_box_entry(created_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_created_before_excludes(self, box_connector):
        box_connector._cached_date_filters = (
            None, None, None, datetime(2023, 1, 1, tzinfo=timezone.utc)
        )
        entry = _make_box_entry(created_at="2024-01-01T00:00:00Z")
        assert box_connector._should_include_file(entry) is False

    def test_extension_filter_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry(name="doc.pdf")) is True
        assert box_connector._should_include_file(_make_box_entry(name="doc.txt")) is False

    def test_extension_filter_not_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry(name="doc.pdf")) is True
        assert box_connector._should_include_file(_make_box_entry(name="app.exe")) is False

    def test_no_extension_with_not_in(self, box_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        box_connector.sync_filters = MagicMock()
        box_connector.sync_filters.get.side_effect = (
            lambda key: mock_filter if key == SyncFilterKey.FILE_EXTENSIONS else None
        )
        box_connector._cached_date_filters = (None, None, None, None)

        assert box_connector._should_include_file(_make_box_entry(name="Makefile")) is True

    def test_missing_modified_date_with_filter(self, box_connector):
        box_connector._cached_date_filters = (
            datetime(2025, 1, 1, tzinfo=timezone.utc), None, None, None
        )
        entry = _make_box_entry(modified_at=None)
        del entry["modified_at"]
        assert box_connector._should_include_file(entry) is False


class TestBoxGetFilterOptions:
    async def test_raises(self, box_connector):
        with pytest.raises(NotImplementedError):
            await box_connector.get_filter_options("any_key")


class TestBoxFetchAndSyncItemsAsSharedWithMe:
    async def test_empty_items(self, box_connector):
        await box_connector._fetch_and_sync_items_as_shared_with_me([])

    async def test_file_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.files_get_file_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry())
        )
        box_connector._to_dict = MagicMock(return_value=_make_box_entry())
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))

        items = [("f1", "file", "u1", "user@test.com")]
        await box_connector._fetch_and_sync_items_as_shared_with_me(items)
        box_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_folder_sync(self, box_connector):
        box_connector.data_source.set_as_user_context = AsyncMock()
        box_connector.data_source.clear_as_user_context = AsyncMock()
        box_connector.data_source.folders_get_folder_by_id = AsyncMock(
            return_value=MagicMock(success=True, data=_make_box_entry(entry_type="folder"))
        )
        box_connector._to_dict = MagicMock(
            return_value=_make_box_entry(entry_type="folder")
        )
        box_connector._process_box_entry = AsyncMock(return_value=MagicMock(
            record=MagicMock(), new_permissions=[]
        ))
        box_connector._sync_folder_contents_recursively = AsyncMock()

        items = [("d1", "folder", "u1", "user@test.com")]
        await box_connector._fetch_and_sync_items_as_shared_with_me(items)
        box_connector._sync_folder_contents_recursively.assert_awaited()


class TestBoxProcessBoxEntryFileExtensionFilter:
    async def test_file_filtered_by_extension(self, box_connector):
        box_connector._should_include_file = MagicMock(return_value=False)
        entry = _make_box_entry()
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is None

    async def test_shared_with_me_group_link(self, box_connector):
        shared_group = MagicMock()
        shared_group.id = "sg1"
        provider = _make_mock_data_store_provider(record_group=shared_group)
        box_connector.data_store_provider = provider
        box_connector.data_source.collaborations_get_file_collaborations = AsyncMock(
            return_value=MagicMock(success=False, error="None")
        )

        entry = _make_box_entry(owned_by={"id": "other-user"})
        result = await box_connector._process_box_entry(
            entry, user_id="u1", user_email="user@test.com", record_group_id="rg1"
        )
        assert result is not None
        assert result.record.is_shared_with_me is True
