"""Unit tests for Zendesk client module."""

import base64
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.sources.client.zendesk.zendesk import (
    ZendeskClient,
    ZendeskOAuthConfig,
    ZendeskRESTClientViaOAuth,
    ZendeskRESTClientViaToken,
    ZendeskResponse,
    ZendeskTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_zendesk_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


SUBDOMAIN = "acme"
EMAIL = "agent@acme.com"
TOKEN = "test-api-token"
BASE_URL = "https://acme.zendesk.com/api/v2"


def _oauth_config():
    return {
        "auth": {
            "authType": "OAUTH",
            "subdomain": SUBDOMAIN,
            "clientId": "cid",
            "clientSecret": "secret",
            "redirectUri": "https://cb",
        },
        "credentials": {"access_token": "tok"},
    }


# ---------------------------------------------------------------------------
# ZendeskResponse
# ---------------------------------------------------------------------------


class TestZendeskResponse:
    def test_to_dict(self):
        resp = ZendeskResponse(success=True, data={"k": "v"})
        assert resp.to_dict()["success"] is True

    def test_to_json(self):
        resp = ZendeskResponse(success=True, data={"k": "v"})
        assert json.loads(resp.to_json())["success"] is True

    def test_defaults_are_none(self):
        resp = ZendeskResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None
        assert resp.status_code is None

    def test_status_code_survives_serialization(self):
        """Without the declared field Pydantic dropped it, leaving the retry path
        unable to tell a retryable 429 from a fatal 401."""
        resp = ZendeskResponse(success=False, error="Too Many Requests", status_code=429)
        assert resp.to_dict()["status_code"] == 429
        assert json.loads(resp.to_json())["status_code"] == 429


# ---------------------------------------------------------------------------
# ZendeskRESTClientViaToken
# ---------------------------------------------------------------------------


class TestZendeskRESTClientViaToken:
    def test_base_url_built_from_subdomain(self):
        client = ZendeskRESTClientViaToken(SUBDOMAIN, TOKEN, EMAIL)
        assert client.get_base_url() == BASE_URL

    def test_get_subdomain(self):
        client = ZendeskRESTClientViaToken(SUBDOMAIN, TOKEN, EMAIL)
        assert client.get_subdomain() == SUBDOMAIN

    def test_basic_auth_header_uses_email_slash_token_scheme(self):
        """Zendesk API-token auth is Basic with the literal ``{email}/token`` username."""
        client = ZendeskRESTClientViaToken(SUBDOMAIN, TOKEN, EMAIL)
        scheme, _, encoded = client.headers["Authorization"].partition(" ")
        assert scheme == "Basic"
        assert base64.b64decode(encoded).decode() == f"{EMAIL}/token:{TOKEN}"


# ---------------------------------------------------------------------------
# ZendeskRESTClientViaOAuth
# ---------------------------------------------------------------------------


class TestZendeskRESTClientViaOAuth:
    def test_base_and_oauth_urls(self):
        client = ZendeskRESTClientViaOAuth(SUBDOMAIN, "cid", "secret", "https://cb", "tok")
        assert client.get_base_url() == BASE_URL
        assert client.oauth_base_url == f"https://{SUBDOMAIN}.zendesk.com/oauth"

    def test_oauth_completed_when_access_token_supplied(self):
        client = ZendeskRESTClientViaOAuth(SUBDOMAIN, "cid", "secret", "https://cb", "tok")
        assert client.is_oauth_completed() is True

    def test_oauth_not_completed_without_access_token(self):
        client = ZendeskRESTClientViaOAuth(SUBDOMAIN, "cid", "secret", "https://cb")
        assert client.is_oauth_completed() is False

    def test_get_subdomain(self):
        client = ZendeskRESTClientViaOAuth(SUBDOMAIN, "cid", "secret", "https://cb", "tok")
        assert client.get_subdomain() == SUBDOMAIN


# ---------------------------------------------------------------------------
# Config objects
# ---------------------------------------------------------------------------


class TestZendeskConfigs:
    def test_token_config_creates_token_client(self):
        config = ZendeskTokenConfig(subdomain=SUBDOMAIN, token=TOKEN, email=EMAIL)
        client = config.create_client()
        assert isinstance(client, ZendeskRESTClientViaToken)
        assert client.get_base_url() == BASE_URL

    def test_token_config_to_dict(self):
        config = ZendeskTokenConfig(subdomain=SUBDOMAIN, token=TOKEN, email=EMAIL)
        assert config.to_dict()["subdomain"] == SUBDOMAIN

    def test_oauth_config_creates_oauth_client(self):
        config = ZendeskOAuthConfig(
            subdomain=SUBDOMAIN,
            client_id="cid",
            client_secret="secret",
            redirect_uri="https://cb",
            access_token="tok",
        )
        assert isinstance(config.create_client(), ZendeskRESTClientViaOAuth)

    def test_oauth_config_access_token_optional(self):
        config = ZendeskOAuthConfig(
            subdomain=SUBDOMAIN,
            client_id="cid",
            client_secret="secret",
            redirect_uri="https://cb",
        )
        assert config.access_token is None


# ---------------------------------------------------------------------------
# ZendeskClient
# ---------------------------------------------------------------------------


class TestZendeskClient:
    def test_build_with_token_config(self):
        config = ZendeskTokenConfig(subdomain=SUBDOMAIN, token=TOKEN, email=EMAIL)
        client = ZendeskClient.build_with_config(config)
        assert client.get_base_url() == BASE_URL
        assert client.get_subdomain() == SUBDOMAIN

    def test_get_client_returns_underlying(self):
        config = ZendeskTokenConfig(subdomain=SUBDOMAIN, token=TOKEN, email=EMAIL)
        client = ZendeskClient.build_with_config(config)
        assert isinstance(client.get_client(), ZendeskRESTClientViaToken)

    async def test_build_from_services_reads_connector_scoped_config_path(
        self, logger, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(return_value=_oauth_config())
        await ZendeskClient.build_from_services(
            logger=logger,
            config_service=mock_config_service,
            connector_instance_id="zd-42",
        )
        mock_config_service.get_config.assert_awaited_once_with(
            "/services/connectors/zd-42/config"
        )

    async def test_build_from_services_oauth(self, logger, mock_config_service):
        """handle_callback stores the token at the config root; reading it from
        "auth" yielded an empty Bearer header and every call 401'd."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "subdomain": SUBDOMAIN,
                "clientId": "cid",
                "clientSecret": "secret",
                "redirectUri": "https://cb",
            },
            "credentials": {"access_token": "tok"},
        })
        client = await ZendeskClient.build_from_services(
            logger=logger,
            config_service=mock_config_service,
            connector_instance_id="zd-1",
        )
        inner = client.get_client()
        assert isinstance(inner, ZendeskRESTClientViaOAuth)
        assert inner.headers["Authorization"] == "Bearer tok"

    async def test_build_from_services_oauth_rejects_missing_token(
        self, logger, mock_config_service
    ):
        """Fail loudly rather than building a client with an empty Bearer header."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH",
                "subdomain": SUBDOMAIN,
                "clientId": "cid",
                "redirectUri": "https://cb",
                # Wrong nesting — the shape that used to silently pass.
                "credentials": {"access_token": "tok"},
            },
        })
        with pytest.raises(ValueError, match="OAuth token required"):
            await ZendeskClient.build_from_services(
                logger=logger,
                config_service=mock_config_service,
                connector_instance_id="zd-1",
            )

    async def test_build_from_services_defaults_to_oauth(self, logger, mock_config_service):
        """Zendesk retires API tokens on 2027-04-30, so an absent authType is OAuth."""
        config = _oauth_config()
        config["auth"].pop("authType")
        mock_config_service.get_config = AsyncMock(return_value=config)

        client = await ZendeskClient.build_from_services(
            logger=logger,
            config_service=mock_config_service,
            connector_instance_id="zd-1",
        )
        assert isinstance(client.get_client(), ZendeskRESTClientViaOAuth)

    async def test_build_from_services_rejects_api_token(self, logger, mock_config_service):
        """The auth option is gone from the registry; a stale stored config must fail
        loudly rather than build a client on a credential Zendesk is retiring."""
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "subdomain": SUBDOMAIN,
                     "apiToken": TOKEN, "email": EMAIL}
        })
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ZendeskClient.build_from_services(
                logger=logger,
                config_service=mock_config_service,
                connector_instance_id="zd-1",
            )

    async def test_build_from_services_rejects_unknown_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "SAML", "subdomain": SUBDOMAIN}
        })
        with pytest.raises(ValueError):
            await ZendeskClient.build_from_services(
                logger=logger,
                config_service=mock_config_service,
                connector_instance_id="zd-1",
            )

    async def test_build_from_services_raises_when_config_missing(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ZendeskClient.build_from_services(
                logger=logger,
                config_service=mock_config_service,
                connector_instance_id="zd-1",
            )
