"""Unit tests for Zammad client module."""

import base64
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.zammad.zammad import (
    ZammadClient,
    ZammadOAuth2Config,
    ZammadRESTClientViaOAuth2,
    ZammadRESTClientViaToken,
    ZammadRESTClientViaUsernamePassword,
    ZammadResponse,
    ZammadTokenConfig,
    ZammadUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_zammad_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


BASE_URL = "https://zammad.example.com"


# ---------------------------------------------------------------------------
# ZammadResponse
# ---------------------------------------------------------------------------


class TestZammadResponse:
    def test_success(self):
        resp = ZammadResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_error(self):
        resp = ZammadResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = ZammadResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_dict_binary(self):
        resp = ZammadResponse(success=True, data=b"binary")
        d = resp.to_dict()
        assert d["data"] == base64.b64encode(b"binary").decode("utf-8")

    def test_to_json(self):
        resp = ZammadResponse(success=True, data={"k": "v"})
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_to_json_binary(self):
        resp = ZammadResponse(success=True, data=b"binary")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["data"] == base64.b64encode(b"binary").decode("utf-8")

    def test_to_json_list(self):
        resp = ZammadResponse(success=True, data=[{"id": 1}])
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["data"] == [{"id": 1}]

    def test_defaults(self):
        resp = ZammadResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestZammadRESTClientViaUsernamePassword:
    def test_init(self):
        client = ZammadRESTClientViaUsernamePassword(BASE_URL, "user", "pass")
        assert client.base_url == BASE_URL
        assert client.username == "user"
        expected_cred = base64.b64encode(b"user:pass").decode()
        assert client.headers["Authorization"] == f"Basic {expected_cred}"

    def test_trailing_slash_stripped(self):
        client = ZammadRESTClientViaUsernamePassword(f"{BASE_URL}/", "u", "p")
        assert client.base_url == BASE_URL

    def test_get_base_url(self):
        client = ZammadRESTClientViaUsernamePassword(BASE_URL, "u", "p")
        assert client.get_base_url() == BASE_URL

    def test_empty_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url cannot be empty"):
            ZammadRESTClientViaUsernamePassword("", "u", "p")

    def test_empty_username_raises(self):
        with pytest.raises(ValueError, match="username cannot be empty"):
            ZammadRESTClientViaUsernamePassword(BASE_URL, "", "p")

    def test_empty_password_raises(self):
        with pytest.raises(ValueError, match="password cannot be empty"):
            ZammadRESTClientViaUsernamePassword(BASE_URL, "u", "")


class TestZammadRESTClientViaToken:
    def test_init(self):
        client = ZammadRESTClientViaToken(BASE_URL, "my-token")
        assert client.base_url == BASE_URL
        assert client.token == "my-token"
        assert client.headers["Authorization"] == "Token token=my-token"

    def test_get_base_url(self):
        client = ZammadRESTClientViaToken(BASE_URL, "tok")
        assert client.get_base_url() == BASE_URL

    def test_empty_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url cannot be empty"):
            ZammadRESTClientViaToken("", "tok")

    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="token cannot be empty"):
            ZammadRESTClientViaToken(BASE_URL, "")


class TestZammadRESTClientViaOAuth2:
    def test_init(self):
        client = ZammadRESTClientViaOAuth2(BASE_URL, "bearer-token")
        assert client.base_url == BASE_URL
        assert client.bearer_token == "bearer-token"
        assert "Bearer bearer-token" in client.headers["Authorization"]

    def test_get_base_url(self):
        client = ZammadRESTClientViaOAuth2(BASE_URL, "bt")
        assert client.get_base_url() == BASE_URL

    def test_empty_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url cannot be empty"):
            ZammadRESTClientViaOAuth2("", "bt")

    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="bearer_token cannot be empty"):
            ZammadRESTClientViaOAuth2(BASE_URL, "")


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------


