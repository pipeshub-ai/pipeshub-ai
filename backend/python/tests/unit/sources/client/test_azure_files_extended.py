"""
Extended tests for app.sources.client.azure.azure_files covering missing lines:
- AzureFilesRESTClient.get_share_service_client (lines 254-262)
- AzureFilesRESTClient.get_async_share_service_client (lines 266-276)
- AzureFilesRESTClient.get_account_url for ConnectionStringConfig (line 299)
- AzureFilesRESTClient.ensure_share_exists (lines 354-363)
- AzureFilesClient delegates: get_share_name, get_credentials_info, etc.
- AzureFilesClient.build_with_connection_string_config error
- AzureFilesClient.build_with_account_key_config error
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.sources.client.azure.azure_files import (
    AzureFilesAccountKeyConfig,
    AzureFilesClient,
    AzureFilesConfigurationError,
    AzureFilesConnectionStringConfig,
    AzureFilesRESTClient,
    AzureFilesResponse,
    AzureFilesShareError,
)


# ============================================================================
# AzureFilesRESTClient
# ============================================================================


class TestAzureFilesRESTClientShareServiceClient:
    def test_get_share_service_client_success(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_client = MagicMock()
        config.create_share_service_client.return_value = mock_client

        rest_client = AzureFilesRESTClient(config)
        result = rest_client.get_share_service_client()
        assert result == mock_client

    def test_get_share_service_client_cached(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_client = MagicMock()
        config.create_share_service_client.return_value = mock_client

        rest_client = AzureFilesRESTClient(config)
        result1 = rest_client.get_share_service_client()
        result2 = rest_client.get_share_service_client()
        assert result1 is result2
        config.create_share_service_client.assert_called_once()

    def test_get_share_service_client_error(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        config.create_share_service_client.side_effect = Exception("error")
        config.get_authentication_method.return_value = "account_key"

        rest_client = AzureFilesRESTClient(config)
        with pytest.raises(AzureFilesConfigurationError):
            rest_client.get_share_service_client()


class TestAzureFilesRESTClientAsyncShareServiceClient:
    @pytest.mark.asyncio
    async def test_get_async_share_service_client_success(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_client = AsyncMock()
        config.create_async_share_service_client = AsyncMock(return_value=mock_client)

        rest_client = AzureFilesRESTClient(config)
        result = await rest_client.get_async_share_service_client()
        assert result == mock_client

    @pytest.mark.asyncio
    async def test_get_async_share_service_client_cached(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_client = AsyncMock()
        config.create_async_share_service_client = AsyncMock(return_value=mock_client)

        rest_client = AzureFilesRESTClient(config)
        result1 = await rest_client.get_async_share_service_client()
        result2 = await rest_client.get_async_share_service_client()
        assert result1 is result2
        config.create_async_share_service_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_async_share_service_client_error(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        config.create_async_share_service_client = AsyncMock(side_effect=Exception("async err"))
        config.get_authentication_method.return_value = "account_key"

        rest_client = AzureFilesRESTClient(config)
        with pytest.raises(AzureFilesConfigurationError):
            await rest_client.get_async_share_service_client()


class TestAzureFilesRESTClientAccountUrl:
    def test_get_account_url_connection_string(self):
        config = MagicMock(spec=AzureFilesConnectionStringConfig)
        config.get_account_name.return_value = "myaccount"
        # Remove get_account_url from spec
        del config.get_account_url

        rest_client = AzureFilesRESTClient(config)
        url = rest_client.get_account_url()
        assert url == "https://myaccount.file.core.windows.net"

    def test_get_account_url_no_account_name(self):
        config = MagicMock(spec=AzureFilesConnectionStringConfig)
        config.get_account_name.return_value = None
        del config.get_account_url

        rest_client = AzureFilesRESTClient(config)
        with pytest.raises(AzureFilesConfigurationError):
            rest_client.get_account_url()


class TestEnsureShareExists:
    @pytest.mark.asyncio
    async def test_share_already_exists(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_share_client = AsyncMock()
        mock_share_client.exists = AsyncMock(return_value=True)

        mock_async_client = MagicMock()
        mock_async_client.get_share_client.return_value = mock_share_client

        rest_client = AzureFilesRESTClient(config)
        # Pre-set the async client to avoid creation
        rest_client._async_share_service_client = mock_async_client

        result = await rest_client.ensure_share_exists("myshare")
        assert result.success is True
        assert result.data["action"] == "exists"

    @pytest.mark.asyncio
    async def test_share_created(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        mock_share_client = AsyncMock()
        mock_share_client.exists = AsyncMock(return_value=False)
        mock_share_client.create_share = AsyncMock()

        mock_async_client = MagicMock()
        mock_async_client.get_share_client.return_value = mock_share_client

        rest_client = AzureFilesRESTClient(config)
        rest_client._async_share_service_client = mock_async_client

        result = await rest_client.ensure_share_exists("myshare")
        assert result.success is True
        assert result.data["action"] == "created"


# ============================================================================
# AzureFilesClient delegation methods
# ============================================================================


class TestAzureFilesClientDelegation:
    def test_get_share_name(self):
        rest_client = MagicMock()
        rest_client.get_share_name.return_value = "share1"
        client = AzureFilesClient(rest_client)
        assert client.get_share_name() == "share1"

    def test_get_credentials_info(self):
        rest_client = MagicMock()
        rest_client.get_credentials_info.return_value = {"method": "key"}
        client = AzureFilesClient(rest_client)
        assert client.get_credentials_info() == {"method": "key"}

    def test_get_account_name(self):
        rest_client = MagicMock()
        rest_client.get_account_name.return_value = "myaccount"
        client = AzureFilesClient(rest_client)
        assert client.get_account_name() == "myaccount"

    def test_get_account_url(self):
        rest_client = MagicMock()
        rest_client.get_account_url.return_value = "https://myaccount.file.core.windows.net"
        client = AzureFilesClient(rest_client)
        assert "myaccount" in client.get_account_url()

    def test_get_account_key(self):
        rest_client = MagicMock()
        rest_client.get_account_key.return_value = "key123"
        client = AzureFilesClient(rest_client)
        assert client.get_account_key() == "key123"

    def test_get_authentication_method(self):
        rest_client = MagicMock()
        rest_client.get_authentication_method.return_value = "account_key"
        client = AzureFilesClient(rest_client)
        assert client.get_authentication_method() == "account_key"

    @pytest.mark.asyncio
    async def test_ensure_share_exists(self):
        rest_client = MagicMock()
        rest_client.ensure_share_exists = AsyncMock(
            return_value=AzureFilesResponse(success=True)
        )
        client = AzureFilesClient(rest_client)
        result = await client.ensure_share_exists("share1")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_async_share_service_client(self):
        rest_client = MagicMock()
        mock_client = AsyncMock()
        rest_client.get_async_share_service_client = AsyncMock(return_value=mock_client)
        client = AzureFilesClient(rest_client)
        result = await client.get_async_share_service_client()
        assert result == mock_client

    @pytest.mark.asyncio
    async def test_close_async_client(self):
        rest_client = MagicMock()
        rest_client.close_async_client = AsyncMock()
        client = AzureFilesClient(rest_client)
        await client.close_async_client()
        rest_client.close_async_client.assert_awaited_once()


# ============================================================================
# build_with_ error paths
# ============================================================================


class TestBuildWithErrors:
    def test_build_with_connection_string_config_error(self):
        """Non-custom exception should be wrapped."""
        config = MagicMock(spec=AzureFilesConnectionStringConfig)
        with patch.object(
            AzureFilesRESTClient, "__init__",
            side_effect=RuntimeError("unexpected"),
        ):
            with pytest.raises(AzureFilesConfigurationError):
                AzureFilesClient.build_with_connection_string_config(config)

    def test_build_with_account_key_config_error(self):
        config = MagicMock(spec=AzureFilesAccountKeyConfig)
        with patch.object(
            AzureFilesRESTClient, "__init__",
            side_effect=RuntimeError("unexpected"),
        ):
            with pytest.raises(AzureFilesConfigurationError):
                AzureFilesClient.build_with_account_key_config(config)
