"""Tests for MinIO connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.s3.base_connector import parse_parent_external_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.minio")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-minio-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.base_console_url = "http://localhost:9000"
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
            "accessKey": "minioadmin",
            "secretKey": "minioadmin",
            "endpointUrl": "http://localhost:9000",
        },
        "scope": "PERSONAL",
        "created_by": "user-1",
    })
    return svc


@pytest.fixture()
def minio_connector(mock_logger, mock_data_entities_processor,
                    mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.minio.connector.MinIOApp"):
        connector = MinIOConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="minio-conn-1",
            endpoint_url="http://localhost:9000",
        )
    return connector


# ===========================================================================
# MinIOConnector
# ===========================================================================

class TestMinIOParseConsoleUrl:
    def test_http(self):
        assert MinIOConnector._parse_console_url("http://localhost:9000") == "http://localhost:9000"

    def test_https(self):
        assert MinIOConnector._parse_console_url("https://minio.example.com") == "https://minio.example.com"

    def test_with_path(self):
        result = MinIOConnector._parse_console_url("http://minio.example.com/path")
        assert result == "http://minio.example.com"


class TestMinIOConnectorInit:
    def test_constructor(self, minio_connector):
        assert minio_connector.connector_id == "minio-conn-1"
        assert minio_connector.endpoint_url == "http://localhost:9000"
        assert minio_connector.data_source is None

    @patch("app.connectors.sources.minio.connector.MinIOClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.minio.connector.MinIODataSource")
    @patch("app.connectors.sources.minio.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build,
                                minio_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await minio_connector.init()
        assert result is True
        assert minio_connector.data_source is not None

    async def test_init_fails_no_config(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await minio_connector.init()
        assert result is False

    async def test_init_fails_missing_keys(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key"}
        })
        result = await minio_connector.init()
        assert result is False

    async def test_init_fails_no_endpoint(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key", "secretKey": "secret"}
        })
        result = await minio_connector.init()
        assert result is False

    @patch("app.connectors.sources.minio.connector.MinIOClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, minio_connector):
        mock_build.side_effect = Exception("Connection refused")
        result = await minio_connector.init()
        assert result is False


class TestMinIOWebUrls:
    def test_generate_web_url(self, minio_connector):
        url = minio_connector._generate_web_url("mybucket", "path/file.txt")
        assert "localhost:9000" in url
        assert "browser/mybucket/path/file.txt" in url

    def test_generate_parent_web_url_with_path(self, minio_connector):
        url = minio_connector._generate_parent_web_url("mybucket/folder")
        assert "browser/mybucket" in url

    def test_generate_parent_web_url_bucket_only(self, minio_connector):
        url = minio_connector._generate_parent_web_url("mybucket")
        assert "browser/mybucket" in url


class TestMinIOBucketRegion:
    async def test_returns_default(self, minio_connector):
        region = await minio_connector._get_bucket_region("mybucket")
        assert region == "us-east-1"

    async def test_caches_region(self, minio_connector):
        await minio_connector._get_bucket_region("mybucket")
        assert "mybucket" in minio_connector.bucket_regions
        # Second call uses cache
        region = await minio_connector._get_bucket_region("mybucket")
        assert region == "us-east-1"
