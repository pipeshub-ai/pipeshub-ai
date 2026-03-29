"""Unit tests for Azure Blob Storage client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.azure.azure_blob import (
    AzureBlobClient,
    AzureBlobConfigurationError,
    AzureBlobConnectionStringConfig,
    AzureBlobContainerError,
    AzureBlobRESTClient,
    AzureBlobResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_azure_blob_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def valid_connection_string():
    return "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=dGVzdGtleQ==;EndpointSuffix=core.windows.net"


@pytest.fixture
def config(valid_connection_string):
    return AzureBlobConnectionStringConfig(
        azureBlobConnectionString=valid_connection_string
    )


@pytest.fixture
def rest_client(config):
    return AzureBlobRESTClient(config)


@pytest.fixture
def blob_client(rest_client):
    return AzureBlobClient(rest_client)


# ---------------------------------------------------------------------------
# AzureBlobConfigurationError
# ---------------------------------------------------------------------------


class TestAzureBlobConfigurationError:
    def test_message(self):
        err = AzureBlobConfigurationError("bad config")
        assert str(err) == "bad config"

    def test_details_default(self):
        err = AzureBlobConfigurationError("bad config")
        assert err.details == {}

    def test_details_custom(self):
        err = AzureBlobConfigurationError("bad", details={"key": "val"})
        assert err.details == {"key": "val"}


# ---------------------------------------------------------------------------
# AzureBlobContainerError
# ---------------------------------------------------------------------------


class TestAzureBlobContainerError:
    def test_message(self):
        err = AzureBlobContainerError("container err")
        assert str(err) == "container err"

    def test_container_name(self):
        err = AzureBlobContainerError("err", container_name="my-container")
        assert err.container_name == "my-container"

    def test_details_default(self):
        err = AzureBlobContainerError("err")
        assert err.details == {}
        assert err.container_name is None


# ---------------------------------------------------------------------------
# AzureBlobResponse
# ---------------------------------------------------------------------------


class TestAzureBlobResponse:
    def test_success(self):
        resp = AzureBlobResponse(success=True, data={"key": "val"})
        assert resp.success is True
        assert resp.data == {"key": "val"}

    def test_error(self):
        resp = AzureBlobResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = AzureBlobResponse(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}
        assert d["message"] == "ok"

    def test_to_json(self):
        resp = AzureBlobResponse(success=True, data={"k": "v"})
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = AzureBlobResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# AzureBlobConnectionStringConfig
# ---------------------------------------------------------------------------


class TestAzureBlobConnectionStringConfig:
    def test_valid_config(self, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        assert config.azureBlobConnectionString == valid_connection_string

    def test_empty_string_raises(self):
        with pytest.raises(AzureBlobConfigurationError, match="cannot be empty"):
            AzureBlobConnectionStringConfig(azureBlobConnectionString="")

    def test_whitespace_only_raises(self):
        with pytest.raises(AzureBlobConfigurationError, match="cannot be empty"):
            AzureBlobConnectionStringConfig(azureBlobConnectionString="   ")

    def test_get_account_name(self, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        assert config.get_account_name() == "teststorage"

    def test_get_account_name_not_found(self):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString="SomeOtherKey=value;Another=thing"
        )
        assert config.get_account_name() is None

    def test_get_authentication_method(self, config):
        assert config.get_authentication_method() == "connection_string"

    def test_to_dict(self, config):
        d = config.to_dict()
        assert d["authentication_method"] == "connection_string"
        assert d["account_name"] == "teststorage"

    @patch("app.sources.client.azure.azure_blob.BlobServiceClient")
    def test_create_blob_service_client(self, mock_bsc, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        mock_bsc.from_connection_string.return_value = MagicMock()
        client = config.create_blob_service_client()
        mock_bsc.from_connection_string.assert_called_once_with(
            conn_str=valid_connection_string
        )
        assert client is not None

    @patch("app.sources.client.azure.azure_blob.BlobServiceClient")
    def test_create_blob_service_client_failure(self, mock_bsc, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        mock_bsc.from_connection_string.side_effect = ValueError("bad conn")
        with pytest.raises(AzureBlobConfigurationError, match="Failed to create BlobServiceClient"):
            config.create_blob_service_client()

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_blob.AsyncBlobServiceClient")
    async def test_create_async_blob_service_client(self, mock_absc, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        mock_absc.from_connection_string.return_value = MagicMock()
        client = await config.create_async_blob_service_client()
        assert client is not None

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_blob.AsyncBlobServiceClient")
    async def test_create_async_blob_service_client_failure(self, mock_absc, valid_connection_string):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString=valid_connection_string
        )
        mock_absc.from_connection_string.side_effect = ValueError("bad conn")
        with pytest.raises(AzureBlobConfigurationError):
            await config.create_async_blob_service_client()


# ---------------------------------------------------------------------------
# AzureBlobRESTClient
# ---------------------------------------------------------------------------


class TestAzureBlobRESTClient:
    def test_init(self, config):
        client = AzureBlobRESTClient(config)
        assert client.config is config
        assert client._blob_service_client is None
        assert client._async_blob_service_client is None

    @patch("app.sources.client.azure.azure_blob.BlobServiceClient")
    def test_get_blob_service_client(self, mock_bsc, config):
        mock_bsc.from_connection_string.return_value = MagicMock()
        client = AzureBlobRESTClient(config)
        result = client.get_blob_service_client()
        assert result is not None
        # Second call returns cached
        result2 = client.get_blob_service_client()
        assert result is result2

    def test_get_blob_service_client_failure(self, config):
        with patch.object(config, "create_blob_service_client", side_effect=Exception("fail")):
            client = AzureBlobRESTClient(config)
            with pytest.raises(AzureBlobConfigurationError, match="Failed to create BlobServiceClient"):
                client.get_blob_service_client()

    @pytest.mark.asyncio
    async def test_get_async_blob_service_client(self, config):
        with patch.object(config, "create_async_blob_service_client", new_callable=AsyncMock, return_value=MagicMock()):
            client = AzureBlobRESTClient(config)
            result = await client.get_async_blob_service_client()
            assert result is not None
            # Second call returns cached
            result2 = await client.get_async_blob_service_client()
            assert result is result2

    @pytest.mark.asyncio
    async def test_get_async_blob_service_client_failure(self, config):
        with patch.object(config, "create_async_blob_service_client", new_callable=AsyncMock, side_effect=Exception("fail")):
            client = AzureBlobRESTClient(config)
            with pytest.raises(AzureBlobConfigurationError, match="Failed to create AsyncBlobServiceClient"):
                await client.get_async_blob_service_client()

    def test_get_account_name(self, rest_client):
        assert rest_client.get_account_name() == "teststorage"

    def test_get_account_name_failure(self):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString="SomeKey=value"
        )
        client = AzureBlobRESTClient(config)
        with pytest.raises(AzureBlobConfigurationError, match="Could not determine account name"):
            client.get_account_name()

    def test_get_account_url(self, rest_client):
        assert rest_client.get_account_url() == "https://teststorage.blob.core.windows.net"

    def test_get_account_url_failure(self):
        config = AzureBlobConnectionStringConfig(
            azureBlobConnectionString="SomeKey=value"
        )
        client = AzureBlobRESTClient(config)
        with pytest.raises(AzureBlobConfigurationError, match="Could not determine account URL"):
            client.get_account_url()

    def test_get_authentication_method(self, rest_client):
        assert rest_client.get_authentication_method() == "connection_string"

    def test_get_credentials_info(self, rest_client):
        info = rest_client.get_credentials_info()
        assert info["authentication_method"] == "connection_string"

    @pytest.mark.asyncio
    async def test_close_async_client(self, rest_client):
        mock_async_client = AsyncMock()
        rest_client._async_blob_service_client = mock_async_client
        await rest_client.close_async_client()
        mock_async_client.close.assert_called_once()
        assert rest_client._async_blob_service_client is None

    @pytest.mark.asyncio
    async def test_close_async_client_when_none(self, rest_client):
        await rest_client.close_async_client()  # Should not raise

    @pytest.mark.asyncio
    async def test_ensure_container_exists_empty_name_raises(self, rest_client):
        with pytest.raises(AzureBlobContainerError, match="containerName is required"):
            await rest_client.ensure_container_exists("")

    @pytest.mark.asyncio
    async def test_ensure_container_exists_creates(self, rest_client):
        mock_container = MagicMock()
        mock_container.exists = AsyncMock(return_value=False)
        mock_container.create_container = AsyncMock(return_value=None)

        mock_async_client = MagicMock()
        mock_async_client.get_container_client.return_value = mock_container

        rest_client._async_blob_service_client = mock_async_client
        with patch.object(rest_client, "get_async_blob_service_client", new_callable=AsyncMock, return_value=mock_async_client):
            result = await rest_client.ensure_container_exists("test-container")
            assert result.success is True
            assert result.data["action"] == "created"
            mock_container.create_container.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_container_exists_already_exists(self, rest_client):
        mock_container = MagicMock()
        mock_container.exists = AsyncMock(return_value=True)

        mock_async_client = MagicMock()
        mock_async_client.get_container_client.return_value = mock_container

        with patch.object(rest_client, "get_async_blob_service_client", new_callable=AsyncMock, return_value=mock_async_client):
            result = await rest_client.ensure_container_exists("test-container")
            assert result.success is True
            assert result.data["action"] == "exists"


# ---------------------------------------------------------------------------
# AzureBlobClient
# ---------------------------------------------------------------------------


class TestAzureBlobClient:
    def test_init(self, blob_client, rest_client):
        assert blob_client.get_client() is rest_client

    def test_get_credentials_info(self, blob_client):
        info = blob_client.get_credentials_info()
        assert "authentication_method" in info

    def test_get_account_name(self, blob_client):
        assert blob_client.get_account_name() == "teststorage"

    def test_get_authentication_method(self, blob_client):
        assert blob_client.get_authentication_method() == "connection_string"

    @pytest.mark.asyncio
    async def test_ensure_container_exists(self, blob_client):
        with patch.object(
            blob_client.client,
            "ensure_container_exists",
            new_callable=AsyncMock,
            return_value=AzureBlobResponse(success=True, data={"action": "exists"}),
        ):
            result = await blob_client.ensure_container_exists("test")
            assert result.success is True

    @pytest.mark.asyncio
    async def test_get_async_blob_service_client(self, blob_client):
        mock_client = AsyncMock()
        with patch.object(
            blob_client.client,
            "get_async_blob_service_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            result = await blob_client.get_async_blob_service_client()
            assert result is mock_client

    @pytest.mark.asyncio
    async def test_close_async_client(self, blob_client):
        with patch.object(blob_client.client, "close_async_client", new_callable=AsyncMock):
            await blob_client.close_async_client()

    @patch("app.sources.client.azure.azure_blob.BlobServiceClient")
    def test_build_with_connection_string_config(self, mock_bsc, config):
        client = AzureBlobClient.build_with_connection_string_config(config)
        assert isinstance(client, AzureBlobClient)

    @patch("app.sources.client.azure.azure_blob.BlobServiceClient")
    def test_build_with_connection_string(self, mock_bsc, valid_connection_string):
        client = AzureBlobClient.build_with_connection_string(valid_connection_string)
        assert isinstance(client, AzureBlobClient)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net"
                }
            }
        )
        with patch("app.sources.client.azure.azure_blob.BlobServiceClient"):
            client = await AzureBlobClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )
            assert isinstance(client, AzureBlobClient)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_connection_string(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}}
        )
        with pytest.raises(AzureBlobConfigurationError):
            await AzureBlobClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(AzureBlobConfigurationError):
            await AzureBlobClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await AzureBlobClient._get_connector_config(
            mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(AzureBlobConfigurationError):
            await AzureBlobClient._get_connector_config(
                mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(AzureBlobConfigurationError):
            await AzureBlobClient._get_connector_config(
                mock_config_service, "inst-1"
            )
