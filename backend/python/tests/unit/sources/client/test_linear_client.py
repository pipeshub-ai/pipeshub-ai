"""Unit tests for Linear client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.linear.linear import (
    LinearClient,
    LinearGraphQLClientViaOAuth,
    LinearGraphQLClientViaToken,
    LinearOAuthConfig,
    LinearTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_linear_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# LinearGraphQLClientViaToken
# ---------------------------------------------------------------------------


class TestLinearGraphQLClientViaToken:
    def test_init(self):
        client = LinearGraphQLClientViaToken("lin_api_token")
        assert client.token == "lin_api_token"
        assert client.endpoint == "https://api.linear.app/graphql"
        assert client.headers["Authorization"] == "lin_api_token"
        assert client.headers["Content-Type"] == "application/json"

    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            LinearGraphQLClientViaToken("")

    def test_whitespace_token_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            LinearGraphQLClientViaToken("   ")

    def test_token_stripped(self):
        client = LinearGraphQLClientViaToken("  tok  ")
        assert client.token == "tok"

    def test_get_endpoint(self):
        client = LinearGraphQLClientViaToken("tok")
        assert client.get_endpoint() == "https://api.linear.app/graphql"

    def test_get_auth_header(self):
        client = LinearGraphQLClientViaToken("tok")
        assert client.get_auth_header() == "tok"

    def test_get_token(self):
        client = LinearGraphQLClientViaToken("tok")
        assert client.get_token() == "tok"

    def test_set_token(self):
        client = LinearGraphQLClientViaToken("tok")
        client.set_token("new-tok")
        assert client.token == "new-tok"
        assert client.headers["Authorization"] == "new-tok"

    def test_custom_timeout(self):
        client = LinearGraphQLClientViaToken("tok", timeout=60)
        assert client.timeout == 60


# ---------------------------------------------------------------------------
# LinearGraphQLClientViaOAuth
# ---------------------------------------------------------------------------


class TestLinearGraphQLClientViaOAuth:
    def test_init_without_bearer_prefix(self):
        client = LinearGraphQLClientViaOAuth("oauth-tok")
        assert client.oauth_token == "oauth-tok"
        assert client.headers["Authorization"] == "Bearer oauth-tok"

    def test_init_with_bearer_prefix(self):
        client = LinearGraphQLClientViaOAuth("Bearer oauth-tok")
        assert client.oauth_token == "Bearer oauth-tok"
        assert client.headers["Authorization"] == "Bearer oauth-tok"

    def test_get_endpoint(self):
        client = LinearGraphQLClientViaOAuth("tok")
        assert client.get_endpoint() == "https://api.linear.app/graphql"

    def test_get_auth_header_adds_bearer(self):
        client = LinearGraphQLClientViaOAuth("tok")
        assert client.get_auth_header() == "Bearer tok"

    def test_get_auth_header_already_bearer(self):
        client = LinearGraphQLClientViaOAuth("Bearer tok")
        assert client.get_auth_header() == "Bearer tok"

    def test_get_token(self):
        client = LinearGraphQLClientViaOAuth("tok")
        assert client.get_token() == "tok"

    def test_set_token_without_bearer(self):
        client = LinearGraphQLClientViaOAuth("tok")
        client.set_token("new-tok")
        assert client.oauth_token == "new-tok"
        assert client.headers["Authorization"] == "Bearer new-tok"

    def test_set_token_with_bearer(self):
        client = LinearGraphQLClientViaOAuth("tok")
        client.set_token("Bearer new-tok")
        assert client.oauth_token == "Bearer new-tok"
        assert client.headers["Authorization"] == "Bearer new-tok"

    def test_custom_timeout(self):
        client = LinearGraphQLClientViaOAuth("tok", timeout=90)
        assert client.timeout == 90


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class TestLinearTokenConfig:
    def test_create_client(self):
        cfg = LinearTokenConfig(token="tok")
        client = cfg.create_client()
        assert isinstance(client, LinearGraphQLClientViaToken)

    def test_defaults(self):
        cfg = LinearTokenConfig(token="tok")
        assert cfg.timeout == 30
        assert cfg.endpoint == "https://api.linear.app/graphql"


class TestLinearOAuthConfig:
    def test_create_client(self):
        cfg = LinearOAuthConfig(oauth_token="tok")
        client = cfg.create_client()
        assert isinstance(client, LinearGraphQLClientViaOAuth)

    def test_defaults(self):
        cfg = LinearOAuthConfig(oauth_token="tok")
        assert cfg.timeout == 30


# ---------------------------------------------------------------------------
# LinearClient init / get_client
# ---------------------------------------------------------------------------


class TestLinearClientInit:
    def test_init_token(self):
        client = LinearGraphQLClientViaToken("tok")
        lc = LinearClient(client)
        assert lc.get_client() is client

    def test_init_oauth(self):
        client = LinearGraphQLClientViaOAuth("tok")
        lc = LinearClient(client)
        assert lc.get_client() is client


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_token_config(self):
        cfg = LinearTokenConfig(token="tok")
        lc = LinearClient.build_with_config(cfg)
        assert isinstance(lc, LinearClient)
        assert isinstance(lc.get_client(), LinearGraphQLClientViaToken)

    def test_oauth_config(self):
        cfg = LinearOAuthConfig(oauth_token="tok")
        lc = LinearClient.build_with_config(cfg)
        assert isinstance(lc.get_client(), LinearGraphQLClientViaOAuth)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await LinearClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Linear"):
            await LinearClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get Linear"):
            await LinearClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_api_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "tok"},
            }
        )
        lc = await LinearClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(lc, LinearClient)
        assert isinstance(lc.get_client(), LinearGraphQLClientViaToken)

    @pytest.mark.asyncio
    async def test_oauth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "oauth-tok"},
            }
        )
        lc = await LinearClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(lc.get_client(), LinearGraphQLClientViaOAuth)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await LinearClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        with pytest.raises(ValueError, match="Token required"):
            await LinearClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="OAuth token required"):
            await LinearClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_custom_timeout(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "tok"},
                "timeout": 60,
            }
        )
        lc = await LinearClient.build_from_services(logger, mock_config_service, "inst-1")
        assert lc.get_client().timeout == 60
