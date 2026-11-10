"""The connectors that refresh their own token after a 401 log the failure without its text.

The refresh error's message embeds the token endpoint's answer, the token URL and the
redirect URI, and a provider can echo the refresh token in prose no redactor recognises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.token_service.oauth_service import OAuthTokenRequestError
from app.connectors.sources.github_teams.runtime import RuntimeHelper as GitHubRuntime
from app.connectors.sources.gitlab.runtime import RuntimeHelper as GitLabRuntime
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.connectors.sources.notion.connector import NotionConnector
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.connectors.sources.servicenow.servicenow.connector import (
    AuthType,
    ServiceNowConnector,
)
from app.connectors.sources.slack.common.token_renewal import SlackTokenRenewal
from app.connectors.sources.zendesk.connector import ZendeskConnector
from app.sources.client.notion.notion import NotionRESTClientViaOAuth
from app.utils.credential_refresh_log import EVENT_CHECK_FAILED
from app.utils.logger import TextFormatter, log_record_fields

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

LOGGER = "test.refresh_caller"
SEEDED_RT = "rt-SEEDED-0123456789abcdef"
LAZY_STARTUP = "app.connectors.core.base.token_service.startup_service.startup_service"


def _leaky_error() -> OAuthTokenRequestError:
    return OAuthTokenRequestError(
        "OAuth token request failed with status 503. Token URL: https://idp.example/oauth/token, "
        f"Redirect URI: https://app.example/cb, Response: refresh token {SEEDED_RT} is not active",
        status=503,
    )


def _config(**credentials: str) -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(return_value={
        "auth": {"authType": "OAUTH"},
        "credentials": {"access_token": "at-held", "refresh_token": "rt-held", **credentials},
    })
    return service


def _refresh_service(error: BaseException) -> MagicMock:
    service = MagicMock()
    service.refresh_now = AsyncMock(side_effect=error)
    service._perform_token_refresh = AsyncMock(side_effect=error)
    return service


def _bare(cls: type) -> object:
    subject = object.__new__(cls)
    subject.logger = logging.getLogger(LOGGER)
    subject.config_service = _config()
    subject.connector_id = "c1"
    subject.connector_name = MagicMock(value="TEST")
    return subject


async def _outlook(error: BaseException) -> object:
    c = _bare(OutlookIndividualConnector)
    c._current_access_token = "at-held"
    c._get_fresh_graph_client = AsyncMock()
    with patch("app.connectors.sources.microsoft.outlook_individual.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await c._renew_token_after_401()


async def _notion(error: BaseException) -> object:
    c = _bare(NotionConnector)
    client = MagicMock(spec=NotionRESTClientViaOAuth)
    client.access_token = "at-held"
    c.notion_client = MagicMock(get_client=MagicMock(return_value=client))
    with patch("app.connectors.sources.notion.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await c._refresh_after_unauthorized()


async def _zendesk(error: BaseException) -> object:
    c = _bare(ZendeskConnector)
    c.external_client = MagicMock(get_client=MagicMock(return_value=MagicMock(access_token="at-held")))
    c._token_refresh_lock = asyncio.Lock()
    with patch("app.connectors.sources.zendesk.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await c._refresh_after_unauthorized()


async def _servicenow(error: BaseException) -> object:
    c = _bare(ServiceNowConnector)
    c.servicenow_client = MagicMock()
    c.auth_type = AuthType.OAUTH
    with patch("app.connectors.sources.servicenow.servicenow.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await c._refresh_after_unauthorized("at-held", "rt-held")


async def _salesforce(error: BaseException) -> object:
    c = _bare(SalesforceConnector)
    c.data_source = MagicMock(limits=AsyncMock(return_value=MagicMock(success=False, status_code=401)))
    c._get_api_version = AsyncMock(return_value="59.0")
    with patch("app.connectors.sources.salesforce.connector.startup_service") as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await c._reinitialize_token_if_needed()


async def _runtime(cls: type, error: BaseException) -> object:
    c = MagicMock(connector_id="c1", connector_name="TEST", config_service=_config(), logger=logging.getLogger(LOGGER))
    # Only a refresh token stored: no newer access token to adopt instead of refreshing.
    c.config_service.get_config.return_value = {"auth": {"authType": "OAUTH"}, "credentials": {"refresh_token": "rt-held"}}
    runtime = cls(c)
    runtime.refresh_token_if_needed = AsyncMock()
    with patch(LAZY_STARTUP) as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await runtime.force_refresh_oauth_token()


async def _slack(error: BaseException) -> object:
    renewal = SlackTokenRenewal("c1", "slack", _config(), logging.getLogger(LOGGER))
    with patch(LAZY_STARTUP) as startup:
        startup.get_token_refresh_service.return_value = _refresh_service(error)
        return await renewal._renew_locked("at-held", "token_expired")


SITES: dict[str, tuple[Callable[[BaseException], Awaitable[object]], object]] = {
    "outlook_individual": (_outlook, False),
    "github_teams": (lambda e: _runtime(GitHubRuntime, e), False),
    "notion": (_notion, False),
    "zendesk": (_zendesk, False),
    "slack": (_slack, None),
    "gitlab": (lambda e: _runtime(GitLabRuntime, e), False),
    "servicenow": (_servicenow, False),
    "salesforce": (_salesforce, False),
}


@pytest.mark.parametrize("site", list(SITES))
async def test_refresh_failure_after_a_401_is_logged_without_the_provider_text(
    site: str, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    drive, returns = SITES[site]

    assert await drive(_leaky_error()) == returns

    [line] = [r for r in caplog.records if r.getMessage() == EVENT_CHECK_FAILED]
    fields = log_record_fields(line)
    assert (line.levelno, fields["error_class"], fields["http_status"]) == (logging.WARNING, "unavailable", 503)
    for record in caplog.records:
        text = TextFormatter().format(record) + repr(log_record_fields(record))
        for leaked in (SEEDED_RT, "idp.example", "app.example/cb", "is not active"):
            assert leaked not in text
