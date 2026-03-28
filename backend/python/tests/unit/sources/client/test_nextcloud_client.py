"""Unit tests for Nextcloud client module."""

import base64
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudRESTClientViaToken,
    NextcloudRESTClientViaUsernamePassword,
    NextcloudTokenConfig,
    NextcloudUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_nextcloud_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


BASE_URL = "https://nextcloud.example.com"


# ---------------------------------------------------------------------------
# NextcloudRESTClientViaUsernamePassword
# ---------------------------------------------------------------------------


class TestNextcloudRESTClientViaUsernamePassword:
    def test_init(self):
        client = NextcloudRESTClientViaUsernamePassword(BASE_URL, "user", "pass")
        assert client.base_url == BASE_URL
        expected_auth = base64.b64encode(b"user:pass").decode("utf-8")
        assert client.headers["Authorization"] == f"Basic {expected_auth}"

    def test_get_base_url(self):
        client = NextcloudRESTClientViaUsernamePassword(BASE_URL, "u", "p")
        assert client.get_base_url() == BASE_URL


# ---------------------------------------------------------------------------
# NextcloudRESTClientViaToken
# ---------------------------------------------------------------------------


class TestNextcloudRESTClientViaToken:
    def test_init(self):
        client = NextcloudRESTClientViaToken(BASE_URL, "my-token")
        assert client.base_url == BASE_URL
        assert client.token == "my-token"

    def test_get_base_url(self):
        client = NextcloudRESTClientViaToken(BASE_URL, "tok")
        assert client.get_base_url() == BASE_URL

    def test_get_token(self):
        client = NextcloudRESTClientViaToken(BASE_URL, "tok")
        assert client.get_token() == "tok"

    def test_set_token(self):
        client = NextcloudRESTClientViaToken(BASE_URL, "tok")
        client.set_token("new-tok")
        assert client.get_token() == "new-tok"
        assert client.headers["Authorization"] == "Bearer new-tok"

    def test_custom_token_type(self):
        client = NextcloudRESTClientViaToken(BASE_URL, "tok", token_type="Custom")
        assert client.base_url == BASE_URL


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------


class TestNextcloudUsernamePasswordConfig:
    def test_create_client(self):
        cfg = NextcloudUsernamePasswordConfig(
            base_url=BASE_URL, username="user", password="pass"
        )
        client = cfg.create_client()
        assert isinstance(client, NextcloudRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = NextcloudUsernamePasswordConfig(
            base_url=BASE_URL, username="user", password="pass"
        )
        d = cfg.to_dict()
        assert d["base_url"] == BASE_URL
        assert d["username"] == "user"
        assert d["ssl"] is True


class TestNextcloudTokenConfig:
    def test_create_client(self):
        cfg = NextcloudTokenConfig(base_url=BASE_URL, token="tok")
        client = cfg.create_client()
        assert isinstance(client, NextcloudRESTClientViaToken)

    def test_to_dict(self):
        cfg = NextcloudTokenConfig(base_url=BASE_URL, token="tok")
        d = cfg.to_dict()
        assert d["base_url"] == BASE_URL
        assert d["token"] == "tok"


# ---------------------------------------------------------------------------
# NextcloudClient
# ---------------------------------------------------------------------------


class TestNextcloudClient:
    def test_init(self):
        mock_client = MagicMock()
        client = NextcloudClient(mock_client)
        assert client.get_client() is mock_client

    def test_build_with_config_username_password(self):
        cfg = NextcloudUsernamePasswordConfig(
            base_url=BASE_URL, username="user", password="pass"
        )
        client = NextcloudClient.build_with_config(cfg)
        assert isinstance(client, NextcloudClient)

    def test_build_with_config_token(self):
        cfg = NextcloudTokenConfig(base_url=BASE_URL, token="tok")
        client = NextcloudClient.build_with_config(cfg)
        assert isinstance(client, NextcloudClient)

    @pytest.mark.asyncio
    async def test_build_from_services_basic_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "BASIC_AUTH",
                    "username": "user",
                    "password": "pass",
                },
                "credentials": {"baseUrl": BASE_URL},
            }
        )
        client = await NextcloudClient.build_from_services(
            logger, mock_config_service
        )
        assert isinstance(client, NextcloudClient)

    @pytest.mark.asyncio
    async def test_build_from_services_bearer_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN", "bearerToken": "my-token"},
                "credentials": {"baseUrl": BASE_URL},
            }
        )
        client = await NextcloudClient.build_from_services(
            logger, mock_config_service
        )
        assert isinstance(client, NextcloudClient)

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {"baseUrl": BASE_URL},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_base_url(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BASIC_AUTH", "username": "u", "password": "p"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="baseUrl.*required"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BASIC_AUTH"},
                "credentials": {"baseUrl": BASE_URL},
            }
        )
        with pytest.raises(ValueError, match="Username and Password"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_bearer_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BEARER_TOKEN"},
                "credentials": {"baseUrl": BASE_URL},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_empty_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Failed to get Nextcloud"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_empty_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": {"baseUrl": BASE_URL}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await NextcloudClient.build_from_services(
                logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_build_from_services_base_url_from_top_level(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "BASIC_AUTH", "username": "u", "password": "p"},
                "baseUrl": BASE_URL,
            }
        )
        client = await NextcloudClient.build_from_services(
            logger, mock_config_service
        )
        assert isinstance(client, NextcloudClient)

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await NextcloudClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_none(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        result = await NextcloudClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        result = await NextcloudClient._get_connector_config(
            logger, mock_config_service
        )
        assert result == {}
