"""Unit tests for Salesforce client module."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceConfig,
    SalesforceResponse,
    SalesforceRESTClient,
)

INSTANCE_URL = "https://my-domain.my.salesforce.com"


@pytest.fixture
def logger():
    return logging.getLogger("test_salesforce_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# SalesforceResponse
# ---------------------------------------------------------------------------


class TestSalesforceResponse:
    def test_success_with_data(self):
        resp = SalesforceResponse(success=True, data={"Id": "001"})
        assert resp.success is True
        assert resp.data == {"Id": "001"}
        assert resp.error is None

    def test_to_dict(self):
        resp = SalesforceResponse(success=False, error="boom", data=None)
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "boom"

    def test_to_json(self):
        resp = SalesforceResponse(success=True, message="ok")
        j = resp.to_json()
        assert '"success":true' in j or '"success": true' in j


# ---------------------------------------------------------------------------
# SalesforceRESTClient
# ---------------------------------------------------------------------------


class TestSalesforceRESTClient:
    def test_init_sets_urls_and_headers(self):
        client = SalesforceRESTClient(
            instance_url=INSTANCE_URL,
            access_token="tok",
            api_version="58.0",
            refresh_token="rt",
        )
        assert client.instance_url == INSTANCE_URL
        assert client.base_url == INSTANCE_URL
        assert client.api_version == "58.0"
        assert client.refresh_token == "rt"
        assert client.headers["Authorization"] == "Bearer tok"
        assert client.headers["Content-Type"] == "application/json"
        assert client.headers["Accept"] == "application/json"

    def test_trailing_slash_stripped(self):
        client = SalesforceRESTClient(f"{INSTANCE_URL}/", "tok")
        assert client.instance_url == INSTANCE_URL

    def test_default_api_version(self):
        client = SalesforceRESTClient(INSTANCE_URL, "tok")
        assert client.api_version == "59.0"

    def test_get_base_url_and_instance_url(self):
        client = SalesforceRESTClient(INSTANCE_URL, "tok")
        assert client.get_base_url() == INSTANCE_URL
        assert client.get_instance_url() == INSTANCE_URL


# ---------------------------------------------------------------------------
# SalesforceConfig
# ---------------------------------------------------------------------------


class TestSalesforceConfig:
    def test_create_client(self):
        cfg = SalesforceConfig(
            instance_url=INSTANCE_URL,
            access_token="at",
            api_version="57.0",
            refresh_token="r1",
        )
        rest = cfg.create_client()
        assert isinstance(rest, SalesforceRESTClient)
        assert rest.instance_url == INSTANCE_URL
        assert rest.headers["Authorization"] == "Bearer at"
        assert rest.api_version == "57.0"
        assert rest.refresh_token == "r1"

    def test_to_dict(self):
        cfg = SalesforceConfig(instance_url=INSTANCE_URL, access_token="x")
        d = cfg.to_dict()
        assert d["instance_url"] == INSTANCE_URL
        assert d["access_token"] == "x"
        assert d["api_version"] == "59.0"

    def test_validate_instance_url_prefixes_https(self):
        cfg = SalesforceConfig(instance_url="na1.salesforce.com", access_token="t")
        assert cfg.instance_url == "https://na1.salesforce.com"

    def test_validate_instance_url_keeps_http_scheme(self):
        cfg = SalesforceConfig(instance_url="http://local.test", access_token="t")
        assert cfg.instance_url == "http://local.test"

    def test_empty_instance_url_raises(self):
        with pytest.raises(ValidationError):
            SalesforceConfig(instance_url="", access_token="t")


# ---------------------------------------------------------------------------
# SalesforceClient
# ---------------------------------------------------------------------------


class TestSalesforceClient:
    def test_init_get_client_get_base_url(self):
        rest = SalesforceRESTClient(INSTANCE_URL, "tok")
        sc = SalesforceClient(rest)
        assert sc.get_client() is rest
        assert sc.get_base_url() == INSTANCE_URL

    def test_build_with_config(self):
        cfg = SalesforceConfig(instance_url=INSTANCE_URL, access_token="at")
        sc = SalesforceClient.build_with_config(cfg)
        assert isinstance(sc, SalesforceClient)
        assert sc.get_client().headers["Authorization"] == "Bearer at"


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_oauth_nested_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "OAUTH",
                "auth": {
                    "credentials": {
                        "accessToken": "at",
                        "instanceUrl": INSTANCE_URL,
                    }
                },
                "apiVersion": "58.0",
            }
        )
        client = await SalesforceClient.build_from_services(logger, mock_config_service)
        assert isinstance(client, SalesforceClient)
        assert client.get_client().api_version == "58.0"

    @pytest.mark.asyncio
    async def test_oauth_fallback_top_level_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "OAUTH",
                "auth": {
                    "accessToken": "at2",
                    "instanceUrl": INSTANCE_URL,
                },
            }
        )
        client = await SalesforceClient.build_from_services(logger, mock_config_service)
        assert client.get_client().headers["Authorization"] == "Bearer at2"

    @pytest.mark.asyncio
    async def test_defaults_to_oauth_when_auth_type_missing(
        self, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "credentials": {
                        "accessToken": "at",
                        "instanceUrl": INSTANCE_URL,
                    }
                },
            }
        )
        client = await SalesforceClient.build_from_services(logger, mock_config_service)
        assert isinstance(client, SalesforceClient)

    @pytest.mark.asyncio
    async def test_access_token_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "ACCESS_TOKEN",
                "auth": {
                    "accessToken": "manual",
                    "instanceUrl": INSTANCE_URL,
                },
            }
        )
        client = await SalesforceClient.build_from_services(logger, mock_config_service)
        assert client.get_client().headers["Authorization"] == "Bearer manual"

    @pytest.mark.asyncio
    async def test_unsupported_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "BASIC",
                "auth": {
                    "accessToken": "x",
                    "instanceUrl": INSTANCE_URL,
                },
            }
        )
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await SalesforceClient.build_from_services(logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_missing_access_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "OAUTH",
                "auth": {"instanceUrl": INSTANCE_URL},
            }
        )
        with pytest.raises(ValueError, match="Access token is required"):
            await SalesforceClient.build_from_services(logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_missing_instance_url(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "authType": "OAUTH",
                "auth": {"credentials": {"accessToken": "only"}},
            }
        )
        with pytest.raises(ValueError, match="Instance URL is required"):
            await SalesforceClient.build_from_services(logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_empty_config_data(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Salesforce connector"):
            await SalesforceClient.build_from_services(logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_empty_dict_config_data(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Failed to get Salesforce connector"):
            await SalesforceClient.build_from_services(logger, mock_config_service)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await SalesforceClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {"auth": {}}
        mock_config_service.get_config.assert_awaited_once_with(
            "/services/connectors/salesforce/config"
        )

    @pytest.mark.asyncio
    async def test_none_becomes_empty_dict(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        result = await SalesforceClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_config_exception_reraises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(RuntimeError, match="etcd down"):
            await SalesforceClient._get_connector_config(logger, mock_config_service)


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_toolset_config_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await SalesforceClient.build_from_toolset({}, logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_missing_config_service_raises(self, logger):
        with pytest.raises(ValueError, match="ConfigurationService is required"):
            await SalesforceClient.build_from_toolset(
                {"credentials": {"access_token": "x"}},
                logger,
                None,
            )

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Access token required"):
            await SalesforceClient.build_from_toolset(
                {"credentials": {}},
                logger,
                mock_config_service,
            )

    @pytest.mark.asyncio
    async def test_missing_instance_url_raises(self, logger, mock_config_service):
        with patch(
            "app.sources.client.salesforce.salesforce.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"instance_url": ""},
        ):
            with pytest.raises(ValueError, match="Instance URL required"):
                await SalesforceClient.build_from_toolset(
                    {
                        "credentials": {"access_token": "at"},
                        "api_version": "59.0",
                    },
                    logger,
                    mock_config_service,
                )

    @pytest.mark.asyncio
    async def test_instance_url_stripped(self, logger, mock_config_service):
        with patch(
            "app.sources.client.salesforce.salesforce.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"instance_url": f" {INSTANCE_URL} "},
        ):
            client = await SalesforceClient.build_from_toolset(
                {
                    "credentials": {
                        "access_token": "at",
                        "refresh_token": "rt",
                    },
                    "api_version": "60.0",
                },
                logger,
                mock_config_service,
            )
        assert isinstance(client, SalesforceClient)
        assert client.get_client().instance_url == INSTANCE_URL
        assert client.get_client().refresh_token == "rt"
        assert client.get_client().api_version == "60.0"

    @pytest.mark.asyncio
    async def test_success_default_api_version(self, logger, mock_config_service):
        with patch(
            "app.sources.client.salesforce.salesforce.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value={"instance_url": INSTANCE_URL},
        ):
            client = await SalesforceClient.build_from_toolset(
                {"credentials": {"access_token": "at"}},
                logger,
                mock_config_service,
            )
        assert client.get_client().api_version == "59.0"
