"""Tests for Google Cloud Storage connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "serviceAccountJson": '{"type":"service_account","project_id":"test"}',
        },
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


# ===========================================================================
# Helper functions
# ===========================================================================

class TestGCSGetFileExtension:
    def test_normal(self):
        assert get_file_extension("report.pdf") == "pdf"

    def test_no_ext(self):
        assert get_file_extension("README") is None

    def test_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"


class TestGCSGetParentPath:
    def test_nested(self):
        assert get_parent_path_from_key("a/b/c/file.txt") == "a/b/c"

    def test_root(self):
        assert get_parent_path_from_key("file.txt") is None

    def test_empty(self):
        assert get_parent_path_from_key("") is None

    def test_trailing_slash(self):
        assert get_parent_path_from_key("a/b/c/") == "a/b"


class TestGCSFolderPathSegments:
    def test_nested(self):
        segs = get_folder_path_segments_from_key("a/b/c/file.txt")
        assert segs == ["a", "a/b", "a/b/c"]

    def test_root(self):
        assert get_folder_path_segments_from_key("file.txt") == []

    def test_empty(self):
        assert get_folder_path_segments_from_key("") == []


class TestGCSMimeType:
    def test_folder(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_gcs("folder/", is_folder=True) == MimeTypes.FOLDER.value

    def test_pdf(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_gcs("report.pdf") == MimeTypes.PDF.value

    def test_unknown(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_gcs("data.xyz999") == MimeTypes.BIN.value


class TestGCSParseParentExternalId:
    def test_with_path(self):
        bucket, path = parse_parent_external_id("mybucket/path/to/dir")
        assert bucket == "mybucket"
        assert path == "path/to/dir/"

    def test_bucket_only(self):
        bucket, path = parse_parent_external_id("mybucket")
        assert bucket == "mybucket"
        assert path is None


class TestGCSGetParentWeburl:
    def test_with_path(self):
        url = get_parent_weburl_for_gcs("mybucket/folder/")
        assert "console.cloud.google.com/storage/browser" in url
        assert "mybucket" in url

    def test_bucket_only(self):
        url = get_parent_weburl_for_gcs("mybucket")
        assert "console.cloud.google.com/storage/browser/mybucket" in url


class TestGCSGetParentPath:
    def test_with_path(self):
        result = get_parent_path_for_gcs("bucket/folder")
        assert result == "folder/"

    def test_bucket_only(self):
        assert get_parent_path_for_gcs("bucket") is None


# ===========================================================================
# GCSConnector
# ===========================================================================

class TestGCSConnectorInit:
    def test_constructor(self, gcs_connector):
        assert gcs_connector.connector_id == "gcs-conn-1"
        assert gcs_connector.data_source is None
        assert gcs_connector.filter_key == "gcs"

    @patch("app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.google_cloud_storage.connector.GCSDataSource")
    @patch("app.connectors.sources.google_cloud_storage.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build,
                                gcs_connector):
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
        result = await gcs_connector.init()
        assert result is False

    async def test_init_fails_no_service_account(self, gcs_connector):
        gcs_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {}
        })
        result = await gcs_connector.init()
        assert result is False

    @patch("app.connectors.sources.google_cloud_storage.connector.GCSClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, gcs_connector):
        mock_build.side_effect = Exception("Auth failed")
        result = await gcs_connector.init()
        assert result is False


class TestGCSEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = GCSDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc is not None
