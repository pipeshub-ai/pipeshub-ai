"""Where Zendesk sign-in, token exchange and refresh send the client secret.

Zendesk's OAuth endpoints are fixed per subdomain, so the saved Authorize and Token
URL fields are never trusted: every request goes to https://<subdomain>.zendesk.com.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _build_oauth_flow_config,
    _create_or_update_oauth_config,
)
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.utils.oauth_config import (
    ZENDESK_OAUTH_URL_ERROR,
    check_zendesk_oauth_settings,
    get_oauth_config,
)

_ROUTER = "app.connectors.api.router"
AUTHORIZE = "https://acme.zendesk.com/oauth/authorizations/new"
TOKEN = "https://acme.zendesk.com/oauth/tokens"


def _auth(**overrides: Any) -> dict[str, Any]:
    return {
        "subdomain": "acme",
        "clientId": "c",
        "clientSecret": "s",
        "authorizeUrl": "http://evil.example/oauth/authorizations/new",
        "tokenUrl": "http://evil.example/oauth/tokens",
        "connectorScope": "team",
        **overrides,
    }


class TestSignInAndCallback:
    async def _urls(self, auth: dict[str, Any], shared: dict[str, Any] | None = None) -> tuple[str, str]:
        with patch(f"{_ROUTER}.resolve_shared_oauth_config_for_flow", AsyncMock(return_value=shared)):
            flow = await _build_oauth_flow_config(
                auth, "ZENDESK", "org-1", AsyncMock(), logging.getLogger("test"), registry_type="Zendesk"
            )
        oauth = get_oauth_config(flow)
        return oauth.authorize_url, oauth.token_url

    async def test_saved_urls_are_replaced_by_the_subdomains_own(self) -> None:
        assert await self._urls(_auth()) == (AUTHORIZE, TOKEN)

    async def test_a_shared_apps_subdomain_is_used(self) -> None:
        shared = {
            "authorizeUrl": "http://evil.example/a",
            "tokenUrl": "http://evil.example/t",
            "config": {"subdomain": "acme", "clientId": "c", "clientSecret": "s"},
        }
        assert await self._urls({"oauthConfigId": "app-1", "connectorScope": "team"}, shared) == (AUTHORIZE, TOKEN)

    @pytest.mark.parametrize("subdomain", ["", "acme.zendesk.com", "evil.example/x", "a b"])
    async def test_a_bad_subdomain_stops_the_flow(self, subdomain: str) -> None:
        with pytest.raises(HTTPException) as refused:
            await self._urls(_auth(subdomain=subdomain))
        assert refused.value.detail == ZENDESK_OAUTH_URL_ERROR


async def test_refresh_goes_to_the_subdomain() -> None:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={"auth": _auth(), "credentials": {"refresh_token": "rt"}})
    service = TokenRefreshService(config_service, MagicMock(), None)
    service._persist_refreshed_credentials = AsyncMock()
    provider = MagicMock()
    provider.refresh_access_token = AsyncMock(return_value=OAuthToken(access_token="at", refresh_token="rt2"))
    provider.close = AsyncMock()
    with patch(
        "app.connectors.core.base.token_service.oauth_service.OAuthProvider", return_value=provider
    ) as provider_cls:
        await service._perform_token_refresh("conn-1", "Zendesk", "rt")
    assert provider_cls.call_args.kwargs["config"].token_url == TOKEN


class TestSaving:
    @pytest.mark.parametrize(
        "field",
        [
            {"tokenUrl": "http://acme.zendesk.com/oauth/tokens"},
            {"tokenUrl": "https://evil.example/oauth/tokens"},
            {"authorizeUrl": "https://acme.zendesk.com.evil.example/oauth/authorizations/new"},
            {"tokenUrl": "https://user@acme.zendesk.com/oauth/tokens"},
            {"subdomain": "acme.zendesk.com"},
        ],
    )
    def test_refused(self, field: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            check_zendesk_oauth_settings("Zendesk", {"subdomain": "acme", **field})

    @pytest.mark.parametrize(
        "settings",
        [
            {"subdomain": "acme", "authorizeUrl": AUTHORIZE, "tokenUrl": TOKEN},
            {"subdomain": "acme"},
            {"oauthConfigId": "app-1"},
        ],
    )
    def test_accepted(self, settings: dict[str, str]) -> None:
        check_zendesk_oauth_settings("Zendesk", settings)

    def test_other_connectors_are_untouched(self) -> None:
        check_zendesk_oauth_settings("Jira", {"tokenUrl": "http://evil.example"})

    async def test_saving_credentials_with_an_http_token_url_is_refused(self) -> None:
        config_service = AsyncMock()
        with pytest.raises(HTTPException) as refused:
            await _create_or_update_oauth_config(
                connector_type="Zendesk",
                auth_config={"subdomain": "acme", "clientId": "c", "clientSecret": "s",
                             "tokenUrl": "http://acme.zendesk.com/oauth/tokens"},
                instance_name="Zendesk",
                user_id="u1",
                org_id="org-1",
                is_admin=True,
                config_service=config_service,
                base_url="",
            )
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        config_service.set_config.assert_not_called()
