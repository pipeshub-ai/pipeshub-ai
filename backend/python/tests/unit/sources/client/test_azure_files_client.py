"""Unit tests for Azure Files client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_azure_files_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def valid_connection_string():
    return "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=dGVzdGtleQ==;EndpointSuffix=core.windows.net"


@pytest.fixture
def conn_str_config(valid_connection_string):
    return AzureFilesConnectionStringConfig(
        connectionString=valid_connection_string, shareName="myshare"
    )


@pytest.fixture
def acct_key_config():
    return AzureFilesAccountKeyConfig(
        accountName="teststorage",
        accountKey="dGVzdGtleQ==",
        shareName="myshare",
    )


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class TestAzureFilesConfigurationError:
    def test_message(self):
        err = AzureFilesConfigurationError("bad config")
        assert str(err) == "bad config"

    def test_details(self):
        err = AzureFilesConfigurationError("bad", details={"k": "v"})
        assert err.details == {"k": "v"}


class TestAzureFilesShareError:
    def test_message(self):
        err = AzureFilesShareError("share err")
        assert str(err) == "share err"

    def test_share_name(self):
        err = AzureFilesShareError("err", share_name="myshare")
        assert err.share_name == "myshare"

    def test_defaults(self):
        err = AzureFilesShareError("err")
        assert err.share_name is None
        assert err.details == {}


# ---------------------------------------------------------------------------
# AzureFilesResponse
# ---------------------------------------------------------------------------


class TestAzureFilesResponse:
    def test_success(self):
        resp = AzureFilesResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_to_dict(self):
        resp = AzureFilesResponse(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}

    def test_to_json(self):
        resp = AzureFilesResponse(success=True)
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = AzureFilesResponse(success=False, error="oops")
        assert resp.error == "oops"
        assert resp.data is None


# ---------------------------------------------------------------------------
# AzureFilesConnectionStringConfig
# ---------------------------------------------------------------------------


class TestAzureFilesConnectionStringConfig:
    def test_valid_config(self, valid_connection_string):
        cfg = AzureFilesConnectionStringConfig(connectionString=valid_connection_string)
        assert cfg.connectionString == valid_connection_string

    def test_empty_raises(self):
        with pytest.raises(AzureFilesConfigurationError, match="cannot be empty"):
            AzureFilesConnectionStringConfig(connectionString="")

    def test_whitespace_raises(self):
        with pytest.raises(AzureFilesConfigurationError, match="cannot be empty"):
            AzureFilesConnectionStringConfig(connectionString="   ")

    def test_get_account_name(self, conn_str_config):
        assert conn_str_config.get_account_name() == "teststorage"

    def test_get_account_name_not_found(self):
        cfg = AzureFilesConnectionStringConfig(connectionString="Other=value")
        assert cfg.get_account_name() is None

    def test_get_authentication_method(self, conn_str_config):
        assert conn_str_config.get_authentication_method() == "connection_string"

    def test_to_dict(self, conn_str_config):
        d = conn_str_config.to_dict()
        assert d["authentication_method"] == "connection_string"
        assert d["account_name"] == "teststorage"
        assert d["share_name"] == "myshare"

    @patch("app.sources.client.azure.azure_files.ShareServiceClient")
    def test_create_share_service_client(self, mock_ssc, conn_str_config):
        mock_ssc.from_connection_string.return_value = MagicMock()
        client = conn_str_config.create_share_service_client()
        assert client is not None

    @patch("app.sources.client.azure.azure_files.ShareServiceClient")
    def test_create_share_service_client_failure(self, mock_ssc, conn_str_config):
        mock_ssc.from_connection_string.side_effect = ValueError("bad")
        with pytest.raises(AzureFilesConfigurationError):
            conn_str_config.create_share_service_client()

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_files.AsyncShareServiceClient")
    async def test_create_async_share_service_client(self, mock_assc, conn_str_config):
        mock_assc.from_connection_string.return_value = MagicMock()
        client = await conn_str_config.create_async_share_service_client()
        assert client is not None

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_files.AsyncShareServiceClient")
    async def test_create_async_share_service_client_failure(self, mock_assc, conn_str_config):
        mock_assc.from_connection_string.side_effect = ValueError("bad")
        with pytest.raises(AzureFilesConfigurationError):
            await conn_str_config.create_async_share_service_client()


# ---------------------------------------------------------------------------
# AzureFilesAccountKeyConfig
# ---------------------------------------------------------------------------


class TestAzureFilesAccountKeyConfig:
    def test_valid_config(self, acct_key_config):
        assert acct_key_config.accountName == "teststorage"

    def test_empty_account_name_raises(self):
        with pytest.raises(AzureFilesConfigurationError, match="accountName cannot be empty"):
            AzureFilesAccountKeyConfig(accountName="", accountKey="key")

    def test_empty_account_key_raises(self):
        with pytest.raises(AzureFilesConfigurationError, match="accountKey cannot be empty"):
            AzureFilesAccountKeyConfig(accountName="acct", accountKey="")

    def test_invalid_protocol_raises(self):
        with pytest.raises(AzureFilesConfigurationError, match="endpointProtocol must be"):
            AzureFilesAccountKeyConfig(
                accountName="acct", accountKey="key", endpointProtocol="ftp"
            )

    def test_get_account_name(self, acct_key_config):
        assert acct_key_config.get_account_name() == "teststorage"

    def test_get_account_url(self, acct_key_config):
        url = acct_key_config.get_account_url()
        assert url == "https://teststorage.file.core.windows.net"

    def test_get_authentication_method(self, acct_key_config):
        assert acct_key_config.get_authentication_method() == "account_key"

    def test_get_credentials_info(self, acct_key_config):
        info = acct_key_config.get_credentials_info()
        assert info["authentication_method"] == "account_key"
        assert info["account_name"] == "teststorage"

    def test_to_dict(self, acct_key_config):
        d = acct_key_config.to_dict()
        assert d["account_name"] == "teststorage"
        assert d["share_name"] == "myshare"

    @patch("app.sources.client.azure.azure_files.ShareServiceClient")
    def test_create_share_service_client(self, mock_ssc, acct_key_config):
        mock_ssc.return_value = MagicMock()
        client = acct_key_config.create_share_service_client()
        assert client is not None

    @patch("app.sources.client.azure.azure_files.ShareServiceClient")
    def test_create_share_service_client_failure(self, mock_ssc, acct_key_config):
        mock_ssc.side_effect = Exception("fail")
        with pytest.raises(AzureFilesConfigurationError):
            acct_key_config.create_share_service_client()

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_files.AsyncShareServiceClient")
    async def test_create_async_share_service_client(self, mock_assc, acct_key_config):
        mock_assc.return_value = MagicMock()
        client = await acct_key_config.create_async_share_service_client()
        assert client is not None

    @pytest.mark.asyncio
    @patch("app.sources.client.azure.azure_files.AsyncShareServiceClient")
    async def test_create_async_share_service_client_failure(self, mock_assc, acct_key_config):
        mock_assc.side_effect = Exception("fail")
        with pytest.raises(AzureFilesConfigurationError):
            await acct_key_config.create_async_share_service_client()


# ---------------------------------------------------------------------------
# AzureFilesRESTClient
# ---------------------------------------------------------------------------


class TestAzureFilesRESTClient:
    def test_init_with_conn_str(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert client.config is conn_str_config

    def test_init_with_acct_key(self, acct_key_config):
        client = AzureFilesRESTClient(acct_key_config)
        assert client.config is acct_key_config

    def test_get_share_name(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert client.get_share_name() == "myshare"

    def test_get_account_name_conn_str(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert client.get_account_name() == "teststorage"

    def test_get_account_name_acct_key(self, acct_key_config):
        client = AzureFilesRESTClient(acct_key_config)
        assert client.get_account_name() == "teststorage"

    def test_get_account_name_failure(self):
        config = AzureFilesConnectionStringConfig(connectionString="SomeKey=value")
        client = AzureFilesRESTClient(config)
        with pytest.raises(AzureFilesConfigurationError):
            client.get_account_name()

    def test_get_account_url_acct_key(self, acct_key_config):
        client = AzureFilesRESTClient(acct_key_config)
        assert "teststorage" in client.get_account_url()

    def test_get_account_url_conn_str(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert "teststorage" in client.get_account_url()

    def test_get_account_key_acct_key_config(self, acct_key_config):
        client = AzureFilesRESTClient(acct_key_config)
        assert client.get_account_key() == "dGVzdGtleQ=="

    def test_get_account_key_conn_str_config(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert client.get_account_key() is None

    def test_get_authentication_method(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        assert client.get_authentication_method() == "connection_string"

    def test_get_credentials_info_acct_key(self, acct_key_config):
        client = AzureFilesRESTClient(acct_key_config)
        info = client.get_credentials_info()
        assert info["authentication_method"] == "account_key"

    def test_get_credentials_info_conn_str(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        info = client.get_credentials_info()
        assert info["authentication_method"] == "connection_string"

    @pytest.mark.asyncio
    async def test_close_async_client(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        mock_async_client = AsyncMock()
        client._async_share_service_client = mock_async_client
        await client.close_async_client()
        mock_async_client.close.assert_called_once()
        assert client._async_share_service_client is None

    @pytest.mark.asyncio
    async def test_close_async_client_when_none(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        await client.close_async_client()  # Should not raise

    @pytest.mark.asyncio
    async def test_ensure_share_exists_creates(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        mock_share = MagicMock()
        mock_share.exists = AsyncMock(return_value=False)
        mock_share.create_share = AsyncMock(return_value=None)
        mock_async_client = MagicMock()
        mock_async_client.get_share_client.return_value = mock_share

        with patch.object(client, "get_async_share_service_client", new_callable=AsyncMock, return_value=mock_async_client):
            result = await client.ensure_share_exists("test-share")
            assert result.success is True
            assert result.data["action"] == "created"

    @pytest.mark.asyncio
    async def test_ensure_share_exists_already_exists(self, conn_str_config):
        client = AzureFilesRESTClient(conn_str_config)
        mock_share = MagicMock()
        mock_share.exists = AsyncMock(return_value=True)
        mock_async_client = MagicMock()
        mock_async_client.get_share_client.return_value = mock_share

        with patch.object(client, "get_async_share_service_client", new_callable=AsyncMock, return_value=mock_async_client):
            result = await client.ensure_share_exists("test-share")
            assert result.success is True
            assert result.data["action"] == "exists"


# ---------------------------------------------------------------------------
# AzureFilesClient
# ---------------------------------------------------------------------------


class TestAzureFilesClient:
    def test_init(self, conn_str_config):
        rest_client = AzureFilesRESTClient(conn_str_config)
        client = AzureFilesClient(rest_client)
        assert client.get_client() is rest_client

    def test_get_share_name(self, conn_str_config):
        rest_client = AzureFilesRESTClient(conn_str_config)
        client = AzureFilesClient(rest_client)
        assert client.get_share_name() == "myshare"

    def test_get_account_name(self, acct_key_config):
        rest_client = AzureFilesRESTClient(acct_key_config)
        client = AzureFilesClient(rest_client)
        assert client.get_account_name() == "teststorage"

    def test_get_account_url(self, acct_key_config):
        rest_client = AzureFilesRESTClient(acct_key_config)
        client = AzureFilesClient(rest_client)
        assert "teststorage" in client.get_account_url()

    def test_get_account_key(self, acct_key_config):
        rest_client = AzureFilesRESTClient(acct_key_config)
        client = AzureFilesClient(rest_client)
        assert client.get_account_key() == "dGVzdGtleQ=="

    def test_get_authentication_method(self, conn_str_config):
        rest_client = AzureFilesRESTClient(conn_str_config)
        client = AzureFilesClient(rest_client)
        assert client.get_authentication_method() == "connection_string"

    def test_build_with_connection_string_config(self, conn_str_config):
        client = AzureFilesClient.build_with_connection_string_config(conn_str_config)
        assert isinstance(client, AzureFilesClient)

    def test_build_with_account_key_config(self, acct_key_config):
        client = AzureFilesClient.build_with_account_key_config(acct_key_config)
        assert isinstance(client, AzureFilesClient)

    def test_build_with_connection_string(self, valid_connection_string):
        client = AzureFilesClient.build_with_connection_string(valid_connection_string, shareName="myshare")
        assert isinstance(client, AzureFilesClient)

    def test_build_with_account_key(self):
        client = AzureFilesClient.build_with_account_key(
            accountName="teststorage", accountKey="key", shareName="myshare"
        )
        assert isinstance(client, AzureFilesClient)

    @pytest.mark.asyncio
    async def test_build_from_services_connection_string(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "CONNECTION_STRING",
                    "connectionString": "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=key;EndpointSuffix=core.windows.net",
                    "shareName": "myshare",
                }
            }
        )
        client = await AzureFilesClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, AzureFilesClient)

    @pytest.mark.asyncio
    async def test_build_from_services_account_key(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "ACCOUNT_KEY",
                    "accountName": "teststorage",
                    "accountKey": "key",
                    "shareName": "myshare",
                }
            }
        )
        client = await AzureFilesClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, AzureFilesClient)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_connection_string(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "CONNECTION_STRING"}}
        )
        with pytest.raises(AzureFilesConfigurationError):
            await AzureFilesClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_account_key(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "ACCOUNT_KEY"}}
        )
        with pytest.raises(AzureFilesConfigurationError):
            await AzureFilesClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_unsupported_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(AzureFilesConfigurationError):
            await AzureFilesClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(AzureFilesConfigurationError):
            await AzureFilesClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await AzureFilesClient._get_connector_config(
            mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(AzureFilesConfigurationError):
            await AzureFilesClient._get_connector_config(
                mock_config_service, "inst-1"
            )
