import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    IndexingFilterKey,
    ListOperator,
    SyncFilterKey,
)
from app.connectors.sources.google_cloud_storage.connector import (
    GCSConnector,
    GCSDataSourceEntitiesProcessor,
    get_file_extension,
    get_folder_path_segments_from_key,
    get_mimetype_for_gcs,
    get_parent_path_for_gcs,
    get_parent_path_from_key,
    get_parent_weburl_for_gcs,
    parse_parent_external_id,
)
from app.models.entities import FileRecord, RecordType, User


def _make_response(success=True, data=None, error=None, status_code=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    r.status_code = status_code
    return r


def _make_mock_tx_store(existing_record=None, existing_revision_record=None, user=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_external_revision_id = AsyncMock(return_value=existing_revision_record)
    tx.get_user_by_id = AsyncMock(return_value=user or {"email": "user@test.com"})
    tx.get_user_by_user_id = AsyncMock(return_value=user or {"email": "user@test.com"})
    tx.ensure_team_app_edge = AsyncMock()
    tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    return tx


def _make_mock_data_store_provider(existing_record=None, existing_revision_record=None, user=None):
    tx = _make_mock_tx_store(existing_record, existing_revision_record, user)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.gcs95")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gcs-95"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.reindex_existing_records = AsyncMock()
    proc.initialize = AsyncMock()
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.get_record_by_external_revision_id = AsyncMock(return_value=None)
    proc.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    proc.get_user_by_user_id = AsyncMock(
        return_value=User(
            email="user@test.com",
            source_user_id="src-1",
            org_id="org-gcs-95",
            full_name="Test User",
            title="Title",
        )
    )
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {"serviceAccountJson": '{"type":"service_account","project_id":"test"}'},
        "scope": "TEAM",
        "created_by": "user-creator-1",
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_data_entities_processor, mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.google_cloud_storage.connector.GCSApp"):
        c = GCSConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="gcs-95-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


class TestHelpers95:
    def test_get_file_extension_single_dot_only(self):
        assert get_file_extension("file.") == ""

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_key("a/b/c/") == "a/b"

    def test_get_folder_segments_single_part_only(self):
        assert get_folder_path_segments_from_key("single") == []

    def test_get_folder_segments_leading_trailing(self):
        assert get_folder_path_segments_from_key("/a/b/") == ["a"]

    @patch(
        "app.connectors.sources.google_cloud_storage.connector.mimetypes.guess_type",
        return_value=("application/zip", None),
    )
    def test_get_mimetype_known_and_in_enum(self, _mock_guess):
        result = get_mimetype_for_gcs("test.zip")
        assert result == "application/zip"

    def test_parse_parent_deep(self):
        bucket, path = parse_parent_external_id("b/a/b/c")
        assert bucket == "b"
        assert path == "a/b/c/"

    def test_get_parent_weburl_for_gcs_no_path(self):
        url = get_parent_weburl_for_gcs("bucket-only")
        assert "bucket-only" in url
        assert url.endswith("bucket-only")

    def test_get_parent_path_for_gcs_with_no_trailing(self):
        result = get_parent_path_for_gcs("bucket/subdir")
        assert result == "subdir/"

    def test_get_parent_path_for_gcs_no_slash(self):
        result = get_parent_path_for_gcs("bucketonly")
        assert result is None


class TestGCSDataSourceEntitiesProcessor95:
    def test_create_placeholder_parent_record_file_type(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = GCSDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        child_record = MagicMock(spec=FileRecord)
        child_record.connector_name = "GCS"
        child_record.connector_id = "c1"
        child_record.origin = "CONNECTOR"
        child_record.record_group_type = "BUCKET"
        child_record.external_record_group_id = "mybucket"

        with patch.object(
            proc.__class__.__bases__[0],
            "_create_placeholder_parent_record",
            return_value=FileRecord(
                id="placeholder-id",
                record_name="parent",
                record_type=RecordType.FILE,
                external_record_id="mybucket/folder",
                version=0,
                origin="CONNECTOR",
                connector_name="GCS",
                connector_id="c1",
                source_created_at=0,
                source_updated_at=0,
                is_file=False,
            ),
        ):
            result = proc._create_placeholder_parent_record(
                "mybucket/folder", RecordType.FILE, child_record
            )
            assert isinstance(result, FileRecord)
            assert result.is_internal is True
            assert result.hide_weburl is True
            assert "mybucket" in (result.weburl or "")

    def test_create_placeholder_non_file_type(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = GCSDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        child_record = MagicMock()
        base_record = MagicMock()
        with patch.object(
            proc.__class__.__bases__[0],
            "_create_placeholder_parent_record",
            return_value=base_record,
        ):
            result = proc._create_placeholder_parent_record(
                "mybucket", RecordType.DRIVE, child_record
            )
            assert result is base_record


class TestInit95:
    @pytest.mark.asyncio
    async def test_init_success(self, connector):
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services",
            new_callable=AsyncMock,
        ) as mock_build, patch(
            "app.connectors.sources.google_cloud_storage.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            mock_client = MagicMock()
            mock_client.get_project_id.return_value = "test-project"
            mock_build.return_value = mock_client
            result = await connector.init()
            assert result is True
            assert connector.project_id == "test-project"

    @pytest.mark.asyncio
    async def test_init_client_exception(self, connector):
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("Auth failed"),
        ):
            result = await connector.init()
            assert result is False

    @pytest.mark.asyncio
    async def test_init_scope_from_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"serviceAccountJson": '{"type":"service_account"}'},
            "scope": "TEAM",
        })
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services",
            new_callable=AsyncMock,
        ) as mock_build, patch(
            "app.connectors.sources.google_cloud_storage.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            mock_build.return_value = MagicMock()
            await connector.init()
            assert connector.scope == ConnectorScope.PERSONAL.value


class TestRunSync95:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_selected_buckets_from_filter(self, mock_filters, connector):
        bucket_filter = MagicMock()
        bucket_filter.value = ["filtered-bucket"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = bucket_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock()
        await connector.run_sync()
        connector._sync_bucket.assert_awaited_once_with("filtered-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_no_buckets_data(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": []})
        )
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock()
        await connector.run_sync()
        connector._sync_bucket.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_no_buckets_key(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {})
        )
        await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_failure(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(False, error="denied")
        )
        await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_skip_none_bucket(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": None}, {"name": "real-bucket"}]})
        )
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock()
        await connector.run_sync()
        connector._sync_bucket.assert_awaited_once_with("real-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_sync_bucket_exception_continues(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = "err-bucket"
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock(side_effect=Exception("sync err"))
        await connector.run_sync()


class TestCreateRecordGroupsForBuckets95:
    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        connector.data_store_provider = _make_mock_data_store_provider(user={"email": "owner@test.com"})
        await connector._create_record_groups_for_buckets(["b1"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator_email(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        connector.data_store_provider = _make_mock_data_store_provider(user={"email": None})
        await connector._create_record_groups_for_buckets(["b1"])

    @pytest.mark.asyncio
    async def test_personal_scope_user_lookup_fails(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        provider.transaction = _tx
        connector.data_store_provider = provider
        await connector._create_record_groups_for_buckets(["b1"])

    @pytest.mark.asyncio
    async def test_skip_none_bucket_names(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        await connector._create_record_groups_for_buckets([None, "b1", None])
        assert connector.data_entities_processor.on_new_record_groups.await_count == 1

    @pytest.mark.asyncio
    async def test_lock_timeout_retry(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[Exception("timeout waiting to lock key"), None]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await connector._create_record_groups_for_buckets(["b1"])

    @pytest.mark.asyncio
    async def test_lock_timeout_exhausted(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=Exception("timeout waiting to lock key")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await connector._create_record_groups_for_buckets(["b1"])

    @pytest.mark.asyncio
    async def test_non_lock_error(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=Exception("other error")
        )
        await connector._create_record_groups_for_buckets(["b1"])


class TestGetDateFilters95:
    def test_with_modified_before(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (None, "2025-12-31T23:59:59+00:00")

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = side_effect
        result = connector._get_date_filters()
        assert result[1] is not None

    def test_with_both_created_filters(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", "2025-12-31T23:59:59+00:00")

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = side_effect
        result = connector._get_date_filters()
        assert result[2] is not None
        assert result[3] is not None


class TestPassDateFilters95:
    def test_no_last_modified(self, connector):
        obj = {"Key": "file.txt"}
        assert connector._pass_date_filters(obj, 100, None, None, None) is True

    def test_non_string_last_modified(self, connector):
        obj = {"Key": "file.txt", "LastModified": 12345}
        assert connector._pass_date_filters(obj, 100, None, None, None) is True

    def test_invalid_iso_string(self, connector):
        obj = {"Key": "file.txt", "LastModified": "not-a-date"}
        assert connector._pass_date_filters(obj, 100, None, None, None) is True

    def test_modified_before_cutoff(self, connector):
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(obj, None, past_ms, None, None) is False

    def test_created_after_cutoff(self, connector):
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "TimeCreated": "2025-01-01T00:00:00Z"}
        assert connector._pass_date_filters(obj, None, None, future_ms, None) is False

    def test_created_before_cutoff(self, connector):
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "TimeCreated": "2025-06-01T00:00:00Z"}
        assert connector._pass_date_filters(obj, None, None, None, past_ms) is False

    def test_time_created_invalid_iso(self, connector):
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "TimeCreated": "invalid"}
        assert connector._pass_date_filters(obj, None, None, 100, None) is True

    def test_time_created_not_string(self, connector):
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "TimeCreated": 12345}
        assert connector._pass_date_filters(obj, None, None, 100, None) is True


class TestProcessRecordsWithRetry95:
    @pytest.mark.asyncio
    async def test_lock_timeout_then_success(self, connector):
        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=[Exception("timeout waiting to lock key"), None]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await connector._process_records_with_retry([(MagicMock(), [])])

    @pytest.mark.asyncio
    async def test_lock_timeout_exhausted(self, connector):
        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("timeout waiting to lock key")
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception, match="timeout"):
                await connector._process_records_with_retry([(MagicMock(), [])])


class TestSyncBucket95:
    @pytest.mark.asyncio
    async def test_sync_bucket_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_access_denied(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(False, error="403 PermissionDenied")
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_generic_error(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(False, error="Something else")
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_no_contents(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, {})
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_with_objects(self, connector):
        objects_data = {
            "Contents": [
                {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 100},
                {"Key": "folder/", "LastModified": "2025-06-01T00:00:00Z"},
                {"Key": "dir/nested.pdf", "LastModified": "2025-06-01T00:00:00Z", "Size": 200},
            ],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, objects_data)
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._process_gcs_object = AsyncMock(return_value=(MagicMock(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        connector._process_records_with_retry = AsyncMock()
        await connector._sync_bucket("bucket")
        connector._process_records_with_retry.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_bucket_with_extension_filter(self, connector):
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf"]
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda key: ext_filter if key == "file_extensions" else None
        connector.sync_filters = sync_filters

        objects_data = {
            "Contents": [
                {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z"},
                {"Key": "report.pdf", "LastModified": "2025-06-01T00:00:00Z", "Size": 100},
            ],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, objects_data)
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._process_gcs_object = AsyncMock(return_value=(MagicMock(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        connector._process_records_with_retry = AsyncMock()
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_with_pagination(self, connector):
        page1 = {
            "Contents": [{"Key": "file1.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 10}],
            "IsTruncated": True,
            "NextContinuationToken": "token123",
        }
        page2 = {
            "Contents": [{"Key": "file2.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 20}],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(side_effect=[
            _make_response(True, page1),
            _make_response(True, page2),
        ])
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._process_gcs_object = AsyncMock(return_value=(MagicMock(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        connector._process_records_with_retry = AsyncMock()
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_incremental_with_sync_point(self, connector):
        objects_data = {
            "Contents": [{"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 10}],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, objects_data)
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"page_token": "old_token", "last_sync_time": 1000000}
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._process_gcs_object = AsyncMock(return_value=(MagicMock(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        connector._process_records_with_retry = AsyncMock()
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_object_processing_error(self, connector):
        objects_data = {
            "Contents": [{"Key": "bad.txt", "LastModified": "2025-06-01T00:00:00Z"}],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, objects_data)
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._process_gcs_object = AsyncMock(side_effect=Exception("obj error"))
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_exception_in_loop(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(side_effect=Exception("connection error"))
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_bucket("bucket")

    @pytest.mark.asyncio
    async def test_sync_bucket_file_no_extension_with_filter(self, connector):
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf"]
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda key: ext_filter if key == "file_extensions" else None
        connector.sync_filters = sync_filters

        objects_data = {
            "Contents": [{"Key": "Makefile", "LastModified": "2025-06-01T00:00:00Z"}],
            "IsTruncated": False,
        }
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(True, objects_data)
        )
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_bucket("bucket")



class TestEnsureParentFoldersExist95:
    @pytest.mark.asyncio
    async def test_empty_segments(self, connector):
        await connector._ensure_parent_folders_exist("bucket", [])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_segment(self, connector):
        connector._create_gcs_permissions = AsyncMock(return_value=[])
        connector._process_records_with_retry = AsyncMock()
        await connector._ensure_parent_folders_exist("bucket", ["folder"])
        connector._process_records_with_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_segments(self, connector):
        connector._create_gcs_permissions = AsyncMock(return_value=[])
        connector._process_records_with_retry = AsyncMock()
        await connector._ensure_parent_folders_exist("bucket", ["a", "a/b", "a/b/c"])
        assert connector._process_records_with_retry.await_count == 3


class TestProcessGcsObject95:
    @pytest.mark.asyncio
    async def test_empty_key(self, connector):
        record, perms = await connector._process_gcs_object({"Key": ""}, "bucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_slash_only_key(self, connector):
        record, perms = await connector._process_gcs_object({"Key": "/"}, "bucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_new_file(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "path/file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "TimeCreated": "2025-05-01T00:00:00Z",
            "Size": 1024,
            "ContentType": "text/plain",
            "Md5Hash": "abc123",
            "Crc32c": "xyz",
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None
        assert record.record_name == "file.txt"
        assert record.is_file is True
        assert record.extension == "txt"

    @pytest.mark.asyncio
    async def test_folder_object(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "folder/",
            "LastModified": "2025-06-01T00:00:00Z",
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None
        assert record.is_file is False

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old_md5"
        existing.external_record_id = "bucket/file.txt"
        existing.version = 1
        existing.source_created_at = 1700000000000
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector.scope = ConnectorScope.TEAM.value

        obj = {
            "Key": "file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "Md5Hash": "new_md5",
            "Size": 100,
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None
        assert record.version == 2

    @pytest.mark.asyncio
    async def test_existing_record_same_revision(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "same_md5"
        existing.external_record_id = "bucket/file.txt"
        existing.version = 1
        existing.source_created_at = 1700000000000
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector.scope = ConnectorScope.TEAM.value

        obj = {
            "Key": "file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "Md5Hash": "same_md5",
            "Size": 100,
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_move_detected(self, connector):
        existing = MagicMock()
        existing.id = "moved-id"
        existing.external_record_id = "bucket/old/file.txt"
        existing.external_revision_id = "same_md5"
        existing.version = 0
        existing.source_created_at = 1700000000000
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_entities_processor.get_record_by_external_revision_id = AsyncMock(return_value=existing)
        connector.scope = ConnectorScope.TEAM.value

        obj = {
            "Key": "new/file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "Md5Hash": "same_md5",
            "Size": 100,
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None
        assert record.id == "moved-id"

    @pytest.mark.asyncio
    async def test_no_revision_available(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "Size": 100,
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_missing_revision_ids(self, connector):
        existing = MagicMock()
        existing.id = "ex-id"
        existing.external_revision_id = ""
        existing.external_record_id = "bucket/file.txt"
        existing.version = 0
        existing.source_created_at = 1700000000000
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector.scope = ConnectorScope.TEAM.value

        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 100}
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_indexing_filter_disables_indexing(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        mock_indexing_filters = MagicMock()
        mock_indexing_filters.is_enabled.return_value = False
        connector.indexing_filters = mock_indexing_filters

        obj = {
            "Key": "file.txt",
            "LastModified": "2025-06-01T00:00:00Z",
            "Md5Hash": "abc",
            "Size": 100,
        }
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_invalid_last_modified(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        obj = {"Key": "file.txt", "LastModified": "invalid-date", "Size": 100}
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        obj = {"Key": "file.txt", "Size": 100}
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_exception_in_processing(self, connector):
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=Exception("db err"))
        obj = {"Key": "file.txt", "LastModified": "2025-06-01T00:00:00Z", "Size": 100}
        record, perms = await connector._process_gcs_object(obj, "bucket")
        assert record is None


class TestCreateGcsPermissions95:
    @pytest.mark.asyncio
    async def test_team_scope(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        perms = await connector._create_gcs_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        perms = await connector._create_gcs_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = None
        perms = await connector._create_gcs_permissions("bucket", "key")
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_personal_scope_user_lookup_fails(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        provider.transaction = _tx
        connector.data_store_provider = provider
        perms = await connector._create_gcs_permissions("bucket", "key")
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_personal_scope_user_no_email(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        connector.data_store_provider = _make_mock_data_store_provider(user={"email": None})
        perms = await connector._create_gcs_permissions("bucket", "key")
        assert len(perms) == 1


class TestTestConnectionAndAccess95:
    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_make_response(True))
        assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_make_response(False, error="err"))
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(side_effect=Exception("network"))
        assert await connector.test_connection_and_access() is False


class TestGetSignedUrl95:
    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(MagicMock())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_no_bucket(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id=None)
        assert await connector.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_no_external_record_id(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id="bucket", external_record_id=None)
        assert await connector.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(True, {"url": "https://signed.url"})
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/file.txt", record_name="file.txt")
        result = await connector.get_signed_url(record)
        assert result == "https://signed.url"

    @pytest.mark.asyncio
    async def test_access_denied(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(False, error="403 Forbidden", status_code=403)
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/file.txt", record_name="file.txt")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_not_found(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(False, error="404 NotFound", status_code=404)
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/file.txt", record_name="file.txt")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_object_key_does_not_pick_the_status(self, connector):
        """The GCS error text embeds bucket and key, so matching phrases in it
        let a key like "hr/permissions/2024.xlsx" report a deleted object as 403."""
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(
                False,
                error="Blob not found: bucket/hr/permissions/2024.xlsx",
                status_code=404,
            )
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/hr/permissions/2024.xlsx",
                           record_name="2024.xlsx")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_denial_on_a_key_containing_404_is_still_403(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(
                False,
                error="GCS API error: reports/404-page-analysis.pdf",
                status_code=403,
            )
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/reports/404-page-analysis.pdf",
                           record_name="404-page-analysis.pdf")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_other_failure(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(False, error="timeout")
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/file.txt", record_name="file.txt")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(side_effect=Exception("err"))
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="bucket/file.txt", record_name="file.txt")
        with pytest.raises(HTTPException) as exc_info:
            await connector.get_signed_url(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_key_without_bucket_prefix(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_signed_url = AsyncMock(
            return_value=_make_response(True, {"url": "https://signed.url"})
        )
        record = MagicMock(id="r1", external_record_group_id="bucket",
                           external_record_id="/path/file.txt", record_name="file.txt")
        result = await connector.get_signed_url(record)
        assert result is not None


class TestStreamRecord95:
    @pytest.mark.asyncio
    async def test_folder_raises(self, connector):
        from fastapi import HTTPException
        record = MagicMock(spec=FileRecord)
        record.is_file = False
        with pytest.raises(HTTPException):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_no_signed_url_raises(self, connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.is_file = True
        connector.get_signed_url = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_success(self, connector):
        record = MagicMock()
        record.is_file = True
        record.record_name = "file.txt"
        record.id = "r1"
        record.mime_type = "text/plain"
        connector.get_signed_url = AsyncMock(return_value="https://signed.url")
        with patch("app.connectors.sources.google_cloud_storage.connector.create_stream_record_response") as mock_create:
            mock_create.return_value = MagicMock()
            result = await connector.stream_record(record)
            mock_create.assert_called_once()


class TestGetFilterOptions95:
    @pytest.mark.asyncio
    async def test_buckets(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": "b1"}, {"name": "b2"}]})
        )
        result = await connector.get_filter_options("buckets")
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_unsupported_key(self, connector):
        with pytest.raises(ValueError, match="Unsupported"):
            await connector.get_filter_options("invalid")

    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        result = await connector.get_filter_options("buckets")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_fails(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_make_response(False, error="err"))
        result = await connector.get_filter_options("buckets")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_buckets_key(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_make_response(True, {}))
        result = await connector.get_filter_options("buckets")
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_search(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": "prod-data"}, {"name": "dev-logs"}]})
        )
        result = await connector.get_filter_options("buckets", search="prod")
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": f"b{i}"} for i in range(5)]})
        )
        result = await connector.get_filter_options("buckets", page=1, limit=2)
        assert len(result.options) == 2
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(side_effect=Exception("err"))
        result = await connector.get_filter_options("buckets")
        assert result.success is False


class TestReindexRecords95:
    @pytest.mark.asyncio
    async def test_empty(self, connector):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self, connector):
        connector.data_source = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(MagicMock(), []), None]
        )
        connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await connector.reindex_records([MagicMock(id="r1"), MagicMock(id="r2")])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_record_exception_continues(self, connector):
        connector.data_source = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("err"))
        connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await connector.reindex_records([MagicMock(id="r1")])

    @pytest.mark.asyncio
    async def test_reindex_raises(self, connector):
        connector.data_source = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=(MagicMock(), []))
        connector.data_entities_processor.on_new_records = AsyncMock(side_effect=RuntimeError("fatal"))
        with pytest.raises(RuntimeError):
            await connector.reindex_records([MagicMock(id="r1")])


class TestCheckAndFetchUpdatedRecord95:
    @pytest.mark.asyncio
    async def test_missing_bucket(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id=None, external_record_id="k")
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_external_record_id(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id="bucket", external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_key(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id="bucket", external_record_id="bucket/")
        connector.data_source.head_blob = AsyncMock(return_value=_make_response(True, {}))
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_head_blob_fails(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt"
        )
        connector.data_source.head_blob = AsyncMock(return_value=_make_response(False))
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_metadata(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt"
        )
        connector.data_source.head_blob = AsyncMock(return_value=_make_response(True, None))
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_same_revision(self, connector):
        connector.data_source = MagicMock()
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="same_md5", version=1, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"Md5Hash": "same_md5", "LastModified": "2025-06-01T00:00:00Z"})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_revision_changed(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="old_md5", version=1, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {
                "Md5Hash": "new_md5",
                "LastModified": "2025-06-01T00:00:00Z",
                "ContentType": "text/plain",
                "ContentLength": 2048,
            })
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated_record, perms = result
        assert updated_record.version == 2

    @pytest.mark.asyncio
    async def test_missing_current_revision(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="old", version=0, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"LastModified": "2025-06-01T00:00:00Z", "ContentLength": 100})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_stored_revision(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id=None, version=0, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"Md5Hash": "abc", "LastModified": "2025-06-01T00:00:00Z", "ContentLength": 100})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_indexing_disabled(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        mock_indexing_filters = MagicMock()
        mock_indexing_filters.is_enabled.return_value = False
        connector.indexing_filters = mock_indexing_filters
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="old", version=0, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"Md5Hash": "new", "LastModified": "2025-06-01T00:00:00Z", "ContentLength": 100})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        assert result[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_blob = AsyncMock(side_effect=Exception("err"))
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_bucket_prefix(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="/file.txt",
            external_revision_id="old", version=0, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"Md5Hash": "new", "LastModified": "2025-06-01T00:00:00Z", "ContentLength": 100})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_folder_record(self, connector):
        connector.data_source = MagicMock()
        connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/folder/",
            external_revision_id="old", version=0, source_created_at=1700000000000,
        )
        connector.data_source.head_blob = AsyncMock(
            return_value=_make_response(True, {"Md5Hash": "new", "LastModified": "2025-06-01T00:00:00Z"})
        )
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None


class TestRunIncrementalSync95:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_selected_buckets(self, mock_filters, connector):
        bucket_filter = MagicMock()
        bucket_filter.value = ["inc-bucket"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = bucket_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_bucket.assert_awaited_once_with("inc-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": "auto-bucket"}]})
        )
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_bucket.assert_awaited_once_with("auto-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_buckets_to_sync(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {})
        )
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_skip_none_buckets(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = None
        connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": None}]})
        )
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_bucket.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_sync_error_continues(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = "bucket"
        connector._sync_bucket = AsyncMock(side_effect=Exception("sync err"))
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_fatal_error(self, mock_filters, connector):
        mock_filters.side_effect = RuntimeError("fatal")
        connector.data_source = MagicMock()
        with pytest.raises(RuntimeError):
            await connector.run_incremental_sync()


class TestCreateConnector95:
    @pytest.mark.asyncio
    async def test_create_connector(self, mock_logger, mock_data_store_provider, mock_config_service):
        with patch("app.connectors.sources.google_cloud_storage.connector.GCSApp"):
            with patch("app.connectors.sources.google_cloud_storage.connector.GCSDataSourceEntitiesProcessor") as mock_proc_cls:
                mock_proc_instance = MagicMock()
                mock_proc_instance.initialize = AsyncMock()
                mock_proc_cls.return_value = mock_proc_instance
                result = await GCSConnector.create_connector(
                    logger=mock_logger,
                    data_store_provider=mock_data_store_provider,
                    config_service=mock_config_service,
                    connector_id="gcs-new-1",
                    scope="personal",
                    created_by="test-user-id",
                    data_entities_processor=mock_proc_instance,
                )
                assert isinstance(result, GCSConnector)


class TestGetGcsRevisionId95:
    def test_generation_with_metageneration(self, connector):
        obj = {"Generation": "123", "Metageneration": "456"}
        assert connector._get_gcs_revision_id(obj) == "123:456"

    def test_generation_without_metageneration(self, connector):
        obj = {"Generation": "123"}
        assert connector._get_gcs_revision_id(obj) == "123"

    def test_empty_object(self, connector):
        assert connector._get_gcs_revision_id({}) == ""


def _folder_filter(values, exclude=False):
    from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType, ListOperator

    operator = ListOperator.NOT_IN if exclude else ListOperator.IN
    return FilterCollection(filters=[Filter(key="folder_paths", value=values, type=FilterType.LIST, operator=operator)])


def _in_memory_sync_points():
    saved = {}
    sync_points = MagicMock()
    sync_points.saved = saved
    sync_points.read_sync_point = AsyncMock(side_effect=lambda key: saved.get(key))
    sync_points.update_sync_point = AsyncMock(side_effect=lambda key, data: saved.setdefault(key, {}).update(data))
    return sync_points


def _out_of_scope_record():
    return MagicMock(id="r1", external_record_id="b1/other/x.pdf", mime_type="application/pdf")


class TestFolderFilter:
    """The "Folders" sync filter: only the chosen folders are listed and synced."""

    @staticmethod
    def _prepare(connector, objects_by_prefix):
        prefixes = []

        async def list_blobs(**kwargs):
            prefixes.append(kwargs.get("prefix"))
            contents = objects_by_prefix.get(kwargs.get("prefix"), [])
            return _make_response(True, {"Contents": [{"Key": k} for k in contents], "IsTruncated": False})

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = list_blobs
        connector.record_sync_point = _in_memory_sync_points()
        connector._process_gcs_object = AsyncMock(return_value=(None, []))
        connector._ensure_parent_folders_exist = AsyncMock()
        connector.data_entities_processor.get_records_in_record_group = AsyncMock(return_value=[])
        return prefixes

    @pytest.mark.asyncio
    async def test_include_lists_only_the_chosen_folder(self, connector):
        connector.sync_filters = _folder_filter(["reports"])
        prefixes = self._prepare(connector, {"reports/": ["reports/a.pdf"]})

        await connector._sync_bucket("b1")

        assert prefixes == ["reports/"]
        assert [c.args[0]["Key"] for c in connector._process_gcs_object.await_args_list] == ["reports/a.pdf"]
        connector.data_entities_processor.get_records_in_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_already_cleaned_scope_is_not_scanned_again(self, connector):
        connector.sync_filters = _folder_filter(["reports"])
        self._prepare(connector, {"reports/": []})

        await connector._sync_bucket("b1")
        await connector._sync_bucket("b1")

        connector.data_entities_processor.get_records_in_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    def _page_then_listing_error(self, connector, processed):
        connector.sync_filters = FilterCollection()
        self._prepare(connector, {})
        connector._process_gcs_object = AsyncMock(return_value=processed)
        connector._process_records_with_retry = AsyncMock()
        pages = iter([
            _make_response(True, {
                "Contents": [{"Key": "b.pdf", "LastModified": "2026-01-02T00:00:00Z"}],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            }),
            RuntimeError("network"),
        ])

        async def listing(**kwargs):
            page = next(pages)
            if isinstance(page, Exception):
                raise page
            return page

        connector.data_source.list_blobs = listing

    @pytest.mark.asyncio
    async def test_a_listing_error_saves_no_checkpoint(self, connector):
        self._page_then_listing_error(connector, (MagicMock(), []))

        await connector._sync_bucket("b1")

        assert [c.args[0]["Key"] for c in connector._process_gcs_object.await_args_list] == ["b.pdf"]
        saved = connector.record_sync_point.saved
        assert not any("last_sync_time" in v for v in saved.values())
        assert {"page_token": "t1"} in saved.values()

    @pytest.mark.asyncio
    async def test_a_failed_object_clears_the_resume_token(self, connector):
        # Resuming from the page token would skip page one, where the failed object is.
        self._page_then_listing_error(connector, (None, []))

        await connector._sync_bucket("b1")

        saved = connector.record_sync_point.saved
        assert saved["FILE/bucket/b1"] == {"page_token": None}

    @pytest.mark.asyncio
    async def test_exclude_skips_the_folder(self, connector):
        connector.sync_filters = _folder_filter(["tmp"], exclude=True)
        prefixes = self._prepare(connector, {None: ["a.pdf", "tmp/cache.bin"]})

        await connector._sync_bucket("b1")

        assert prefixes == [None]
        assert [c.args[0]["Key"] for c in connector._process_gcs_object.await_args_list] == ["a.pdf"]


_JAN = [datetime(2026, 1, day, tzinfo=timezone.utc) for day in (1, 2, 3)]


def _ms(moment):
    return int(moment.timestamp() * 1000)


class TestFailedObjectCheckpoint:
    """An object that fails to process holds the checkpoint back, so the next sync retries it."""

    @staticmethod
    def _sync_points():
        saved = {}
        sync_points = MagicMock()
        sync_points.saved = saved
        sync_points.read_sync_point = AsyncMock(side_effect=lambda key: saved.get(key))
        sync_points.update_sync_point = AsyncMock(side_effect=lambda key, data: saved.setdefault(key, {}).update(data))
        return sync_points

    def _prepare(self, connector, objects, failing=(), raising=()):
        async def list_blobs(**kwargs):
            contents = [{"Key": key, "LastModified": at} for key, at in objects]
            return _make_response(True, {"Contents": contents, "IsTruncated": False})

        async def process(obj, bucket_name):
            if obj["Key"] in raising:
                raise RuntimeError("boom")
            return (None, []) if obj["Key"] in failing else (MagicMock(), [])

        connector.sync_filters = FilterCollection()
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = list_blobs
        connector.record_sync_point = self._sync_points()
        connector._ensure_parent_folders_exist = AsyncMock()
        connector._process_gcs_object = AsyncMock(side_effect=process)
        connector._process_records_with_retry = AsyncMock()

    @staticmethod
    def _saved_time(connector):
        return connector.record_sync_point.saved.get("FILE/bucket/b1", {}).get("last_sync_time")

    @staticmethod
    async def _sync(connector):
        from app.connectors.core.registry.folder_scope import FolderScope

        await connector._sync_bucket_prefix("b1", "", FolderScope())

    @pytest.mark.asyncio
    async def test_a_failed_object_holds_the_checkpoint_before_it(self, connector):
        self._prepare(connector, [("a.pdf", _JAN[0]), ("b.pdf", _JAN[1]), ("c.pdf", _JAN[2])], failing={"b.pdf"})

        await self._sync(connector)

        assert self._saved_time(connector) == _ms(_JAN[1]) - 1

    @pytest.mark.asyncio
    async def test_an_object_that_raises_holds_the_checkpoint_too(self, connector):
        self._prepare(connector, [("a.pdf", _JAN[0]), ("b.pdf", _JAN[1]), ("c.pdf", _JAN[2])], raising={"b.pdf"})

        await self._sync(connector)

        assert self._saved_time(connector) == _ms(_JAN[1]) - 1

    @pytest.mark.asyncio
    async def test_the_next_sync_retries_it_and_then_advances(self, connector):
        objects = [("a.pdf", _JAN[0]), ("b.pdf", _JAN[1]), ("c.pdf", _JAN[2])]
        self._prepare(connector, objects, failing={"b.pdf"})
        await self._sync(connector)
        saved = connector.record_sync_point

        self._prepare(connector, objects)
        connector.record_sync_point = saved
        await self._sync(connector)

        retried = [c.args[0]["Key"] for c in connector._process_gcs_object.await_args_list]
        assert retried == ["b.pdf", "c.pdf"]
        assert self._saved_time(connector) == _ms(_JAN[2])

    @pytest.mark.asyncio
    async def test_a_filtered_out_object_does_not_hold_the_checkpoint(self, connector):
        self._prepare(connector, [("a.pdf", _JAN[0]), ("b.txt", _JAN[1]), ("c.pdf", _JAN[2])])
        connector.sync_filters = FilterCollection(
            filters=[Filter(key="file_extensions", value=["pdf"], type=FilterType.LIST, operator=ListOperator.IN)]
        )

        await self._sync(connector)

        assert self._saved_time(connector) == _ms(_JAN[2])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", [{"b.pdf"}, set()])
    async def test_a_failed_final_save_clears_the_token_and_fails_the_sync(self, connector, failing):
        # The unsaved records sit on pages the resume token would skip.
        self._prepare(connector, [], failing=failing)
        connector.batch_size = 100
        pages = iter([
            _make_response(True, {
                "Contents": [{"Key": k, "LastModified": at} for k, at in zip(("a.pdf", "b.pdf", "c.pdf"), _JAN)],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            }),
            _make_response(True, {"Contents": [], "IsTruncated": False}),
        ])

        async def listing(**kwargs):
            return next(pages)

        connector.data_source.list_blobs = listing
        connector._process_records_with_retry = AsyncMock(side_effect=RuntimeError("db"))

        with pytest.raises(RuntimeError, match="db"):
            await self._sync(connector)

        assert connector.record_sync_point.saved["FILE/bucket/b1"] == {"page_token": None}

    @pytest.mark.asyncio
    async def test_a_failed_token_clear_does_not_hide_the_save_error(self, connector):
        self._prepare(connector, [("a.pdf", _JAN[0])])
        connector.batch_size = 100
        connector._process_records_with_retry = AsyncMock(side_effect=RuntimeError("db"))
        connector.record_sync_point.update_sync_point = AsyncMock(side_effect=OSError("kv down"))

        with pytest.raises(RuntimeError, match="db"):
            await self._sync(connector)
