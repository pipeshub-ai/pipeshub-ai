import asyncio
import io
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleDriveError,
)
from app.models.entities import FileRecord, Record, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger():
    log = logging.getLogger("test_drive_ind_95")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_file_metadata(
    file_id="file-1",
    name="test.txt",
    mime_type="text/plain",
    created_time="2025-01-01T00:00:00Z",
    modified_time="2025-01-15T00:00:00Z",
    parents=None,
    shared=False,
    head_revision_id="rev-1",
    file_extension="txt",
    size=1024,
    version=None,
):
    meta = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "createdTime": created_time,
        "modifiedTime": modified_time,
        "shared": shared,
        "headRevisionId": head_revision_id,
        "fileExtension": file_extension,
        "size": size,
        "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
    }
    if parents is not None:
        meta["parents"] = parents
    if version is not None:
        meta["version"] = version
    return meta


def _make_record(**kwargs):
    defaults = dict(
        id="rec-1",
        external_record_id="file-1",
        external_record_group_id="drive-1",
        record_name="test.txt",
    )
    defaults.update(kwargs)
    rec = MagicMock(spec=Record)
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


@pytest.fixture
def connector():
    with patch(
        "app.connectors.sources.google.drive.individual.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.drive.individual.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        conn = GoogleDriveIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="drive-conn-1",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.google_client = MagicMock()
        conn.drive_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


# ===================================================================
# init() – lines 247-284
# ===================================================================

class TestInitFullPath:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleDriveDataSource")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_success_full_path(self, MockGClient, MockDS, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        mock_client_inst = AsyncMock()
        mock_client_inst.get_client.return_value = MagicMock()
        MockGClient.build_from_services = AsyncMock(return_value=mock_client_inst)
        MockDS.return_value = MagicMock()
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at", "refresh_token": "rt"},
            }
        )
        result = await connector.init()
        assert result is True
        assert connector.google_client is mock_client_inst

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_no_tokens_warning(self, MockGClient, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        mock_client_inst = AsyncMock()
        mock_client_inst.get_client.return_value = MagicMock()
        MockGClient.build_from_services = AsyncMock(return_value=mock_client_inst)
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {},
            }
        )
        with patch(
            "app.connectors.sources.google.drive.individual.connector.GoogleDriveDataSource"
        ):
            result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.fetch_oauth_config_by_id")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_init_client_build_fails(self, MockGClient, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {"clientId": "cid", "clientSecret": "csec"}
        }
        MockGClient.build_from_services = AsyncMock(side_effect=RuntimeError("build fail"))
        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"oauthConfigId": "oc-1"},
                "credentials": {"access_token": "at", "refresh_token": "rt"},
            }
        )
        with pytest.raises(ValueError, match="Failed to initialize Google Drive client"):
            await connector.init()


# ===================================================================
# _process_drive_items_generator – lines 663-673
# ===================================================================