class TestZammadUsernamePasswordConfig:
    def test_create_client(self):
        cfg = ZammadUsernamePasswordConfig(base_url=BASE_URL, username="u", password="p")
        client = cfg.create_client()
        assert isinstance(client, ZammadRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = ZammadUsernamePasswordConfig(base_url=BASE_URL, username="u", password="p")
        d = cfg.to_dict()
        assert d["base_url"] == BASE_URL
        assert d["username"] == "u"


class TestZammadTokenConfig:
    def test_create_client(self):
        cfg = ZammadTokenConfig(base_url=BASE_URL, token="tok")
        client = cfg.create_client()
        assert isinstance(client, ZammadRESTClientViaToken)

    def test_to_dict(self):
        cfg = ZammadTokenConfig(base_url=BASE_URL, token="tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"
        assert d["base_url"] == BASE_URL


class TestZammadOAuth2Config:
    def test_create_client(self):
        cfg = ZammadOAuth2Config(base_url=BASE_URL, bearer_token="bt")
        client = cfg.create_client()
        assert isinstance(client, ZammadRESTClientViaOAuth2)

    def test_to_dict(self):
        cfg = ZammadOAuth2Config(base_url=BASE_URL, bearer_token="bt")
        d = cfg.to_dict()
        assert d["bearer_token"] == "bt"
        assert d["base_url"] == BASE_URL


# ---------------------------------------------------------------------------
# ZammadClient
# ---------------------------------------------------------------------------


class TestZammadClient:
    def test_init(self):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = BASE_URL
        client = ZammadClient(mock_client)
        assert client.get_client() is mock_client
        assert client.get_base_url() == BASE_URL

    def test_build_with_config_username(self):
        cfg = ZammadUsernamePasswordConfig(base_url=BASE_URL, username="u", password="p")
        client = ZammadClient.build_with_config(cfg)
        assert isinstance(client, ZammadClient)

    def test_build_with_config_token(self):
        cfg = ZammadTokenConfig(base_url=BASE_URL, token="tok")
        client = ZammadClient.build_with_config(cfg)
        assert isinstance(client, ZammadClient)

    def test_build_with_config_oauth2(self):
        cfg = ZammadOAuth2Config(base_url=BASE_URL, bearer_token="bt")
        client = ZammadClient.build_with_config(cfg)
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_username_password(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "baseUrl": BASE_URL,
                    "username": "user",
                    "password": "pass",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_basic_alias(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "BASIC",
                    "baseUrl": BASE_URL,
                    "username": "user",
                    "password": "pass",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "TOKEN",
                    "baseUrl": BASE_URL,
                    "token": "tok",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_api_token_alias(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "API_TOKEN",
                    "baseUrl": BASE_URL,
                    "token": "tok",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth2(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH2",
                    "baseUrl": BASE_URL,
                    "bearerToken": "bt",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_bearer_alias(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "BEARER",
                    "baseUrl": BASE_URL,
                    "bearerToken": "bt",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_alias(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH",
                    "baseUrl": BASE_URL,
                    "accessToken": "at",
                }
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED", "baseUrl": BASE_URL}
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_base_url(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "TOKEN", "token": "tok"}
            }
        )
        with pytest.raises(ValueError, match="Base URL not found"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "baseUrl": BASE_URL,
                }
            }
        )
        with pytest.raises(ValueError, match="Username and password"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "TOKEN", "baseUrl": BASE_URL}
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_bearer_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH2", "baseUrl": BASE_URL}
            }
        )
        with pytest.raises(ValueError, match="Bearer token required"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_empty_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Failed to get Zammad"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_empty_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await ZammadClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_base_url_from_top_level(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "baseUrl": BASE_URL,
                "auth": {"authType": "TOKEN", "token": "tok"},
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_build_from_services_no_instance_id(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "TOKEN", "baseUrl": BASE_URL, "token": "tok"}
            }
        )
        client = await ZammadClient.build_from_services(
            logger, mock_config_service
        )
        assert isinstance(client, ZammadClient)

    @pytest.mark.asyncio
    async def test_get_connector_config_with_instance_id(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ZammadClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}
        mock_config_service.get_config.assert_called_once_with(
            "/services/connectors/inst-1/config"
        )

    @pytest.mark.asyncio
    async def test_get_connector_config_without_instance_id(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ZammadClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {"auth": {}}
        mock_config_service.get_config.assert_called_once_with(
            "/services/connectors/zammad/config"
        )

    @pytest.mark.asyncio
    async def test_get_connector_config_none(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        result = await ZammadClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        result = await ZammadClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {}
