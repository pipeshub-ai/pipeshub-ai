"""Unit tests for MinIO client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.minio.minio import (
    MinIOAccessKeyConfig,
    MinIOClient,
    MinIORESTClientViaAccessKey,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_minio_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def rest_client():
    return MinIORESTClientViaAccessKey(
        access_key_id="AKID",
        secret_access_key="SECRET",
        endpoint_url="http://localhost:9000",
        bucket_name="my-bucket",
        use_ssl=False,
        verify_ssl=False,
        region_name="us-east-1",
    )


# ---------------------------------------------------------------------------
# MinIORESTClientViaAccessKey
# ---------------------------------------------------------------------------


class TestMinIORESTClientViaAccessKey:
    def test_init(self, rest_client):
        assert rest_client.access_key_id == "AKID"
        assert rest_client.secret_access_key == "SECRET"
        assert rest_client.endpoint_url == "http://localhost:9000"
        assert rest_client.bucket_name == "my-bucket"
        assert rest_client.use_ssl is False
        assert rest_client.verify_ssl is False
        assert rest_client.region_name == "us-east-1"
        assert rest_client.session is None

    def test_defaults(self):
        client = MinIORESTClientViaAccessKey("A", "S", "http://localhost:9000")
        assert client.bucket_name is None
        assert client.use_ssl is True
        assert client.verify_ssl is True
        assert client.region_name == "us-east-1"

    @patch("app.sources.client.minio.minio.aioboto3.Session")
    def test_create_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        session = rest_client.create_session()
        assert session is not None
        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
            region_name="us-east-1",
        )

    @patch("app.sources.client.minio.minio.aioboto3.Session")
    def test_get_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        session = rest_client.get_session()
        assert session is not None
        # Second call returns cached
        session2 = rest_client.get_session()
        assert session is session2

    @pytest.mark.asyncio
    @patch("app.sources.client.minio.minio.aioboto3.Session")
    async def test_get_s3_client(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_session_cls.return_value = mock_session
        client = MinIORESTClientViaAccessKey("A", "S", "http://localhost:9000", verify_ssl=False)
        result = await client.get_s3_client()
        mock_session.client.assert_called_once_with(
            "s3", endpoint_url="http://localhost:9000", verify=False
        )

    def test_get_bucket_name(self, rest_client):
        assert rest_client.get_bucket_name() == "my-bucket"

    def test_get_bucket_name_none(self):
        client = MinIORESTClientViaAccessKey("A", "S", "http://localhost:9000")
        assert client.get_bucket_name() is None

    def test_set_bucket_name(self, rest_client):
        rest_client.set_bucket_name("new-bucket")
        assert rest_client.get_bucket_name() == "new-bucket"

    def test_get_region_name(self, rest_client):
        assert rest_client.get_region_name() == "us-east-1"

    def test_get_endpoint_url(self, rest_client):
        assert rest_client.get_endpoint_url() == "http://localhost:9000"

    def test_get_credentials(self, rest_client):
        creds = rest_client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"
        assert creds["aws_secret_access_key"] == "SECRET"
        assert creds["endpoint_url"] == "http://localhost:9000"
        assert creds["use_ssl"] is False
        assert creds["verify_ssl"] is False


# ---------------------------------------------------------------------------
# MinIOAccessKeyConfig
# ---------------------------------------------------------------------------


class TestMinIOAccessKeyConfig:
    def test_create_client(self):
        cfg = MinIOAccessKeyConfig(
            access_key_id="AKID",
            secret_access_key="SECRET",
            endpoint_url="http://localhost:9000",
        )
        client = cfg.create_client()
        assert isinstance(client, MinIORESTClientViaAccessKey)

    def test_create_client_with_options(self):
        cfg = MinIOAccessKeyConfig(
            access_key_id="AKID",
            secret_access_key="SECRET",
            endpoint_url="http://localhost:9000",
            bucket_name="bkt",
            use_ssl=False,
            verify_ssl=False,
            region_name="eu-west-1",
        )
        client = cfg.create_client()
        assert client.bucket_name == "bkt"
        assert client.use_ssl is False
        assert client.region_name == "eu-west-1"

    def test_to_dict(self):
        cfg = MinIOAccessKeyConfig(
            access_key_id="AKID",
            secret_access_key="SECRET",
            endpoint_url="http://localhost:9000",
        )
        d = cfg.to_dict()
        assert d["access_key_id"] == "AKID"
        assert d["endpoint_url"] == "http://localhost:9000"

    def test_defaults(self):
        cfg = MinIOAccessKeyConfig(
            access_key_id="A", secret_access_key="S", endpoint_url="http://localhost:9000"
        )
        assert cfg.bucket_name is None
        assert cfg.use_ssl is True
        assert cfg.verify_ssl is True
        assert cfg.region_name == "us-east-1"


# ---------------------------------------------------------------------------
# MinIOClient
# ---------------------------------------------------------------------------


class TestMinIOClient:
    def test_init(self, rest_client):
        client = MinIOClient(rest_client)
        assert client.get_client() is rest_client

    def test_get_bucket_name(self, rest_client):
        client = MinIOClient(rest_client)
        assert client.get_bucket_name() == "my-bucket"

    def test_set_bucket_name(self, rest_client):
        client = MinIOClient(rest_client)
        client.set_bucket_name("new")
        assert client.get_bucket_name() == "new"

    def test_get_credentials(self, rest_client):
        client = MinIOClient(rest_client)
        creds = client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"

    def test_get_region_name(self, rest_client):
        client = MinIOClient(rest_client)
        assert client.get_region_name() == "us-east-1"

    def test_get_endpoint_url(self, rest_client):
        client = MinIOClient(rest_client)
        assert client.get_endpoint_url() == "http://localhost:9000"

    @patch("app.sources.client.minio.minio.aioboto3.Session")
    def test_get_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        client = MinIOClient(rest_client)
        session = client.get_session()
        assert session is not None

    def test_build_with_config(self):
        cfg = MinIOAccessKeyConfig(
            access_key_id="AKID",
            secret_access_key="SECRET",
            endpoint_url="http://localhost:9000",
        )
        client = MinIOClient.build_with_config(cfg)
        assert isinstance(client, MinIOClient)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                    "endpointUrl": "http://localhost:9000",
                    "bucket": "bkt",
                    "useSsl": False,
                    "verifySsl": False,
                    "region": "eu-west-1",
                }
            }
        )
        client = await MinIOClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, MinIOClient)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_keys(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"endpointUrl": "http://localhost:9000"}}
        )
        with pytest.raises(ValueError, match="Access key ID and secret"):
            await MinIOClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_endpoint(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"accessKey": "A", "secretKey": "S"}
            }
        )
        with pytest.raises(ValueError, match="Endpoint URL is required"):
            await MinIOClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await MinIOClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_defaults(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                    "endpointUrl": "http://localhost:9000",
                }
            }
        )
        client = await MinIOClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        rest = client.get_client()
        assert rest.use_ssl is True
        assert rest.verify_ssl is True
        assert rest.region_name == "us-east-1"

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await MinIOClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await MinIOClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError):
            await MinIOClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )
