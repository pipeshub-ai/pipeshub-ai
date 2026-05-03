"""Unit tests for GitHub client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.github.github import (
    GitHubClient,
    GitHubClientViaToken,
    GitHubConfig,
    GitHubResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_github_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# GitHubResponse
# ---------------------------------------------------------------------------


class TestGitHubResponse:
    def test_success_response(self):
        resp = GitHubResponse(success=True, data={"repos": []})
        assert resp.success is True
        assert resp.data == {"repos": []}

    def test_error_response(self):
        resp = GitHubResponse(success=False, error="not found")
        assert resp.error == "not found"

    def test_to_dict(self):
        resp = GitHubResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_default_nones(self):
        resp = GitHubResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# GitHubClientViaToken
# ---------------------------------------------------------------------------


class TestGitHubClientViaToken:
    def test_init(self):
        client = GitHubClientViaToken("tok")
        assert client.token == "tok"
        assert client.base_url is None
        assert client._sdk is None

    def test_init_with_options(self):
        client = GitHubClientViaToken("tok", base_url="http://ghe.local/api/v3", timeout=60.0, per_page=50)
        assert client.base_url == "http://ghe.local/api/v3"
        assert client.timeout == 60.0
        assert client.per_page == 50

    def test_get_sdk_before_create_raises(self):
        client = GitHubClientViaToken("tok")
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_sdk()

    @patch("app.sources.client.github.github.Github")
    @patch("app.sources.client.github.github.Auth")
    def test_create_client(self, mock_auth, mock_github):
        client = GitHubClientViaToken("tok")
        client.create_client()
        mock_auth.Token.assert_called_once_with("tok")
        mock_github.assert_called_once()
        assert client._sdk is mock_github.return_value

    @patch("app.sources.client.github.github.Github")
    @patch("app.sources.client.github.github.Auth")
    def test_create_client_with_all_options(self, mock_auth, mock_github):
        client = GitHubClientViaToken("tok", base_url="http://ghe", timeout=60.0, per_page=50)
        client.create_client()
        call_kwargs = mock_github.call_args[1]
        assert call_kwargs["base_url"] == "http://ghe"
        assert call_kwargs["timeout"] == 60.0
        assert call_kwargs["per_page"] == 50

    @patch("app.sources.client.github.github.Github")
    @patch("app.sources.client.github.github.Auth")
    def test_get_sdk_after_create(self, mock_auth, mock_github):
        client = GitHubClientViaToken("tok")
        client.create_client()
        assert client.get_sdk() is mock_github.return_value

    def test_get_base_url(self):
        client = GitHubClientViaToken("tok", base_url="http://ghe")
        assert client.get_base_url() == "http://ghe"

    def test_get_base_url_none(self):
        client = GitHubClientViaToken("tok")
        assert client.get_base_url() is None


# ---------------------------------------------------------------------------
# GitHubConfig
# ---------------------------------------------------------------------------


class TestGitHubConfig:
    @patch("app.sources.client.github.github.Github")
    @patch("app.sources.client.github.github.Auth")
    def test_create_client(self, *_):
        cfg = GitHubConfig(token="tok")
        client = cfg.create_client()
        assert isinstance(client, GitHubClientViaToken)

    def test_defaults(self):
        cfg = GitHubConfig(token="tok")
        assert cfg.base_url is None
        assert cfg.timeout is None
        assert cfg.per_page is None


# ---------------------------------------------------------------------------
# GitHubClient init / get_client
# ---------------------------------------------------------------------------


class TestGitHubClientInit:
    def test_init(self):
        mock_client = MagicMock()
        gc = GitHubClient(mock_client)
        assert gc.get_client() is mock_client

    def test_get_sdk(self):
        mock_client = MagicMock()
        mock_client.get_sdk.return_value = MagicMock()
        gc = GitHubClient(mock_client)
        assert gc.get_sdk() is mock_client.get_sdk.return_value


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    @patch("app.sources.client.github.github.Github")
    @patch("app.sources.client.github.github.Auth")
    def test_build(self, *_):
        cfg = GitHubConfig(token="tok")
        gc = GitHubClient.build_with_config(cfg)
        assert isinstance(gc, GitHubClient)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await GitHubClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get GitHub"):
            await GitHubClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get GitHub"):
            await GitHubClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_api_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "token": "tok"},
                "credentials": {"something": "x"},
            }
        )
        with patch("app.sources.client.github.github.Github"), \
             patch("app.sources.client.github.github.Auth"):
            gc = await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")
            assert isinstance(gc, GitHubClient)

    @pytest.mark.asyncio
    async def test_oauth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "oauth-tok"},
            }
        )
        with patch("app.sources.client.github.github.Github"), \
             patch("app.sources.client.github.github.Auth"):
            gc = await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")
            assert isinstance(gc, GitHubClient)

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_auth_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": {"x": "y"}}
        )
        with pytest.raises(ValueError, match="Auth configuration not found"):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "OAUTH"}, "credentials": {}}
        )
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN"},
                "credentials": {"something": "x"},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"something": "x"},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {"something": "x"},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await GitHubClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await GitHubClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    async def test_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Access token required"):
            await GitHubClient.build_from_toolset(
                {"credentials": {}}, logger
            )

    @pytest.mark.asyncio
    async def test_success(self, logger):
        with patch("app.sources.client.github.github.Github"), \
             patch("app.sources.client.github.github.Auth"):
            gc = await GitHubClient.build_from_toolset(
                {"credentials": {"access_token": "tok"}}, logger
            )
            assert isinstance(gc, GitHubClient)

    @pytest.mark.asyncio
    async def test_none_credentials_raises(self, logger):
        with pytest.raises(ValueError, match="Access token required"):
            await GitHubClient.build_from_toolset(
                {"credentials": None}, logger
            )
