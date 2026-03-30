"""Tests for S3 connector and S3-compatible base connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.s3.base_connector import (
    S3CompatibleBaseConnector,
    S3CompatibleDataSourceEntitiesProcessor,
    get_file_extension,
    get_folder_path_segments_from_key,
    get_mimetype_for_s3,
    get_parent_path_for_s3,
    get_parent_path_from_key,
    get_parent_weburl_for_s3,
    make_s3_composite_revision,
    parse_parent_external_id,
)
from app.connectors.sources.s3.connector import S3Connector
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.s3")


@pytest.fixture()
def mock_data_entities_processor():
    from app.models.entities import AppMetadata
    
    proc = MagicMock()
    proc.org_id = "org-s3-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.get_app_by_id = AsyncMock(return_value=AppMetadata(
        connector_id="s3-conn-1",
        name="S3 Connector",
        type="s3",
        app_group="STORAGE",
        scope="PERSONAL",
        created_by="user-1",
        created_at_timestamp=1234567890,
        updated_at_timestamp=1234567890,
    ))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "accessKey": "AKIAIOSFODNN7EXAMPLE",
            "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def s3_connector(mock_logger, mock_data_entities_processor,
                 mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.s3.connector.S3App"):
        connector = S3Connector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-conn-1",
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
class TestS3Helpers:
    def test_get_file_extension(self):
        assert get_file_extension("file.pdf") == "pdf"
        assert get_file_extension("Makefile") is None
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path(self):
        assert get_parent_path_from_key("a/b/c/file.txt") == "a/b/c"
        assert get_parent_path_from_key("file.txt") is None
        assert get_parent_path_from_key("") is None
        assert get_parent_path_from_key("a/b/c/") == "a/b"

    def test_folder_segments(self):
        assert get_folder_path_segments_from_key("a/b/c/file.txt") == ["a", "a/b", "a/b/c"]
        assert get_folder_path_segments_from_key("file.txt") == []
        assert get_folder_path_segments_from_key("") == []
        assert get_folder_path_segments_from_key("/a/b/file.txt") == ["a", "a/b"]

    def test_mimetype(self):
        assert get_mimetype_for_s3("folder/", is_folder=True) == MimeTypes.FOLDER.value
        assert get_mimetype_for_s3("report.pdf") == MimeTypes.PDF.value
        assert get_mimetype_for_s3("data.unknownext") == MimeTypes.BIN.value

    def test_parse_parent(self):
        bucket, path = parse_parent_external_id("mybucket/path/to/dir")
        assert bucket == "mybucket"
        assert path == "path/to/dir/"
        bucket, path = parse_parent_external_id("mybucket")
        assert path is None

    def test_get_parent_weburl(self):
        url = get_parent_weburl_for_s3("mybucket/folder/")
        assert "s3.console.aws.amazon.com" in url
        url = get_parent_weburl_for_s3("mybucket")
        assert "s3/buckets/mybucket" in url

    def test_get_parent_weburl_custom_base(self):
        url = get_parent_weburl_for_s3("mybucket/dir", "http://minio:9000")
        assert "minio:9000" in url

    def test_get_parent_path_for_s3(self):
        assert get_parent_path_for_s3("bucket/folder") == "folder/"
        assert get_parent_path_for_s3("bucket") is None

    def test_composite_revision_with_etag(self):
        assert make_s3_composite_revision("mybucket", "file.txt", "abc123") == "mybucket/abc123"

    def test_composite_revision_without_etag(self):
        assert make_s3_composite_revision("mybucket", "file.txt", None) == "mybucket/file.txt|"


# ===========================================================================
# S3Connector Init
# ===========================================================================
class TestS3ConnectorInit:
    def test_constructor(self, s3_connector):
        assert s3_connector.connector_id == "s3-conn-1"
        assert s3_connector.data_source is None

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build, s3_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        assert await s3_connector.init() is True
        assert s3_connector.data_source is not None

    async def test_init_fails_no_config(self, s3_connector):
        s3_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await s3_connector.init() is False

    async def test_init_fails_missing_keys(self, s3_connector):
        s3_connector.config_service.get_config = AsyncMock(return_value={"auth": {"accessKey": "key"}})
        assert await s3_connector.init() is False

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, s3_connector):
        mock_build.side_effect = Exception("Connection failed")
        assert await s3_connector.init() is False

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_with_scope_and_bucket(self, mock_filters, mock_ds_cls, mock_build, s3_connector):
        s3_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key", "secretKey": "secret", "bucket": "mybucket"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await s3_connector.init()
        assert result is True
        assert s3_connector.bucket_name == "mybucket"
        assert s3_connector.scope == "PERSONAL"


# ===========================================================================
# Web URLs
# ===========================================================================
class TestS3WebUrls:
    def test_generate_web_url(self, s3_connector):
        url = s3_connector._generate_web_url("mybucket", "path/file.txt")
        assert "s3.console.aws.amazon.com" in url
        assert "mybucket" in url

    def test_generate_parent_web_url_with_path(self, s3_connector):
        url = s3_connector._generate_parent_web_url("mybucket/folder")
        assert "mybucket" in url

    def test_generate_parent_web_url_bucket_only(self, s3_connector):
        url = s3_connector._generate_parent_web_url("mybucket")
        assert "s3/buckets/mybucket" in url


# ===========================================================================
# Date filters
# ===========================================================================
class TestS3DateFilters:
    def test_get_date_filters_empty(self, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        s3_connector.sync_filters = FilterCollection()
        ma, mb, ca, cb = s3_connector._get_date_filters()
        assert all(x is None for x in (ma, mb, ca, cb))

    def test_pass_date_filters_folder(self, s3_connector):
        assert s3_connector._pass_date_filters({"Key": "folder/"}, 100, None, None, None) is True

    def test_pass_date_filters_no_filters(self, s3_connector):
        assert s3_connector._pass_date_filters({"Key": "file.txt"}, None, None, None, None) is True

    def test_pass_date_filters_no_last_modified(self, s3_connector):
        assert s3_connector._pass_date_filters({"Key": "file.txt"}, 100, None, None, None) is True

    def test_pass_date_filters_modified_after_fail(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        future_ms = int((now.timestamp() + 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, future_ms, None, None, None) is False

    def test_pass_date_filters_modified_before_fail(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        past_ms = int((now.timestamp() - 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, None, past_ms, None, None) is False

    def test_pass_date_filters_created_after_fail(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        future_ms = int((now.timestamp() + 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, None, None, future_ms, None) is False

    def test_pass_date_filters_created_before_fail(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        past_ms = int((now.timestamp() - 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, None, None, None, past_ms) is False

    def test_pass_date_filters_non_datetime_last_modified(self, s3_connector):
        obj = {"Key": "file.txt", "LastModified": "not a datetime"}
        assert s3_connector._pass_date_filters(obj, 100, None, None, None) is True


# ===========================================================================
# Run sync
# ===========================================================================
class TestS3RunSync:
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_not_initialized(self, mock_filters, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = None
        with pytest.raises(ConnectionError):
            await s3_connector.run_sync()

    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_with_configured_bucket(self, mock_filters, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "mybucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._create_record_groups_for_buckets = AsyncMock()
        s3_connector._sync_bucket = AsyncMock()
        await s3_connector.run_sync()
        s3_connector._sync_bucket.assert_awaited_once_with("mybucket")

    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_list_buckets(self, mock_filters, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"Name": "b1"}, {"Name": "b2"}]})
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._create_record_groups_for_buckets = AsyncMock()
        s3_connector._sync_bucket = AsyncMock()
        await s3_connector.run_sync()
        assert s3_connector._sync_bucket.await_count == 2

    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_bucket_error_continues(self, mock_filters, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "mybucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._create_record_groups_for_buckets = AsyncMock()
        s3_connector._sync_bucket = AsyncMock(side_effect=Exception("Sync failed"))
        await s3_connector.run_sync()


# ===========================================================================
# Record groups
# ===========================================================================
class TestS3RecordGroups:
    async def test_create_record_groups_empty(self, s3_connector):
        await s3_connector._create_record_groups_for_buckets([])

    async def test_create_record_groups_team_scope(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_create_record_groups_personal_with_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited()

    async def test_create_record_groups_personal_no_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = None
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited()


# ===========================================================================
# Bucket region
# ===========================================================================
class TestS3BucketRegion:
    async def test_cached_region(self, s3_connector):
        s3_connector.bucket_regions = {"mybucket": "eu-west-1"}
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "eu-west-1"

    async def test_no_data_source(self, s3_connector):
        s3_connector.data_source = None
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    async def test_fetch_region_success(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(True, {"LocationConstraint": "eu-west-1"})
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "eu-west-1"
        assert "mybucket" in s3_connector.bucket_regions

    async def test_fetch_region_null_location(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(True, {"LocationConstraint": None})
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    async def test_fetch_region_failure(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(False, error="Access denied")
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    async def test_fetch_region_exception(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(side_effect=Exception("Network error"))
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"


# ===========================================================================
# Ensure parent folders
# ===========================================================================
class TestS3EnsureParentFolders:
    async def test_empty_segments(self, s3_connector):
        await s3_connector._ensure_parent_folders_exist("bucket", [])
        s3_connector.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_creates_folders(self, s3_connector):
        s3_connector._create_s3_permissions = AsyncMock(return_value=[])
        await s3_connector._ensure_parent_folders_exist("bucket", ["a", "a/b"])
        assert s3_connector.data_entities_processor.on_new_records.await_count == 2


# ===========================================================================
# Process S3 object
# ===========================================================================
class TestS3ProcessObject:
    async def test_empty_key(self, s3_connector):
        record, perms = await s3_connector._process_s3_object({"Key": ""}, "bucket")
        assert record is None

    async def test_new_file(self, s3_connector):
        s3_connector._create_s3_permissions = AsyncMock(return_value=[])
        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc123"',
            "Size": 1024,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.record_name == "file.txt"
        assert record.is_file is True

    async def test_folder_object(self, s3_connector):
        s3_connector._create_s3_permissions = AsyncMock(return_value=[])
        obj = {"Key": "path/folder/", "LastModified": datetime.now(timezone.utc), "ETag": "", "Size": 0}
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.is_file is False
        assert record.mime_type == MimeTypes.FOLDER.value


# ===========================================================================
# App users
# ===========================================================================
class TestS3AppUsers:
    def test_get_app_users(self, s3_connector):
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = s3_connector.get_app_users(users)
        assert len(app_users) == 1


# ===========================================================================
# Entities processor
# ===========================================================================
class TestS3EntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc.base_console_url == "https://s3.console.aws.amazon.com"

    def test_custom_console_url(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            base_console_url="http://minio:9000",
        )
        assert proc.base_console_url == "http://minio:9000"

    def test_custom_parent_url_generator(self, mock_logger, mock_data_store_provider, mock_config_service):
        gen = lambda x: f"custom/{x}"
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            parent_url_generator=gen,
        )
        assert proc.parent_url_generator("test") == "custom/test"


# ===========================================================================
# Remove old parent relationship
# ===========================================================================
class TestS3RemoveOldParent:
    async def test_remove_old_parent(self, s3_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
        await s3_connector._remove_old_parent_relationship("rec-1", mock_tx)

    async def test_remove_old_parent_exception(self, s3_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.delete_parent_child_edge_to_record = AsyncMock(side_effect=Exception("fail"))
        await s3_connector._remove_old_parent_relationship("rec-1", mock_tx)
