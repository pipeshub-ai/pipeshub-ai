"""Unit tests for S3 client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.s3.s3 import (
    S3AccessKeyConfig,
    S3Client,
    S3RESTClientViaAccessKey,
    S3Response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_s3_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def rest_client():
    return S3RESTClientViaAccessKey(
        access_key_id="AKID",
        secret_access_key="SECRET",
        region_name="us-east-1",
        bucket_name="my-bucket",
    )


# ---------------------------------------------------------------------------
# S3Response
# ---------------------------------------------------------------------------


class TestS3Response:
    def test_success(self):
        resp = S3Response(success=True, data={"key": "val"})
        assert resp.success is True

    def test_error(self):
        resp = S3Response(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = S3Response(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}

    def test_to_json(self):
        resp = S3Response(success=True)
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = S3Response(success=True)
        assert resp.data is None
        assert resp.error is None


# ---------------------------------------------------------------------------
# S3RESTClientViaAccessKey
# ---------------------------------------------------------------------------


class TestS3RESTClientViaAccessKey:
    def test_init(self, rest_client):
        assert rest_client.access_key_id == "AKID"
        assert rest_client.secret_access_key == "SECRET"
        assert rest_client.region_name == "us-east-1"
        assert rest_client.bucket_name == "my-bucket"
        assert rest_client.session is None

    @patch("app.sources.client.s3.s3.aioboto3.Session")
    def test_create_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        session = rest_client.create_session()
        assert session is not None
        mock_session_cls.assert_called_once_with(
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
            region_name="us-east-1",
        )

    @patch("app.sources.client.s3.s3.aioboto3.Session")
    def test_get_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        session = rest_client.get_session()
        assert session is not None
        # Second call returns cached
        session2 = rest_client.get_session()
        assert session is session2

    @pytest.mark.asyncio
    @patch("app.sources.client.s3.s3.aioboto3.Session")
    async def test_get_s3_client(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.client.return_value = MagicMock()
        mock_session_cls.return_value = mock_session
        client = S3RESTClientViaAccessKey("AKID", "SECRET", "us-east-1")
        result = await client.get_s3_client()
        mock_session.client.assert_called_once_with("s3")

    def test_get_bucket_name(self, rest_client):
        assert rest_client.get_bucket_name() == "my-bucket"

    def test_get_bucket_name_none(self):
        client = S3RESTClientViaAccessKey("AKID", "SECRET", "us-east-1")
        assert client.get_bucket_name() is None

    def test_set_bucket_name(self, rest_client):
        rest_client.set_bucket_name("new-bucket")
        assert rest_client.get_bucket_name() == "new-bucket"

    def test_get_region_name(self, rest_client):
        assert rest_client.get_region_name() == "us-east-1"

    def test_get_credentials(self, rest_client):
        creds = rest_client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"
        assert creds["aws_secret_access_key"] == "SECRET"
        assert creds["region_name"] == "us-east-1"


# ---------------------------------------------------------------------------
# S3AccessKeyConfig
# ---------------------------------------------------------------------------


class TestS3AccessKeyConfig:
    def test_create_client(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-west-2"
        )
        client = cfg.create_client()
        assert isinstance(client, S3RESTClientViaAccessKey)
        assert client.region_name == "us-west-2"

    def test_create_client_with_bucket(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET",
            region_name="us-west-2", bucket_name="bkt"
        )
        client = cfg.create_client()
        assert client.bucket_name == "bkt"

    def test_to_dict(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-west-2"
        )
        d = cfg.to_dict()
        assert d["access_key_id"] == "AKID"
        assert d["ssl"] is True

    def test_defaults(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-east-1"
        )
        assert cfg.bucket_name is None
        assert cfg.ssl is True


# ---------------------------------------------------------------------------
# S3Client
# ---------------------------------------------------------------------------


class TestS3Client:
    def test_init(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_client() is rest_client

    def test_get_bucket_name(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_bucket_name() == "my-bucket"

    def test_set_bucket_name(self, rest_client):
        client = S3Client(rest_client)
        client.set_bucket_name("new")
        assert client.get_bucket_name() == "new"

    def test_get_credentials(self, rest_client):
        client = S3Client(rest_client)
        creds = client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"

    def test_get_region_name(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_region_name() == "us-east-1"

    @patch("app.sources.client.s3.s3.aioboto3.Session")
    def test_get_session(self, mock_session_cls, rest_client):
        mock_session_cls.return_value = MagicMock()
        client = S3Client(rest_client)
        session = client.get_session()
        assert session is not None

    def test_build_with_config(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-east-1"
        )
        client = S3Client.build_with_config(cfg)
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "ACCESS_KEY",
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                    "region": "us-west-2",
                    "bucket": "bkt",
                }
            }
        )
        client = await S3Client.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_default_region(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                }
            }
        )
        client = await S3Client.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_keys(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "ACCESS_KEY"}}
        )
        with pytest.raises(ValueError, match="Access key ID and secret"):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await S3Client._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await S3Client._get_connector_config(
                logger, mock_config_service, "inst-1"
            )
