"""Unit tests for GitLab client module."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.gitlab.gitlab import (
    GitLabClient,
    GitLabClientViaToken,
    GitLabConfig,
    GitLabResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_gitlab_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# GitLabResponse
# ---------------------------------------------------------------------------


class TestGitLabResponse:
    def test_success_response(self):
        resp = GitLabResponse(success=True, data={"projects": []})
        assert resp.success is True
        assert resp.data == {"projects": []}

    def test_error_response(self):
        resp = GitLabResponse(success=False, error="not found")
        assert resp.error == "not found"

    def test_default_nones(self):
        resp = GitLabResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None

    def test_to_dict(self):
        resp = GitLabResponse(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}
        assert d["message"] == "ok"


# ---------------------------------------------------------------------------
# GitLabClientViaToken
# ---------------------------------------------------------------------------


class TestGitLabClientViaToken:
    def test_init_defaults(self):
        client = GitLabClientViaToken("tok")
        assert client.token == "tok"
        assert client.url == "https://gitlab.com"
        assert client.timeout is None
        assert client.api_version == "4"
        assert client.retry_transient_errors is None
        assert client.max_retries is None
        assert client.obey_rate_limit is None
        assert client._sdk is None

    def test_init_with_custom_url(self):
        client = GitLabClientViaToken("tok", url="https://gitlab.example.com")
        assert client.url == "https://gitlab.example.com"

    def test_init_with_all_options(self):
        client = GitLabClientViaToken(
            "tok",
            url="https://gl.local",
            timeout=60.0,
            api_version="4",
            retry_transient_errors=True,
            max_retries=5,
            obey_rate_limit=True,
        )
        assert client.url == "https://gl.local"
        assert client.timeout == 60.0
        assert client.retry_transient_errors is True
        assert client.max_retries == 5
        assert client.obey_rate_limit is True

    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_create_client_minimal_kwargs(self, mock_gitlab_module):
        client = GitLabClientViaToken("tok")
        sdk = client.create_client()
        mock_gitlab_module.Gitlab.assert_called_once()
        call_kwargs = mock_gitlab_module.Gitlab.call_args[1]
        assert call_kwargs["url"] == "https://gitlab.com"
        assert call_kwargs["oauth_token"] == "tok"
        assert call_kwargs["api_version"] == "4"
        # Optional kwargs should NOT be in call_kwargs when unset
        assert "timeout" not in call_kwargs
        assert "retry_transient_errors" not in call_kwargs
        assert "max_retries" not in call_kwargs
        assert "obey_rate_limit" not in call_kwargs
        assert sdk is mock_gitlab_module.Gitlab.return_value
        assert client._sdk is mock_gitlab_module.Gitlab.return_value

    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_create_client_all_kwargs(self, mock_gitlab_module):
        client = GitLabClientViaToken(
            "tok",
            url="https://gl.local",
            timeout=60.0,
            api_version="4",
            retry_transient_errors=True,
            max_retries=5,
            obey_rate_limit=True,
        )
        client.create_client()
        call_kwargs = mock_gitlab_module.Gitlab.call_args[1]
        assert call_kwargs["url"] == "https://gl.local"
        assert call_kwargs["oauth_token"] == "tok"
        assert call_kwargs["timeout"] == 60.0
        assert call_kwargs["api_version"] == "4"
        assert call_kwargs["retry_transient_errors"] is True
        assert call_kwargs["max_retries"] == 5
        assert call_kwargs["obey_rate_limit"] is True

    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_create_client_skips_api_version_when_none(self, mock_gitlab_module):
        client = GitLabClientViaToken("tok", api_version=None)
        client.create_client()
        call_kwargs = mock_gitlab_module.Gitlab.call_args[1]
        assert "api_version" not in call_kwargs

    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_get_sdk_lazy_init(self, mock_gitlab_module):
        client = GitLabClientViaToken("tok")
        assert client._sdk is None
        sdk = client.get_sdk()
        assert sdk is mock_gitlab_module.Gitlab.return_value
        # A second call should return the cached SDK (no additional Gitlab() call)
        sdk2 = client.get_sdk()
        assert sdk2 is sdk
        assert mock_gitlab_module.Gitlab.call_count == 1

    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_get_sdk_returns_cached(self, mock_gitlab_module):
        client = GitLabClientViaToken("tok")
        client.create_client()
        assert mock_gitlab_module.Gitlab.call_count == 1
        # Subsequent get_sdk should not call Gitlab() again
        client.get_sdk()
        assert mock_gitlab_module.Gitlab.call_count == 1

    def test_get_base_url(self):
        client = GitLabClientViaToken("tok", url="https://gl.local")
        assert client.get_base_url() == "https://gl.local"

    def test_get_base_url_default(self):
        client = GitLabClientViaToken("tok")
        assert client.get_base_url() == "https://gitlab.com"

    def test_get_token(self):
        client = GitLabClientViaToken("my-token")
        assert client.get_token() == "my-token"


# ---------------------------------------------------------------------------
# GitLabConfig
# ---------------------------------------------------------------------------


class TestGitLabConfig:
    def test_defaults(self):
        cfg = GitLabConfig(token="tok")
        assert cfg.token == "tok"
        assert cfg.url == "https://gitlab.com"
        assert cfg.timeout is None
        assert cfg.api_version == "4"
        assert cfg.retry_transient_errors is None
        assert cfg.max_retries is None
        assert cfg.obey_rate_limit is None

    def test_create_client(self):
        cfg = GitLabConfig(
            token="tok",
            url="https://gl.local",
            timeout=30.0,
            api_version="4",
            retry_transient_errors=True,
            max_retries=3,
            obey_rate_limit=False,
        )
        client = cfg.create_client()
        assert isinstance(client, GitLabClientViaToken)
        assert client.token == "tok"
        assert client.url == "https://gl.local"
        assert client.timeout == 30.0
        assert client.api_version == "4"
        assert client.retry_transient_errors is True
        assert client.max_retries == 3
        assert client.obey_rate_limit is False


# ---------------------------------------------------------------------------
# GitLabClient init / get_client / get_sdk / get_token
# ---------------------------------------------------------------------------


class TestGitLabClientInit:
    def test_init(self):
        inner = MagicMock(spec=GitLabClientViaToken)
        gc = GitLabClient(inner)
        assert gc.get_client() is inner

    def test_get_sdk_delegates(self):
        inner = MagicMock(spec=GitLabClientViaToken)
        inner.get_sdk.return_value = MagicMock()
        gc = GitLabClient(inner)
        assert gc.get_sdk() is inner.get_sdk.return_value
        inner.get_sdk.assert_called_once()

    def test_get_token_delegates(self):
        inner = MagicMock(spec=GitLabClientViaToken)
        inner.get_token.return_value = "tok"
        gc = GitLabClient(inner)
        assert gc.get_token() == "tok"
        inner.get_token.assert_called_once()


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    @patch("app.sources.client.gitlab.gitlab.gitlab")
    def test_build(self, _mock_gitlab_module):
        cfg = GitLabConfig(token="tok")
        gc = GitLabClient.build_with_config(cfg)
        assert isinstance(gc, GitLabClient)
        assert gc.get_token() == "tok"


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    @patch("app.sources.client.gitlab.gitlab.gitlab")
    async def test_api_token(self, _mock_gitlab, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "token": "tok", "url": "https://gl.local"},
                "credentials": {"something": "x"},
            }
        )
        gc = await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(gc, GitLabClient)
        assert gc.get_token() == "tok"

    @pytest.mark.asyncio
    @patch("app.sources.client.gitlab.gitlab.gitlab")
    async def test_api_token_defaults_when_authtype_missing(
        self, _mock_gitlab, logger, mock_config_service
    ):
        # authType absent → defaults to API_TOKEN
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"token": "tok"},
                "credentials": {"something": "x"},
            }
        )
        gc = await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(gc, GitLabClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.gitlab.gitlab.gitlab")
    async def test_oauth(self, _mock_gitlab, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "oauth-tok"},
            }
        )
        gc = await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(gc, GitLabClient)
        assert gc.get_token() == "oauth-tok"

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get GitLab connector configuration"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_auth_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}, "credentials": {"x": "y"}}
        )
        with pytest.raises(ValueError, match="Auth configuration missing"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_missing_credentials_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "token": "tok"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="Credentials configuration not found"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN"},
                "credentials": {"something": "x"},
            }
        )
        with pytest.raises(ValueError, match="Token required"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_missing_access_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"other": "value"},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {"something": "x"},
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await GitLabClient.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        result = await GitLabClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {"authType": "API_TOKEN"}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get GitLab connector configuration"):
            await GitLabClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get GitLab connector configuration"):
            await GitLabClient._get_connector_config(logger, mock_config_service, "inst-1")
