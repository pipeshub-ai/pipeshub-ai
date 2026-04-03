"""Extended tests for MinIO connector covering init, URL generation, and factory method."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.s3.base_connector import parse_parent_external_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minio_connector():
    from app.models.entities import AppMetadata
    
    with patch("app.connectors.sources.minio.connector.MinIOApp"):
        logger = logging.getLogger("test.minio")
        dep = MagicMock()
        dep.org_id = "org-minio-1"
        dep.base_console_url = "http://localhost:9000"
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_records = AsyncMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.get_app_by_id = AsyncMock(return_value=AppMetadata(
            connector_id="minio-conn-1",
            name="MinIO Connector",
            type="minio",
            app_group="STORAGE",
            scope="PERSONAL",
            created_by="user-1",
            created_at_timestamp=1234567890,
            updated_at_timestamp=1234567890,
        ))
        ds_provider = MagicMock()
        config_service = AsyncMock()
        connector = MinIOConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="minio-conn-1",
            scope="personal",
            created_by="user-1",
            endpoint_url="http://localhost:9000",
        )
    return connector


# ===========================================================================
# _parse_console_url
# ===========================================================================

class TestParseConsoleUrl:
    def test_http_localhost(self):
        assert MinIOConnector._parse_console_url("http://localhost:9000") == "http://localhost:9000"

    def test_https_domain(self):
        assert MinIOConnector._parse_console_url("https://minio.example.com:9000") == "https://minio.example.com:9000"

    def test_with_path(self):
        assert MinIOConnector._parse_console_url("http://localhost:9000/path") == "http://localhost:9000"

    def test_minimal_url(self):
        assert MinIOConnector._parse_console_url("http://host") == "http://host"


# ===========================================================================
# URL generation
# ===========================================================================

class TestURLGeneration:
    def test_generate_web_url(self, minio_connector):
        url = minio_connector._generate_web_url("my-bucket", "path/to/file.txt")
        assert url == "http://localhost:9000/browser/my-bucket/path/to/file.txt"

    def test_generate_parent_web_url_with_path(self, minio_connector):
        url = minio_connector._generate_parent_web_url("my-bucket/path/to/")
        assert "browser/my-bucket" in url

    def test_generate_parent_web_url_bucket_only(self, minio_connector):
        url = minio_connector._generate_parent_web_url("my-bucket")
        assert url == "http://localhost:9000/browser/my-bucket"


# ===========================================================================
# _get_bucket_region
# ===========================================================================

class TestGetBucketRegion:
    async def test_returns_default(self, minio_connector):
        region = await minio_connector._get_bucket_region("test-bucket")
        assert region == "us-east-1"

    async def test_caches_region(self, minio_connector):
        await minio_connector._get_bucket_region("test-bucket")
        assert "test-bucket" in minio_connector.bucket_regions

    async def test_returns_cached(self, minio_connector):
        minio_connector.bucket_regions["cached-bucket"] = "us-west-2"
        region = await minio_connector._get_bucket_region("cached-bucket")
        assert region == "us-west-2"


# ===========================================================================
# init
# ===========================================================================

class TestMinIOInit:
    async def test_init_no_config(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await minio_connector.init()
        assert result is False

    async def test_init_no_access_key(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"secretKey": "secret", "endpointUrl": "http://localhost:9000"},
        })
        result = await minio_connector.init()
        assert result is False

    async def test_init_no_endpoint(self, minio_connector):
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key", "secretKey": "secret"},
        })
        result = await minio_connector.init()
        assert result is False

    @patch("app.connectors.sources.minio.connector.MinIOClient")
    @patch("app.connectors.sources.minio.connector.load_connector_filters")
    async def test_init_success(self, mock_filters, mock_client, minio_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        mock_client.build_from_services = AsyncMock(return_value=MagicMock())
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key", "secretKey": "secret", "endpointUrl": "http://localhost:9000"},
            "scope": "TEAM",
            "created_by": "user-1",
        })
        result = await minio_connector.init()
        assert result is True

    @patch("app.connectors.sources.minio.connector.MinIOClient")
    async def test_init_client_error(self, mock_client, minio_connector):
        mock_client.build_from_services = AsyncMock(side_effect=Exception("client error"))
        minio_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accessKey": "key", "secretKey": "secret", "endpointUrl": "http://localhost:9000"},
        })
        result = await minio_connector.init()
        assert result is False


# ===========================================================================
# _build_data_source
# ===========================================================================

class TestBuildDataSource:
    @patch("app.connectors.sources.minio.connector.MinIOClient")
    async def test_build_data_source(self, mock_client, minio_connector):
        mock_client.build_from_services = AsyncMock(return_value=MagicMock())
        ds = await minio_connector._build_data_source()
        assert ds is not None


# ===========================================================================
# parse_parent_external_id (utility from s3 base)
# ===========================================================================

class TestParseParentExternalId:
    def test_with_path(self):
        bucket, path = parse_parent_external_id("my-bucket/folder/subfolder/")
        assert bucket == "my-bucket"
        assert path == "folder/subfolder/"

    def test_bucket_only(self):
        bucket, path = parse_parent_external_id("my-bucket")
        assert bucket == "my-bucket"
        assert path is None

    def test_bucket_with_single_file(self):
        bucket, path = parse_parent_external_id("my-bucket/file.txt")
        assert bucket == "my-bucket"
        assert path == "file.txt/"


# ===========================================================================
# create_connector factory
# ===========================================================================

class TestCreateConnector:
    @patch("app.connectors.sources.minio.connector.MinIOApp")
    @patch("app.connectors.sources.minio.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_default_endpoint(self, mock_proc, mock_app):
        mock_proc_instance = MagicMock()
        mock_proc_instance.initialize = AsyncMock()
        mock_proc.return_value = mock_proc_instance

        logger = logging.getLogger("test")
        ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)

        connector = await MinIOConnector.create_connector(
            logger=logger, data_store_provider=ds,
            config_service=config_service, connector_id="test-id",
            scope="team", created_by="test-user-id",
        )
        assert connector is not None

    @patch("app.connectors.sources.minio.connector.MinIOApp")
    @patch("app.connectors.sources.minio.connector.S3CompatibleDataSourceEntitiesProcessor")
    async def test_create_connector_with_config(self, mock_proc, mock_app):
        mock_proc_instance = MagicMock()
        mock_proc_instance.initialize = AsyncMock()
        mock_proc.return_value = mock_proc_instance

        logger = logging.getLogger("test")
        ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"endpointUrl": "https://minio.prod.com:9000"},
        })

        connector = await MinIOConnector.create_connector(
            logger=logger, data_store_provider=ds,
            config_service=config_service, connector_id="test-id",
            scope="team", created_by="test-user-id",
        )
        assert connector.endpoint_url == "https://minio.prod.com:9000"


from app.connectors.core.registry.filters import FilterCollection
