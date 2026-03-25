"""Tests for Google Cloud Storage connector."""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
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
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.gcs")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gcs-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {"serviceAccountJson": '{"type":"service_account","project_id":"test"}'},
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def gcs_connector(mock_logger, mock_data_entities_processor,
                  mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.google_cloud_storage.connector.GCSApp"):
        connector = GCSConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="gcs-conn-1",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# Helper functions
# ===========================================================================
class TestGCSHelpers:
    def test_get_file_extension_normal(self):
        assert get_file_extension("report.pdf") == "pdf"

    def test_get_file_extension_none(self):
        assert get_file_extension("README") is None

    def test_get_file_extension_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path_nested(self):
        assert get_parent_path_from_key("a/b/c/file.txt") == "a/b/c"

    def test_get_parent_path_root(self):
        assert get_parent_path_from_key("file.txt") is None

    def test_get_parent_path_empty(self):
        assert get_parent_path_from_key("") is None

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_key("a/b/c/") == "a/b"

    def test_folder_segments_nested(self):
        assert get_folder_path_segments_from_key("a/b/c/file.txt") == ["a", "a/b", "a/b/c"]

    def test_folder_segments_root(self):
        assert get_folder_path_segments_from_key("file.txt") == []

    def test_folder_segments_empty(self):
        assert get_folder_path_segments_from_key("") == []

    def test_mimetype_folder(self):
        assert get_mimetype_for_gcs("folder/", is_folder=True) == MimeTypes.FOLDER.value

    def test_mimetype_pdf(self):
        assert get_mimetype_for_gcs("report.pdf") == MimeTypes.PDF.value

    def test_mimetype_unknown(self):
        assert get_mimetype_for_gcs("data.xyz999") == MimeTypes.BIN.value

    def test_parse_parent_with_path(self):
        bucket, path = parse_parent_external_id("mybucket/path/to/dir")
        assert bucket == "mybucket"
        assert path == "path/to/dir/"

    def test_parse_parent_bucket_only(self):
        bucket, path = parse_parent_external_id("mybucket")
        assert bucket == "mybucket"
        assert path is None

    def test_get_parent_weburl_with_path(self):
        url = get_parent_weburl_for_gcs("mybucket/folder/")
        assert "console.cloud.google.com/storage/browser" in url

    def test_get_parent_weburl_bucket_only(self):
        url = get_parent_weburl_for_gcs("mybucket")
        assert "console.cloud.google.com/storage/browser/mybucket" in url

    def test_get_parent_path_for_gcs_with_path(self):
        result = get_parent_path_for_gcs("bucket/folder")
        assert result == "folder/"

    def test_get_parent_path_for_gcs_bucket_only(self):
        assert get_parent_path_for_gcs("bucket") is None


# ===========================================================================
# GCSDataSourceEntitiesProcessor
# ===========================================================================
class TestGCSEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = GCSDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc is not None


# ===========================================================================
# GCSConnector Init
# ===========================================================================
class TestGCSConnectorInit:
    def test_constructor(self, gcs_connector):
        assert gcs_connector.connector_id == "gcs-conn-1"
        assert gcs_connector.data_source is None
        assert gcs_connector.filter_key == "gcs"

    @patch("app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.google_cloud_storage.connector.GCSDataSource")
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build, gcs_connector):
        mock_client = MagicMock()
        mock_client.get_project_id.return_value = "test-project"
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await gcs_connector.init()
        assert result is True
        assert gcs_connector.project_id == "test-project"

    async def test_init_fails_no_config(self, gcs_connector):
        gcs_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await gcs_connector.init() is False

    async def test_init_fails_no_service_account(self, gcs_connector):
        gcs_connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await gcs_connector.init() is False

    @patch("app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, gcs_connector):
        mock_build.side_effect = Exception("Auth failed")
        assert await gcs_connector.init() is False


# ===========================================================================
# URL generation
# ===========================================================================
class TestGCSWebUrls:
    def test_generate_web_url(self, gcs_connector):
        url = gcs_connector._generate_web_url("mybucket", "path/file.txt")
        assert "console.cloud.google.com/storage/browser/mybucket" in url

    def test_generate_parent_web_url(self, gcs_connector):
        url = gcs_connector._generate_parent_web_url("mybucket/folder")
        assert "mybucket" in url


# ===========================================================================
# Date filters
# ===========================================================================
class TestGCSDateFilters:
    def test_get_date_filters_empty(self, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        gcs_connector.sync_filters = FilterCollection()
        ma, mb, ca, cb = gcs_connector._get_date_filters()
        assert all(x is None for x in (ma, mb, ca, cb))

    def test_pass_date_filters_folder(self, gcs_connector):
        assert gcs_connector._pass_date_filters({"Key": "folder/"}, 100, None, None, None) is True

    def test_pass_date_filters_no_filters(self, gcs_connector):
        assert gcs_connector._pass_date_filters({"Key": "file.txt"}, None, None, None, None) is True

    def test_pass_date_filters_no_last_modified(self, gcs_connector):
        assert gcs_connector._pass_date_filters({"Key": "file.txt"}, 100, None, None, None) is True

    def test_pass_date_filters_modified_after_pass(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "2024-06-01T12:00:00Z"}
        assert gcs_connector._pass_date_filters(obj, 1000, None, None, None) is True

    def test_pass_date_filters_modified_after_fail(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "2024-01-01T00:00:00Z"}
        future_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert gcs_connector._pass_date_filters(obj, future_ms, None, None, None) is False

    def test_pass_date_filters_modified_before_fail(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "2024-06-01T12:00:00Z"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert gcs_connector._pass_date_filters(obj, None, past_ms, None, None) is False

    def test_pass_date_filters_created_after_fail(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "2024-06-01T12:00:00Z", "TimeCreated": "2024-01-01T00:00:00Z"}
        future_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert gcs_connector._pass_date_filters(obj, None, None, future_ms, None) is False

    def test_pass_date_filters_created_before_fail(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "2024-06-01T12:00:00Z", "TimeCreated": "2024-06-01T00:00:00Z"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert gcs_connector._pass_date_filters(obj, None, None, None, past_ms) is False

    def test_pass_date_filters_invalid_iso(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": "not-a-date"}
        assert gcs_connector._pass_date_filters(obj, 100, None, None, None) is True

    def test_pass_date_filters_non_string_last_modified(self, gcs_connector):
        obj = {"Key": "file.txt", "LastModified": 12345}
        assert gcs_connector._pass_date_filters(obj, 100, None, None, None) is True


# ===========================================================================
# Run sync
# ===========================================================================
class TestGCSRunSync:
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_not_initialized(self, mock_filters, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        gcs_connector.data_source = None
        with pytest.raises(ConnectionError):
            await gcs_connector.run_sync()

    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_with_configured_bucket(self, mock_filters, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        gcs_connector.data_source = MagicMock()
        gcs_connector.bucket_name = "mybucket"
        gcs_connector._create_record_groups_for_buckets = AsyncMock()
        gcs_connector._sync_bucket = AsyncMock()
        await gcs_connector.run_sync()
        gcs_connector._sync_bucket.assert_awaited_once_with("mybucket")

    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_list_buckets(self, mock_filters, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        gcs_connector.data_source = MagicMock()
        gcs_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"name": "b1"}, {"name": "b2"}]})
        )
        gcs_connector._create_record_groups_for_buckets = AsyncMock()
        gcs_connector._sync_bucket = AsyncMock()
        await gcs_connector.run_sync()
        assert gcs_connector._sync_bucket.await_count == 2

    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_list_buckets_fails(self, mock_filters, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        gcs_connector.data_source = MagicMock()
        gcs_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(False, error="Access denied")
        )
        await gcs_connector.run_sync()

    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_no_buckets_found(self, mock_filters, gcs_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        gcs_connector.data_source = MagicMock()
        gcs_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": []})
        )
        await gcs_connector.run_sync()


# ===========================================================================
# Record groups for buckets
# ===========================================================================
class TestGCSRecordGroupsForBuckets:
    async def test_create_record_groups_empty(self, gcs_connector):
        await gcs_connector._create_record_groups_for_buckets([])

    async def test_create_record_groups_team_scope(self, gcs_connector):
        gcs_connector.connector_scope = ConnectorScope.TEAM.value
        await gcs_connector._create_record_groups_for_buckets(["bucket1"])
        gcs_connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_create_record_groups_personal_scope_with_creator(self, gcs_connector, mock_data_store_provider):
        gcs_connector.connector_scope = ConnectorScope.PERSONAL.value
        gcs_connector.created_by = "user-1"
        await gcs_connector._create_record_groups_for_buckets(["bucket1"])
        gcs_connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_create_record_groups_retry_on_lock(self, gcs_connector):
        gcs_connector.connector_scope = ConnectorScope.TEAM.value
        gcs_connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[Exception("timeout waiting to lock"), None]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await gcs_connector._create_record_groups_for_buckets(["bucket1"])


# ===========================================================================
# Process records with retry
# ===========================================================================
class TestGCSProcessRecordsRetry:
    async def test_success_first_try(self, gcs_connector):
        await gcs_connector._process_records_with_retry([(MagicMock(), [])])
        gcs_connector.data_entities_processor.on_new_records.assert_awaited_once()

    async def test_retry_on_lock_timeout(self, gcs_connector):
        gcs_connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=[Exception("timeout waiting to lock"), None]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await gcs_connector._process_records_with_retry([(MagicMock(), [])])

    async def test_raises_on_non_lock_error(self, gcs_connector):
        gcs_connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("Some other error")
        )
        with pytest.raises(Exception, match="Some other error"):
            await gcs_connector._process_records_with_retry([(MagicMock(), [])])


# ===========================================================================
# App users
# ===========================================================================
class TestGCSAppUsers:
    def test_get_app_users(self, gcs_connector):
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = gcs_connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].email == "a@test.com"
