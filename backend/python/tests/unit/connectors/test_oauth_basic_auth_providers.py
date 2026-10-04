"""Airtable and Zoom take OAuth client credentials only in a Basic header.

Airtable's discovery document lists ``client_secret_basic`` and ``none`` as its only
token endpoint auth methods, and Zoom's lists only ``client_secret_basic``. These
run the real ``get_oauth_config`` and ``OAuthProvider`` against a token endpoint
that refuses a secret in the body.
"""

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.oauth_service import OAuthProvider
from app.utils.oauth_config import get_oauth_config
from tests.support.basic_auth_token_endpoint import FakeBasicAuthTokenEndpoint


def _registered_token_url(toolset: str) -> str:
    if toolset == "airtable":
        from app.agents.actions.airtable.airtable import Airtable as toolset_class
    else:
        from app.agents.actions.zoom.zoom import Zoom as toolset_class
    return toolset_class._toolset_metadata["config"]["auth"]["oauthConfigs"]["OAUTH"]["tokenUrl"]


def _provider(token_url: str, client_secret: str, client_id: str = "app-client-id") -> OAuthProvider:
    config = get_oauth_config({
        "clientId": client_id,
        "clientSecret": client_secret,
        "redirectUri": "https://pipeshub.example/toolsets/oauth/callback",
        "tokenUrl": token_url,
    })
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={})
    config_service.set_config = AsyncMock()
    return OAuthProvider(config, config_service, "/services/toolsets/instance/credentials")


@pytest.mark.asyncio
@pytest.mark.parametrize("toolset", ["airtable", "zoom"])
async def test_code_exchange_and_refresh_send_the_secret_only_in_a_basic_header(toolset: str) -> None:
    token_url = _registered_token_url(toolset)
    endpoint = FakeBasicAuthTokenEndpoint(token_url, "app-client-id", "app-client-secret")
    provider = _provider(token_url, "app-client-secret")

    with patch.object(oauth_service, "ClientSession", endpoint.client_session):
        token = await provider.exchange_code_for_token("auth-code", code_verifier="verifier")
        refreshed = await provider.refresh_access_token(token.refresh_token)

    assert token.access_token == "access-1"
    assert refreshed.access_token == "access-2"
    assert all("client_secret" not in form for _, form in endpoint.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("toolset", ["airtable", "zoom"])
async def test_basic_header_form_encodes_the_id_and_secret_separately(toolset: str) -> None:
    token_url = _registered_token_url(toolset)
    endpoint = FakeBasicAuthTokenEndpoint(token_url, "app:id/1", "s3cr+t/%=:x")
    provider = _provider(token_url, "s3cr+t/%=:x", client_id="app:id/1")

    with patch.object(oauth_service, "ClientSession", endpoint.client_session):
        token = await provider.exchange_code_for_token("auth-code", code_verifier="verifier")
        refreshed = await provider.refresh_access_token(token.refresh_token)

    assert refreshed.access_token == "access-2"
    expected = "Basic " + base64.b64encode(b"app%3Aid%2F1:s3cr%2Bt%2F%25%3D%3Ax").decode()
    assert [headers["Authorization"] for headers, _ in endpoint.requests] == [expected, expected]


@pytest.mark.asyncio
async def test_airtable_public_pkce_client_sends_no_authorization_header() -> None:
    token_url = _registered_token_url("airtable")
    endpoint = FakeBasicAuthTokenEndpoint(token_url, "app-client-id", None)
    provider = _provider(token_url, "")

    with patch.object(oauth_service, "ClientSession", endpoint.client_session):
        token = await provider.exchange_code_for_token("auth-code", code_verifier="verifier")
        refreshed = await provider.refresh_access_token(token.refresh_token)

    assert refreshed.access_token == "access-2"
    assert all("Authorization" not in headers for headers, _ in endpoint.requests)
    assert all(form["client_id"] == "app-client-id" for _, form in endpoint.requests)


def test_basic_auth_marker_stays_out_of_the_authorize_url() -> None:
    provider = _provider(_registered_token_url("airtable"), "app-client-secret")

    url = provider._get_authorization_url(state="s")

    assert "use_basic_auth" not in url
    assert "token_endpoint_auth_method" not in url


@pytest.mark.parametrize(
    "token_url",
    [
        "https://auth.atlassian.com/oauth/token",
        "https://api.clickup.com/api/v2/oauth/token",
        "https://api.linear.app/oauth/token",
        "https://airtable.com.evil.example/token",
        "https://evilzoom.us/oauth/token",
    ],
)
def test_other_token_endpoints_keep_credentials_in_the_body(token_url: str) -> None:
    config = get_oauth_config({"clientId": "c", "clientSecret": "s", "tokenUrl": token_url})

    assert config.token_endpoint_auth_method == "client_secret_post"
