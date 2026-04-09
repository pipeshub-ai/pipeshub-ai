"""Comprehensive tests for Google Cloud Storage connector - extended coverage."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    tx.ensure_team_app_edge = AsyncMock()
    tx.remove_record_from_parent = AsyncMock()
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


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.gcs_comp")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gcs-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.get_user_by_user_id = AsyncMock(
        return_value=User(
            email="user@test.com",
            source_user_id="src-1",
            org_id="org-gcs-1",
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
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.google_cloud_storage.connector.GCSApp"):
        c = GCSConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="gcs-comp-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


# ===========================================================================
# Helper functions - extended
# ===========================================================================
class TestHelperFunctionsComprehensive:
    def test_get_file_extension_nested(self):
        assert get_file_extension("a/b/c/report.pdf") == "pdf"

    def test_get_file_extension_no_dots(self):
        assert get_file_extension("README") is None

    def test_get_file_extension_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path_from_key_deeply_nested(self):
        assert get_parent_path_from_key("a/b/c/d/e/file.txt") == "a/b/c/d/e"

    def test_get_parent_path_from_key_root_file(self):
        assert get_parent_path_from_key("file.txt") is None

    def test_get_parent_path_from_key_empty(self):
        assert get_parent_path_from_key("") is None

    def test_get_parent_path_from_key_slash_only(self):
        assert get_parent_path_from_key("/") is None

    def test_get_parent_path_from_key_leading_slash(self):
        result = get_parent_path_from_key("/a/b/file.txt")
        assert result == "a/b"

    def test_folder_segments_deep(self):
        segments = get_folder_path_segments_from_key("a/b/c/d/file.txt")
        assert segments == ["a", "a/b", "a/b/c", "a/b/c/d"]

    def test_folder_segments_single_file(self):
        assert get_folder_path_segments_from_key("file.txt") == []

    def test_folder_segments_empty(self):
        assert get_folder_path_segments_from_key("") == []

    def test_folder_segments_single_dir(self):
        segments = get_folder_path_segments_from_key("dir/file.txt")
        assert segments == ["dir"]

    def test_mimetype_folder(self):
        assert get_mimetype_for_gcs("anything", is_folder=True) == MimeTypes.FOLDER.value

    def test_mimetype_pdf(self):
        assert get_mimetype_for_gcs("test.pdf") == MimeTypes.PDF.value

    def test_mimetype_unknown(self):
        assert get_mimetype_for_gcs("test.xyz789") == MimeTypes.BIN.value

    def test_mimetype_no_extension(self):
        assert get_mimetype_for_gcs("Makefile") == MimeTypes.BIN.value

    def test_parse_parent_with_path(self):
        bucket, path = parse_parent_external_id("mybucket/subdir/")
        assert bucket == "mybucket"
        assert path == "subdir/"

    def test_parse_parent_bucket_only(self):
        bucket, path = parse_parent_external_id("mybucket")
        assert bucket == "mybucket"
        assert path is None

    def test_parse_parent_with_path_no_trailing_slash(self):
        bucket, path = parse_parent_external_id("mybucket/subdir")
        assert bucket == "mybucket"
        assert path == "subdir/"

    def test_parse_parent_with_leading_slash_in_path(self):
        bucket, path = parse_parent_external_id("mybucket//subdir")
        assert bucket == "mybucket"
        assert path == "subdir/"

    def test_get_parent_weburl_with_path(self):
        url = get_parent_weburl_for_gcs("mybucket/subdir/")
        assert "mybucket" in url
        assert "subdir" in url

    def test_get_parent_weburl_bucket_only(self):
        url = get_parent_weburl_for_gcs("mybucket")
        assert "mybucket" in url

    def test_get_parent_path_for_gcs_with_path(self):
        result = get_parent_path_for_gcs("mybucket/subdir/")
        assert result == "subdir/"

    def test_get_parent_path_for_gcs_bucket_only(self):
        result = get_parent_path_for_gcs("mybucket")
        assert result is None


# ===========================================================================
# GCSDataSourceEntitiesProcessor
# ===========================================================================
class TestGCSProcessorComprehensive:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = GCSDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc is not None


# ===========================================================================
# Init
# ===========================================================================
class TestInitComprehensive:
    def test_constructor(self, connector):
        assert connector.connector_id == "gcs-comp-1"

    @pytest.mark.asyncio
    async def test_init_fails_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_fails_no_service_account(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await connector.init()
        assert result is False


# ===========================================================================
# URL generation
# ===========================================================================
class TestURLGeneration:
    def test_generate_web_url(self, connector):
        url = connector._generate_web_url("mybucket", "path/to/file.txt")
        assert "mybucket" in url
        assert "file.txt" in url

    def test_generate_parent_web_url(self, connector):
        url = connector._generate_parent_web_url("mybucket/subdir/")
        assert "mybucket" in url


# ===========================================================================
# get_app_users
# ===========================================================================
class TestGetAppUsers:
    def test_converts_users(self, connector):
        user = User(
            email="user@test.com",
            full_name="Test User",
        )
        result = connector.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "user@test.com"


# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestGetDateFilters:
    def test_no_filters(self, connector):
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert all(v is None for v in result)

    def test_with_created_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.CREATED else None

        result = connector._get_date_filters()
        assert result[2] is not None  # created_after_ms


# ===========================================================================
# _pass_date_filters
# ===========================================================================
class TestPassDateFiltersComprehensive:
    def test_folder_always_passes(self, connector):
        assert connector._pass_date_filters({"Key": "some/folder/"}) is True

    def test_no_filters_passes(self, connector):
        connector.modified_after_cutoff = None
        connector.modified_before_cutoff = None
        connector.created_after_cutoff = None
        connector.created_before_cutoff = None
        assert connector._pass_date_filters({}) is True

    def test_with_iso_string(self, connector):
        connector.modified_after_cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        connector.modified_before_cutoff = None
        connector.created_after_cutoff = None
        connector.created_before_cutoff = None
        result = connector._pass_date_filters({"timeCreated": "2025-06-01T00:00:00Z"})
        assert result is True

    def test_with_datetime_object(self, connector):
        connector.modified_after_cutoff = None
        connector.modified_before_cutoff = None
        connector.created_after_cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        connector.created_before_cutoff = None
        dt_val = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result = connector._pass_date_filters({"timeCreated": dt_val})
        assert result is True


# ===========================================================================
# _get_gcs_revision_id
# ===========================================================================
class TestGetGcsRevisionId:
    def test_with_etag(self, connector):
        obj = {"Md5Hash": "abc123"}
        result = connector._get_gcs_revision_id(obj)
        assert "abc123" in result

    def test_with_generation(self, connector):
        obj = {"Generation": "12345"}
        result = connector._get_gcs_revision_id(obj)
        assert "12345" in result

    def test_with_updated_timestamp(self, connector):
        obj = {"updated": "2025-01-15T00:00:00Z"}
        result = connector._get_gcs_revision_id(obj)
        assert result is not None

    def test_fallback(self, connector):
        obj = {}
        result = connector._get_gcs_revision_id(obj)
        assert result is not None  # Should still return something


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncComprehensive:
    @pytest.mark.asyncio
    async def test_run_sync_not_initialized(self, connector):
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector.data_source = None
            with pytest.raises(Exception):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_configured_bucket(self, connector):
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector.data_source = AsyncMock()
            connector.bucket_name = "test-bucket"
            connector._create_record_groups_for_buckets = AsyncMock()
            connector._sync_bucket = AsyncMock()

            await connector.run_sync()

            connector._sync_bucket.assert_awaited_once_with("test-bucket")


# ===========================================================================
# _create_record_groups_for_buckets
# ===========================================================================
class TestCreateRecordGroupsForBuckets:
    @pytest.mark.asyncio
    async def test_empty_list(self, connector):
        await connector._create_record_groups_for_buckets([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_team_scope(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        await connector._create_record_groups_for_buckets(["bucket1", "bucket2"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited()


# ===========================================================================
# Misc
# ===========================================================================
class TestMiscComprehensive:
    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self, connector):
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self, connector):
        connector.data_source = MagicMock()
        connector.bucket_name = "test-bucket"
        connector._sync_bucket = AsyncMock()
        with patch(
            "app.connectors.sources.google_cloud_storage.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_incremental_sync()
            connector._sync_bucket.assert_awaited_once_with("test-bucket")

    @pytest.mark.asyncio
    async def test_process_records_with_retry_success(self, connector):
        records = [(MagicMock(), [])]
        await connector._process_records_with_retry(records)
        connector.data_entities_processor.on_new_records.assert_awaited_once_with(records)

    @pytest.mark.asyncio
    async def test_process_records_with_retry_non_lock_error(self, connector):
        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("non-lock error")
        )
        with pytest.raises(Exception, match="non-lock error"):
            await connector._process_records_with_retry([(MagicMock(), [])])