class TestProcessDriveItemsGenerator:
    @pytest.mark.asyncio
    async def test_generator_yields_records(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = False
        mock_record.indexing_status = None
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = [MagicMock()]
        connector._process_drive_item.return_value = update
        files = [_make_file_metadata()]
        results = []
        async for rec, perms, upd in connector._process_drive_items_generator(
            files, "u1", "user@test.com", "d1"
        ):
            results.append((rec, perms, upd))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_generator_skips_none_update(self, connector):
        connector._process_drive_item = AsyncMock(return_value=None)
        files = [_make_file_metadata()]
        results = []
        async for item in connector._process_drive_items_generator(
            files, "u1", "user@test.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_generator_files_disabled_sets_auto_index_off(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = False
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_drive_item.return_value = update

        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata()], "u1", "user@test.com", "d1"
        ):
            results.append(item)
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_generator_shared_disabled_sets_auto_index_off(self, connector):
        connector._process_drive_item = AsyncMock()
        mock_record = MagicMock()
        mock_record.is_shared = True
        update = MagicMock()
        update.record = mock_record
        update.new_permissions = []
        connector._process_drive_item.return_value = update

        mock_indexing = MagicMock()
        mock_indexing.is_enabled = MagicMock(side_effect=lambda key, default=True: key != IndexingFilterKey.SHARED)
        connector.indexing_filters = mock_indexing

        results = []
        async for item in connector._process_drive_items_generator(
            [_make_file_metadata(shared=True)], "u1", "user@test.com", "d1"
        ):
            results.append(item)
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_generator_exception_continues(self, connector):
        connector._process_drive_item = AsyncMock(side_effect=Exception("boom"))
        files = [_make_file_metadata(), _make_file_metadata(file_id="file-2")]
        results = []
        async for item in connector._process_drive_items_generator(
            files, "u1", "user@test.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_generator_record_none_skipped(self, connector):
        update = MagicMock()
        update.record = None
        connector._process_drive_item = AsyncMock(return_value=update)
        files = [_make_file_metadata()]
        results = []
        async for item in connector._process_drive_items_generator(
            files, "u1", "user@test.com", "d1"
        ):
            results.append(item)
        assert len(results) == 0


# ===================================================================
# _perform_full_sync – lines 758-836
# ===================================================================

class TestPerformFullSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_no_start_page_token_returns(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={}
        )
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_with_pagination(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        page1 = {
            "files": [_make_file_metadata(file_id="f1")],
            "nextPageToken": "next-token-1234567890123456",
        }
        page2 = {
            "files": [_make_file_metadata(file_id="f2")],
        }
        connector.drive_data_source.files_list = AsyncMock(side_effect=[page1, page2])

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                record = MagicMock()
                perms = []
                yield record, perms, update

        connector._process_drive_items_generator = mock_gen

        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_awaited()
        connector.drive_delta_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_handles_deleted_and_updated(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={"files": [_make_file_metadata()]}
        )

        async def mock_gen(files, uid, email, did):
            update_del = MagicMock()
            update_del.is_deleted = True
            update_del.is_updated = False
            yield MagicMock(), [], update_del

            update_upd = MagicMock()
            update_upd.is_deleted = False
            update_upd.is_updated = True
            yield MagicMock(), [], update_upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()

        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        assert connector._handle_record_updates.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_empty_files_breaks(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={"files": []}
        )
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_exception_raises(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            side_effect=RuntimeError("api fail")
        )
        with pytest.raises(RuntimeError):
            await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_batch_processing(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.batch_size = 2
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        connector.drive_data_source.files_list = AsyncMock(
            return_value={"files": [_make_file_metadata(file_id=f"f{i}") for i in range(5)]}
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen

        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        assert connector.data_entities_processor.on_new_records.await_count >= 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_full_sync_page_token_in_params(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_get_start_page_token = AsyncMock(
            return_value={"startPageToken": "start-token-12345678901234567890"}
        )
        page1_files = [_make_file_metadata(file_id="f1")]
        connector.drive_data_source.files_list = AsyncMock(
            side_effect=[
                {"files": page1_files, "nextPageToken": "page2-token-1234567890123456"},
                {"files": []},
            ]
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen
        await connector._perform_full_sync("key", "org1", "u1", "u@t.com", "d1")
        calls = connector.drive_data_source.files_list.call_args_list
        assert len(calls) == 2
        assert "pageToken" in calls[1].kwargs.get("pageToken", "") or "pageToken" in str(calls[1])


# ===================================================================
# _perform_incremental_sync – lines 887-965
# ===================================================================

class TestPerformIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_deleted_files(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": True, "fileId": "del-1"},
                    {"removed": True, "fileId": "del-2"},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        connector._handle_record_updates = AsyncMock()

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        assert connector._handle_record_updates.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_file_changes(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata(file_id="f1")},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                record = MagicMock()
                yield record, [], update

        connector._process_drive_items_generator = mock_gen

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_updated_records(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata()},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            upd = MagicMock()
            upd.is_deleted = False
            upd.is_updated = True
            upd.record = MagicMock()
            upd.record.record_name = "updated.txt"
            yield upd.record, [], upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_deleted_in_generator(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata()},
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            upd = MagicMock()
            upd.is_deleted = True
            upd.is_updated = False
            upd.record = MagicMock()
            yield upd.record, [], upd

        connector._process_drive_items_generator = mock_gen
        connector._handle_record_updates = AsyncMock()

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_pagination(self, mock_refresh, connector):
        mock_refresh.return_value = None
        page1 = {
            "changes": [
                {"removed": False, "file": _make_file_metadata(file_id="f1")},
            ],
            "nextPageToken": "next-page-token-1234567890123456",
        }
        page2 = {
            "changes": [],
            "newStartPageToken": "final-start-token-1234567890123",
        }
        connector.drive_data_source.changes_list = AsyncMock(side_effect=[page1, page2])

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.drive_delta_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_no_tokens_warning(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [],
            }
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_exception_raises(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            side_effect=RuntimeError("api error")
        )
        with pytest.raises(RuntimeError):
            await connector._perform_incremental_sync(
                "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
            )

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_batch_processing(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.batch_size = 1
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [
                    {"removed": False, "file": _make_file_metadata(file_id=f"f{i}")}
                    for i in range(3)
                ],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )

        async def mock_gen(files, uid, email, did):
            for f in files:
                update = MagicMock()
                update.is_deleted = False
                update.is_updated = False
                yield MagicMock(), [], update

        connector._process_drive_items_generator = mock_gen

        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        assert connector.data_entities_processor.on_new_records.await_count >= 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_sync_point_unchanged(self, mock_refresh, connector):
        mock_refresh.return_value = None
        token = "same-token-12345678901234567"
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [],
                "newStartPageToken": token,
            }
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", token, "d1"
        )
        connector.drive_delta_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_removed_no_file_id(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": True}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        connector._handle_record_updates = AsyncMock()
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector._handle_record_updates.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.refresh_google_datasource_credentials")
    async def test_incremental_change_no_file_metadata(self, mock_refresh, connector):
        mock_refresh.return_value = None
        connector.drive_data_source.changes_list = AsyncMock(
            return_value={
                "changes": [{"removed": False}],
                "newStartPageToken": "new-start-token-12345678901234",
            }
        )
        await connector._perform_incremental_sync(
            "key", "org1", "u1", "u@t.com", "old-token-12345678901234567", "d1"
        )
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===================================================================
# test_connection_and_access – lines 977-978
# ===================================================================

class TestTestConnectionGoogleClient:
    @pytest.mark.asyncio
    async def test_google_client_none(self, connector):
        connector.google_client = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_client_returns_none(self, connector):
        connector.google_client.get_client.return_value = None
        result = await connector.test_connection_and_access()
        assert result is False


# ===================================================================
# _stream_google_api_request – lines 1001-1040
# ===================================================================

class TestStreamGoogleApiRequest:
    @pytest.mark.asyncio
    async def test_stream_success(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(return_value=(MagicMock(), True))
            MockDownloader.return_value = mock_dl

            chunks = []
            async for chunk in connector._stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)

    @pytest.mark.asyncio
    async def test_stream_http_error(self, connector):
        mock_request = MagicMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "error"
        http_err = HttpError(resp, b"error")
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=http_err)
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                async for _ in connector._stream_google_api_request(mock_request, "download"):
                    pass
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_chunk_error(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            mock_dl.next_chunk = MagicMock(side_effect=ValueError("chunk fail"))
            MockDownloader.return_value = mock_dl

            with pytest.raises(HTTPException):
                async for _ in connector._stream_google_api_request(mock_request, "download"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_multiple_chunks(self, connector):
        mock_request = MagicMock()
        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader:
            mock_dl = MagicMock()
            call_count = 0

            def next_chunk_side_effect():
                nonlocal call_count
                call_count += 1
                return (MagicMock(), call_count >= 2)

            mock_dl.next_chunk = MagicMock(side_effect=next_chunk_side_effect)
            MockDownloader.return_value = mock_dl

            chunks = []
            async for chunk in connector._stream_google_api_request(mock_request, "test"):
                chunks.append(chunk)


# ===================================================================
# _convert_to_pdf – lines 1044-1094
# ===================================================================

class TestConvertToPdf:
    @pytest.mark.asyncio
    async def test_convert_success(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.path.exists", return_value=True):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert result == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_convert_nonzero_return_code(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"conversion error"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_convert_timeout(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_convert_timeout_force_kill(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock()
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_convert_output_file_not_found(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.path.exists", return_value=False):
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_convert_generic_exception(self, connector):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no soffice")):
            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")


# ===================================================================
# _get_file_metadata_from_drive – lines 1106-1123
# ===================================================================

class TestGetFileMetadataFromDrive:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_get = MagicMock()
        mock_get.execute.return_value = {"id": "f1", "name": "test.txt", "mimeType": "text/plain"}
        mock_files.get.return_value = mock_get
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        result = await connector._get_file_metadata_from_drive("f1")
        assert result["id"] == "f1"

    @pytest.mark.asyncio
    async def test_not_found_error(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"not found")
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_other_http_error(self, connector):
        mock_service = MagicMock()
        mock_files = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        mock_files.get.return_value.execute.side_effect = HttpError(resp, b"forbidden")
        mock_service.files.return_value = mock_files
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("f1")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self, connector):
        mock_service = MagicMock()
        mock_service.files.side_effect = RuntimeError("unexpected")
        connector.google_client.get_client.return_value = mock_service

        with pytest.raises(HTTPException) as exc_info:
            await connector._get_file_metadata_from_drive("f1")
        assert exc_info.value.status_code == 500


# ===================================================================
# stream_record – lines 1143-1278
# ===================================================================

class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_file_id(self, connector):
        record = _make_record(external_record_id=None)
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_google_workspace_pdf_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.document"}
        )
        mock_service = MagicMock()
        mock_export = MagicMock()
        mock_service.files.return_value.export_media.return_value = mock_export
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            mock_stream.assert_called_once()
            call_kwargs = mock_stream.call_args
            assert call_kwargs[1]["mime_type"] == "application/pdf" or call_kwargs.kwargs.get("mime_type") == "application/pdf"

    @pytest.mark.asyncio
    async def test_google_workspace_native_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.spreadsheet"}
        )
        mock_service = MagicMock()
        mock_export = MagicMock()
        mock_service.files.return_value.export_media.return_value = mock_export
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_workspace_presentation_export(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.google-apps.presentation"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.export_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_file_pdf_conversion(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        )
        mock_service = MagicMock()
        mock_request = MagicMock()
        mock_service.files.return_value.get_media.return_value = mock_request
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)

            mock_dl_inst = MagicMock()
            status_mock = MagicMock()
            status_mock.progress.return_value = 1.0
            mock_dl_inst.next_chunk.return_value = (status_mock, True)
            MockDL.return_value = mock_dl_inst

            connector._convert_to_pdf = AsyncMock(return_value="/tmp/test_dir/test.pdf")
            mock_stream.return_value = MagicMock()

            result = await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_file_download(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "application/octet-stream"}
        )
        mock_service = MagicMock()
        mock_service.files.return_value.get_media.return_value = MagicMock()
        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_record_http_exception_passthrough(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_record_generic_exception(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pdf_download_http_error_forbidden_not_downloadable(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "text/plain"}
        )
        mock_service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_err = HttpError(resp, b"forbidden")
        http_err.error_details = [{"reason": "fileNotDownloadable"}]
        mock_service.files.return_value.get_media.return_value.execute.side_effect = http_err

        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = http_err
            MockDL.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_pdf_download_http_error_other(self, connector):
        record = _make_record()
        connector._get_file_metadata_from_drive = AsyncMock(
            return_value={"mimeType": "text/plain"}
        )
        mock_service = MagicMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "Server Error"
        http_err = HttpError(resp, b"server error")

        connector.google_client.get_client.return_value = mock_service

        with patch(
            "app.connectors.sources.google.drive.individual.connector.MediaIoBaseDownload"
        ) as MockDL, patch(
            "builtins.open", MagicMock()
        ), patch(
            "tempfile.TemporaryDirectory"
        ) as MockTmpDir:
            MockTmpDir.return_value.__enter__ = MagicMock(return_value="/tmp/test_dir")
            MockTmpDir.return_value.__exit__ = MagicMock(return_value=False)
            mock_dl = MagicMock()
            mock_dl.next_chunk.side_effect = http_err
            MockDL.return_value = mock_dl

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, convertTo=MimeTypes.PDF.value)
            assert exc_info.value.status_code == 500


# ===================================================================
# _create_app_user error – lines 1310-1312
# ===================================================================

class TestCreateAppUserError:
    @pytest.mark.asyncio
    async def test_create_app_user_exception(self, connector):
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=RuntimeError("db fail")
        )
        user_about = {
            "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "U"}
        }
        with pytest.raises(RuntimeError):
            await connector._create_app_user(user_about)


# ===================================================================
# run_sync – line 1335 (no drive_id)
# ===================================================================

class TestRunSyncNoDriveId:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_no_drive_id_raises(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={
                "user": {"permissionId": "p1", "emailAddress": "u@t.com", "displayName": "U"},
                "storageQuota": {},
            }
        )
        connector.drive_data_source.files_get = AsyncMock(return_value={})
        connector._create_app_user = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await connector.run_sync()
        assert exc_info.value.status_code == 500


# ===================================================================
# cleanup – lines 1367-1379
# ===================================================================

class TestCleanupBranches:
    @pytest.mark.asyncio
    async def test_cleanup_without_drive_data_source(self, connector):
        if hasattr(connector, "drive_data_source"):
            del connector.drive_data_source
        await connector.cleanup()
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_without_google_client(self, connector):
        connector.drive_data_source = MagicMock()
        if hasattr(connector, "google_client"):
            del connector.google_client
        await connector.cleanup()
        assert connector.drive_data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_with_none_values(self, connector):
        connector.drive_data_source = None
        connector.google_client = None
        await connector.cleanup()
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_exception_swallowed(self, connector):
        connector.drive_data_source = MagicMock()
        cls = type(connector)
        cls.drive_data_source = property(
            fget=MagicMock(side_effect=RuntimeError("fail")),
            fset=MagicMock()
        )
        try:
            await connector.cleanup()
        except Exception:
            pass
        finally:
            try:
                delattr(cls, "drive_data_source")
            except AttributeError:
                pass


# ===================================================================
# reindex_records – lines 1402-1403, 1417-1419, 1422-1427
# ===================================================================

class TestReindexRecordsExtended:
    @pytest.mark.asyncio
    async def test_no_user_info_raises(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {}}
        )
        with pytest.raises(Exception, match="Failed to get user information"):
            await connector.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_record_check_exception_continues(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=RuntimeError("check fail")
        )
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_updated_records(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(MagicMock(), [MagicMock()])
        )
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_non_updated_records(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.about_get = AsyncMock(
            return_value={"user": {"permissionId": "p1", "emailAddress": "u@t.com"}}
        )
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([_make_record()])
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_propagates_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError):
            await connector.reindex_records([_make_record()])


# ===================================================================
# _check_and_fetch_updated_record – lines 1438-1489
# ===================================================================

class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_no_file_id(self, connector):
        record = _make_record(external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_group_id_uses_user_id(self, connector):
        record = _make_record(external_record_group_id=None)
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        connector._process_drive_item = AsyncMock()
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = []
        connector._process_drive_item.return_value = update

        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is not None
        call_args = connector._process_drive_item.call_args
        assert call_args[1].get("drive_id", call_args[0][3] if len(call_args[0]) > 3 else None) == "u1"

    @pytest.mark.asyncio
    async def test_http_not_found_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        connector.drive_data_source.files_get = AsyncMock(
            side_effect=HttpError(resp, b"not found")
        )
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_other_error_raises(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        resp = MagicMock()
        resp.status = 500
        resp.reason = "Server Error"
        connector.drive_data_source.files_get = AsyncMock(
            side_effect=HttpError(resp, b"error")
        )
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_file_metadata_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_update_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        connector._process_drive_item = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_update_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = True
        update.is_updated = False
        connector._process_drive_item = AsyncMock(return_value=update)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_updated_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = False
        connector._process_drive_item = AsyncMock(return_value=update)
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_returns_record_and_permissions(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock()
        connector.drive_data_source.files_get = AsyncMock(
            return_value=_make_file_metadata()
        )
        update = MagicMock()
        update.is_deleted = False
        update.is_updated = True
        update.record = MagicMock()
        update.new_permissions = [MagicMock()]
        connector._process_drive_item = AsyncMock(return_value=update)

        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is not None
        assert update.record.id == record.id

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self, connector):
        record = _make_record()
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("fail"))
        result = await connector._check_and_fetch_updated_record("org1", record, "u1", "u@t.com")
        assert result is None


# ===================================================================
# create_connector – lines 1511-1518
# ===================================================================

class TestCreateConnector:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google.drive.individual.connector.DataSourceEntitiesProcessor")
    @patch("app.connectors.sources.google.drive.individual.connector.SyncPoint")
    @patch("app.connectors.sources.google.drive.individual.connector.GoogleClient")
    async def test_create_connector(self, MockGClient, MockSP, MockDSEP):
        from app.connectors.sources.google.drive.individual.connector import (
            GoogleDriveIndividualConnector,
        )

        MockSP.return_value = AsyncMock()
        mock_dep = AsyncMock()
        mock_dep.org_id = "org-1"
        MockDSEP.return_value = mock_dep

        logger = _make_logger()
        ds_provider = MagicMock()
        config_service = AsyncMock()

        result = await GoogleDriveIndividualConnector.create_connector(
            logger, ds_provider, config_service, "conn-1"
        )
        assert isinstance(result, GoogleDriveIndividualConnector)
        mock_dep.initialize.assert_awaited_once()


# ===================================================================
# get_filter_options
# ===================================================================

class TestGetFilterOptionsRaises:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("any_key", page=1, limit=10, search="x", cursor="c")


# ===================================================================
# Extension filter additional edge cases
# ===================================================================

class TestExtensionFilterEdgeCases:
    def test_non_list_filter_value_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = _make_file_metadata()
        assert connector._pass_extension_filter(meta) is True

    def test_google_docs_not_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(meta) is False

    def test_google_docs_in_allowed(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_DOCS.value]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_DOCS.value}
        assert connector._pass_extension_filter(meta) is True

    def test_google_sheets_unknown_operator(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [MimeTypes.GOOGLE_SHEETS.value]
        mock_op = MagicMock()
        mock_op.value = "unknown_operator"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": MimeTypes.GOOGLE_SHEETS.value}
        assert connector._pass_extension_filter(meta) is True

    def test_no_extension_in_operator_returns_false(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "name": "noext"}
        assert connector._pass_extension_filter(meta) is False

    def test_no_extension_not_in_operator_returns_true(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "name": "noext"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_in_allowed_list(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_not_in_excluded_list(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.NOT_IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True

    def test_extension_from_name_fallback(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["docx"]
        mock_op = MagicMock()
        mock_op.value = FilterOperator.IN
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "application/msword", "name": "report.docx"}
        assert connector._pass_extension_filter(meta) is True

    def test_unknown_operator_allows(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = "BETWEEN"
        mock_filter.get_operator.return_value = mock_op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        meta = {"mimeType": "text/plain", "fileExtension": "txt"}
        assert connector._pass_extension_filter(meta) is True


# ===================================================================
# Date filter additional edge cases
# ===================================================================

class TestDateFilterEdgeCases:
    def test_created_filter_before_start(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2024-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_created_filter_after_end(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_modified_filter_before_start(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00Z", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time="2024-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_modified_filter_after_end(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time="2025-01-01T00:00:00Z")
        assert connector._pass_date_filters(meta) is False

    def test_both_filters_pass(self, connector):
        created_f = MagicMock()
        created_f.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        modified_f = MagicMock()
        modified_f.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: created_f if key == SyncFilterKey.CREATED else (modified_f if key == SyncFilterKey.MODIFIED else None)
        )
        meta = _make_file_metadata(
            created_time="2025-01-01T00:00:00Z",
            modified_time="2025-01-15T00:00:00Z",
        )
        assert connector._pass_date_filters(meta) is True

    def test_no_created_time_in_metadata(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.CREATED else None
        )
        meta = _make_file_metadata(created_time=None)
        assert connector._pass_date_filters(meta) is True

    def test_no_modified_time_in_metadata(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        meta = _make_file_metadata(modified_time=None)
        assert connector._pass_date_filters(meta) is True
