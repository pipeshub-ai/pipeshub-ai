"""Tests for S3 connector and S3-compatible base connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.s3")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-s3-1"
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
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
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


# ===========================================================================
# Helper functions
# ===========================================================================

class TestS3GetFileExtension:
    def test_normal(self):
        assert get_file_extension("file.pdf") == "pdf"

    def test_no_ext(self):
        assert get_file_extension("Makefile") is None

    def test_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"


class TestS3GetParentPath:
    def test_nested(self):
        assert get_parent_path_from_key("a/b/c/file.txt") == "a/b/c"

    def test_root(self):
        assert get_parent_path_from_key("file.txt") is None

    def test_empty(self):
        assert get_parent_path_from_key("") is None

    def test_trailing_slash(self):
        assert get_parent_path_from_key("a/b/c/") == "a/b"


class TestS3FolderPathSegments:
    def test_nested(self):
        segs = get_folder_path_segments_from_key("a/b/c/file.txt")
        assert segs == ["a", "a/b", "a/b/c"]

    def test_root(self):
        assert get_folder_path_segments_from_key("file.txt") == []

    def test_empty(self):
        assert get_folder_path_segments_from_key("") == []

    def test_leading_slash(self):
        segs = get_folder_path_segments_from_key("/a/b/file.txt")
        assert segs == ["a", "a/b"]


class TestS3MimeType:
    def test_folder(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_s3("folder/", is_folder=True) == MimeTypes.FOLDER.value

    def test_pdf(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_s3("report.pdf") == MimeTypes.PDF.value

    def test_unknown(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_s3("data.unknownext") == MimeTypes.BIN.value


class TestParseParentExternalId:
    def test_with_path(self):
        bucket, path = parse_parent_external_id("mybucket/path/to/dir")
        assert bucket == "mybucket"
        assert path == "path/to/dir/"

    def test_bucket_only(self):
        bucket, path = parse_parent_external_id("mybucket")
        assert bucket == "mybucket"
        assert path is None


class TestGetParentWeburlForS3:
    def test_with_path(self):
        url = get_parent_weburl_for_s3("mybucket/folder/")
        assert "s3.console.aws.amazon.com" in url
        assert "mybucket" in url

    def test_bucket_only(self):
        url = get_parent_weburl_for_s3("mybucket")
        assert "s3/buckets/mybucket" in url

    def test_custom_base_url(self):
        url = get_parent_weburl_for_s3("mybucket/dir", "http://minio:9000")
        assert "minio:9000" in url


class TestGetParentPathForS3:
    def test_with_path(self):
        result = get_parent_path_for_s3("bucket/folder")
        assert result == "folder/"

    def test_bucket_only(self):
        assert get_parent_path_for_s3("bucket") is None


class TestMakeS3CompositeRevision:
    def test_with_etag(self):
        result = make_s3_composite_revision("mybucket", "file.txt", "abc123")
        assert result == "mybucket/abc123"

    def test_without_etag(self):
        result = make_s3_composite_revision("mybucket", "file.txt", None)
        assert result == "mybucket/file.txt|"


# ===========================================================================
# S3Connector
# ===========================================================================

class TestS3ConnectorInit:
    def test_constructor(self, s3_connector):
        assert s3_connector.connector_id == "s3-conn-1"
        assert s3_connector.data_source is None

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.s3.connector.S3DataSource")
    @patch("app.connectors.sources.s3.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build,
                                s3_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await s3_connector.init()
        assert result is True
        assert s3_connector.data_source is not None

    async def test_init_fails_no_config(self, s3_connector):
        s3_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await s3_connector.init()
        assert result is False

    async def test_init_fails_missing_keys(self, s3_connector):
        s3_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key"}
        })
        result = await s3_connector.init()
        assert result is False

    @patch("app.connectors.sources.s3.connector.S3Client.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, s3_connector):
        mock_build.side_effect = Exception("Connection failed")
        result = await s3_connector.init()
        assert result is False


class TestS3ConnectorWebUrl:
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


class TestS3BaseDateFilters:
    def test_pass_date_filters_folder(self, s3_connector):
        obj = {"Key": "folder/"}
        assert s3_connector._pass_date_filters(obj, 100, None, None, None) is True

    def test_pass_date_filters_no_filters(self, s3_connector):
        obj = {"Key": "file.txt"}
        assert s3_connector._pass_date_filters(obj, None, None, None, None) is True

    def test_pass_date_filters_modified_after(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        # Set cutoff in the future
        future_ms = int((now.timestamp() + 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, future_ms, None, None, None) is False

    def test_pass_date_filters_no_last_modified(self, s3_connector):
        obj = {"Key": "file.txt"}
        assert s3_connector._pass_date_filters(obj, 100, None, None, None) is True


class TestS3CompatibleEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc.base_console_url == "https://s3.console.aws.amazon.com"

    def test_custom_console_url(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            base_console_url="http://minio:9000",
        )
        assert proc.base_console_url == "http://minio:9000"
