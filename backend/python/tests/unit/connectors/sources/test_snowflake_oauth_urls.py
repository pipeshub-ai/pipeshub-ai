"""Snowflake OAuth endpoints must follow the configured account."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import _build_oauth_flow_config
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.sources.snowflake.oauth_urls import SNOWFLAKE_OAUTH_URL_ERROR
from app.utils.oauth_config import check_oauth_url_settings, get_oauth_config

_ROUTER = "app.connectors.api.router"
AUTHORIZE = "https://myorg-account_xyz.snowflakecomputing.com/oauth/authorize"
TOKEN = "https://myorg-account_xyz.snowflakecomputing.com/oauth/token-request"


def _auth(**overrides: object) -> dict[str, Any]:
    return {
        "accountIdentifier": "myorg-account_xyz",
        "clientId": "client",
        "clientSecret": "secret",
        "authorizeUrl": "https://{account}.snowflakecomputing.com/oauth/authorize",
        "tokenUrl": "https://{account}.snowflakecomputing.com/oauth/token-request",
        "connectorScope": "team",
        **overrides,
    }


async def _urls(auth: dict[str, Any], shared: dict[str, Any] | None = None) -> tuple[str, str]:
    with patch(f"{_ROUTER}.resolve_shared_oauth_config_for_flow", AsyncMock(return_value=shared)):
        flow = await _build_oauth_flow_config(
            auth,
            "SNOWFLAKE",
            "org-1",
            AsyncMock(),
            logging.getLogger("test"),
            registry_type="Snowflake",
        )
    oauth = get_oauth_config(flow)
    return oauth.authorize_url, oauth.token_url


@pytest.mark.asyncio
async def test_account_identifier_replaces_registry_placeholder() -> None:
    assert await _urls(_auth()) == (AUTHORIZE, TOKEN)


@pytest.mark.asyncio
async def test_full_account_url_is_normalized() -> None:
    assert await _urls(_auth(accountIdentifier="https://myorg-account_xyz.snowflakecomputing.com/")) == (
        AUTHORIZE,
        TOKEN,
    )


@pytest.mark.asyncio
async def test_shared_oauth_config_carries_account_identifier() -> None:
    shared = {
        "authorizeUrl": "https://{account}.snowflakecomputing.com/oauth/authorize",
        "tokenUrl": "https://{account}.snowflakecomputing.com/oauth/token-request",
        "config": {
            "accountIdentifier": "myorg-account_xyz",
            "clientId": "client",
            "clientSecret": "secret",
        },
    }
    assert await _urls({"oauthConfigId": "app-1", "connectorScope": "team"}, shared) == (
        AUTHORIZE,
        TOKEN,
    )


@pytest.mark.asyncio
async def test_missing_account_identifier_stops_the_flow() -> None:
    with pytest.raises(HTTPException) as refused:
        await _urls(_auth(accountIdentifier=""))
    assert refused.value.detail == SNOWFLAKE_OAUTH_URL_ERROR


async def test_refresh_uses_account_specific_token_url() -> None:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"auth": _auth(), "credentials": {"refresh_token": "refresh"}}
    )
    service = TokenRefreshService(config_service, MagicMock(), None)
    service._persist_refreshed_credentials = AsyncMock()
    provider = MagicMock()
    provider.refresh_access_token = AsyncMock(return_value=OAuthToken(access_token="access", refresh_token="refresh-2"))
    provider.close = AsyncMock()
    with patch(
        "app.connectors.core.base.token_service.oauth_service.OAuthProvider", return_value=provider
    ) as provider_cls:
        await service._perform_token_refresh("conn-1", "Snowflake", "refresh")
    assert provider_cls.call_args.kwargs["config"].token_url == TOKEN


@pytest.mark.parametrize(
    "account_identifier",
    ["", "https://evil.example/path", "https://user@myorg-account_xyz.snowflakecomputing.com", "a b"],
)
def test_invalid_account_identifier_is_rejected(account_identifier: str) -> None:
    with pytest.raises(ValueError, match="Snowflake account identifier"):
        check_oauth_url_settings("Snowflake", {"accountIdentifier": account_identifier})


def test_other_connectors_are_untouched() -> None:
    check_oauth_url_settings("Jira", {"accountIdentifier": "not-used"})
