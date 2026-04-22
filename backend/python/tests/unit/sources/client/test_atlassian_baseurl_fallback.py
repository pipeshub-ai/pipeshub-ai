"""Coverage for the baseUrl → shared-OAuth-app fallback branches in the Jira
and Confluence clients.

Targets the lines missed by existing `test_*_full_coverage.py` / `_coverage.py`
files after the multi-site OAuth change:

Jira (`app/sources/client/jira/jira.py`):
    * L284        — BEARER_TOKEN with empty ``baseUrl`` → error
    * L300-309    — ``build_from_services`` OAuth: resolve ``baseUrl`` via
                    ``fetch_oauth_config_by_id`` when instance has none
    * L311        — OAuth with empty baseUrl after fallback → error
    * L406-418    — ``build_from_toolset`` OAuth: resolve via
                    ``fetch_toolset_oauth_config_by_id``
    * L420        — toolset OAuth with empty baseUrl after fallback → error

Confluence (`app/sources/client/confluence/confluence.py`): mirror lines
    293, 310-319, 321, 376, 391-403, 405.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.jira.jira import JiraClient


@pytest.fixture
def logger():
    return logging.getLogger("test_atlassian_baseurl_fallback")


def _oauth_instance_config(base_url: str = "", oauth_config_id: str | None = None):
    auth: dict = {"authType": "OAUTH"}
    if base_url:
        auth["baseUrl"] = base_url
    if oauth_config_id:
        auth["oauthConfigId"] = oauth_config_id
    return {"auth": auth, "credentials": {"access_token": "tok-123"}}


# ===========================================================================
# JIRA — build_from_services BEARER_TOKEN
# ===========================================================================

class TestJiraBuildFromServicesBearerEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_raises_when_baseurl_missing(self, logger):
        """Line 284: BEARER_TOKEN auth without baseUrl is rejected."""
        config_service = AsyncMock()
        instance_cfg = {
            "auth": {"authType": "BEARER_TOKEN", "bearerToken": "tok"},
            "credentials": {},
        }
        with patch.object(
            JiraClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ):
            with pytest.raises(ValueError, match="Atlassian site URL .* is required for BEARER_TOKEN"):
                await JiraClient.build_from_services(logger, config_service, "inst")


# ===========================================================================
# JIRA — build_from_services OAUTH fallback via shared OAuth app
# ===========================================================================

class TestJiraBuildFromServicesOAuthFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_shared_oauth_app_baseurl(self, logger):
        """Lines 300-309: instance has no baseUrl but oauthConfigId resolves
        to a shared OAuth app with baseUrl → use that."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config(oauth_config_id="oauth-1")

        shared = {"config": {"baseUrl": "https://shared.atlassian.net"}}

        with patch.object(
            JiraClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch(
            "app.sources.client.jira.jira.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud-x",
        ):
            client = await JiraClient.build_from_services(logger, config_service, "inst")

        assert client is not None
        mock_fetch.assert_awaited_once()
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["oauth_config_id"] == "oauth-1"
        assert kwargs["connector_type"] == "Jira"

    @pytest.mark.asyncio
    async def test_raises_when_no_oauth_config_id_and_no_baseurl(self, logger):
        """No baseUrl, no oauthConfigId, and token has zero accessible sites → error."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config()  # no baseUrl, no oauthConfigId
        with patch.object(
            JiraClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_services(logger, config_service, "inst")

    @pytest.mark.asyncio
    async def test_raises_when_shared_oauth_has_no_baseurl(self, logger):
        """Shared OAuth app exists but baseUrl empty AND token has no accessible sites → error."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config(oauth_config_id="oauth-1")
        shared = {"config": {}}  # no baseUrl
        with patch.object(
            JiraClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch(
            "app.sources.client.jira.jira.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ), patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_services(logger, config_service, "inst")

    @pytest.mark.asyncio
    async def test_raises_when_fetch_returns_none(self, logger):
        """fetch_oauth_config_by_id returns None AND token has no accessible sites → error."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config(oauth_config_id="oauth-missing")
        with patch.object(
            JiraClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch(
            "app.sources.client.jira.jira.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=None),
        ), patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_services(logger, config_service, "inst")


# ===========================================================================
# JIRA — build_from_toolset OAUTH fallback
# ===========================================================================

class TestJiraBuildFromToolsetOAuthFallback:
    @pytest.mark.asyncio
    async def test_falls_back_via_user_auth_oauth_config_id(self, logger):
        """Lines 406-418: oauthConfigId on user_auth resolves shared toolset config."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "tool-oauth-1"},
        }
        shared = {"config": {"baseUrl": "https://agent.atlassian.net"}}
        with patch(
            "app.sources.client.jira.jira.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud-x",
        ):
            client = await JiraClient.build_from_toolset(toolset_cfg, logger, config_service)

        assert client is not None
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["oauth_config_id"] == "tool-oauth-1"
        assert kwargs["toolset_type"] == "Jira"

    @pytest.mark.asyncio
    async def test_falls_back_via_toolset_level_oauth_config_id(self, logger):
        """Toolset-level oauthConfigId works when user_auth doesn't have it."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {},
            "oauthConfigId": "top-level-oauth-1",
        }
        shared = {"config": {"baseUrl": "https://agent2.atlassian.net"}}
        with patch(
            "app.sources.client.jira.jira.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            JiraClient, "get_jira_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/jira/cloud-y",
        ):
            await JiraClient.build_from_toolset(toolset_cfg, logger, config_service)

        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["oauth_config_id"] == "top-level-oauth-1"

    @pytest.mark.asyncio
    async def test_raises_when_baseurl_missing_and_no_config_service(self, logger):
        """config_service is None so shared-oauth fallback is skipped; token has no accessible sites → error."""
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "x"},
        }
        with patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_toolset(toolset_cfg, logger, config_service=None)

    @pytest.mark.asyncio
    async def test_raises_when_no_oauth_config_id_and_no_baseurl(self, logger):
        """No oauthConfigId anywhere AND token has no accessible sites → error."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {},
        }
        with patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_toolset(toolset_cfg, logger, config_service)

    @pytest.mark.asyncio
    async def test_raises_when_shared_toolset_oauth_has_no_baseurl(self, logger):
        """Fetch succeeds but shared.config.baseUrl empty AND token has no accessible sites → error."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "tool-oauth-2"},
        }
        shared = {"config": {}}
        with patch(
            "app.sources.client.jira.jira.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ), patch.object(
            JiraClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Jira sites"):
                await JiraClient.build_from_toolset(toolset_cfg, logger, config_service)


# ===========================================================================
# CONFLUENCE — build_from_services BEARER_TOKEN
# ===========================================================================

class TestConfluenceBuildFromServicesBearerEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_raises_when_baseurl_missing(self, logger):
        """Line 293: BEARER_TOKEN auth without baseUrl is rejected."""
        config_service = AsyncMock()
        instance_cfg = {
            "auth": {"authType": "BEARER_TOKEN", "bearerToken": "tok"},
            "credentials": {},
        }
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ):
            with pytest.raises(ValueError, match="Atlassian site URL .* is required for BEARER_TOKEN"):
                await ConfluenceClient.build_from_services(logger, config_service, "inst")


# ===========================================================================
# CONFLUENCE — build_from_services OAUTH fallback
# ===========================================================================

class TestConfluenceBuildFromServicesOAuthFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_shared_oauth_app_baseurl(self, logger):
        """Lines 310-319: fallback via fetch_oauth_config_by_id."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config(oauth_config_id="c-oauth-1")
        shared = {"config": {"baseUrl": "https://cshared.atlassian.net"}}
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch(
            "app.sources.client.confluence.confluence.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/cloud-x/wiki/api/v2",
        ):
            client = await ConfluenceClient.build_from_services(logger, config_service, "inst")

        assert client is not None
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["oauth_config_id"] == "c-oauth-1"
        assert kwargs["connector_type"] == "Confluence"

    @pytest.mark.asyncio
    async def test_raises_when_no_oauth_config_id_and_no_baseurl(self, logger):
        """No baseUrl anywhere AND token has no accessible sites → error."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config()
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch.object(
            ConfluenceClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Confluence sites"):
                await ConfluenceClient.build_from_services(logger, config_service, "inst")

    @pytest.mark.asyncio
    async def test_raises_when_fetch_returns_none(self, logger):
        """fetch returns None AND token has no accessible sites → error."""
        config_service = AsyncMock()
        instance_cfg = _oauth_instance_config(oauth_config_id="missing")
        with patch.object(
            ConfluenceClient, "_get_connector_config",
            new_callable=AsyncMock, return_value=instance_cfg,
        ), patch(
            "app.sources.client.confluence.confluence.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=None),
        ), patch.object(
            ConfluenceClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Confluence sites"):
                await ConfluenceClient.build_from_services(logger, config_service, "inst")


# ===========================================================================
# CONFLUENCE — build_from_toolset BEARER_TOKEN & OAUTH
# ===========================================================================

class TestConfluenceBuildFromToolsetBearerEmptyBaseUrl:
    @pytest.mark.asyncio
    async def test_bearer_token_toolset_requires_baseurl(self, logger):
        """Line 376: BEARER_TOKEN toolset without baseUrl is rejected."""
        toolset_cfg = {
            "authType": "BEARER_TOKEN",
            "bearerToken": "tok",
            "auth": {},
        }
        with pytest.raises(ValueError, match="Atlassian site URL .* is required for BEARER_TOKEN toolsets"):
            await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service=None)


class TestConfluenceBuildFromToolsetOAuthFallback:
    @pytest.mark.asyncio
    async def test_falls_back_via_user_auth_oauth_config_id(self, logger):
        """Lines 391-403: resolves via fetch_toolset_oauth_config_by_id."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "c-tool-1"},
        }
        shared = {"config": {"baseUrl": "https://cagent.atlassian.net"}}
        with patch(
            "app.sources.client.confluence.confluence.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/c-cloud/wiki/api/v2",
        ):
            client = await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service)

        assert client is not None
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["toolset_type"] == "Confluence"
        assert kwargs["oauth_config_id"] == "c-tool-1"

    @pytest.mark.asyncio
    async def test_falls_back_via_toolset_level_oauth_config_id(self, logger):
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {},
            "oauthConfigId": "c-top-1",
        }
        shared = {"config": {"baseUrl": "https://cagent2.atlassian.net"}}
        with patch(
            "app.sources.client.confluence.confluence.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ) as mock_fetch, patch.object(
            ConfluenceClient, "get_confluence_base_url",
            new_callable=AsyncMock,
            return_value="https://api.atlassian.com/ex/confluence/c-cloud-2/wiki/api/v2",
        ):
            await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service)

        assert mock_fetch.call_args.kwargs["oauth_config_id"] == "c-top-1"

    @pytest.mark.asyncio
    async def test_raises_when_no_config_service(self, logger):
        """config_service None skips shared-oauth fallback; token has no accessible sites → error."""
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "x"},
        }
        with patch.object(
            ConfluenceClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Confluence sites"):
                await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service=None)

    @pytest.mark.asyncio
    async def test_raises_when_no_oauth_config_id(self, logger):
        """No oauthConfigId AND token has no accessible sites → error."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {},
        }
        with patch.object(
            ConfluenceClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Confluence sites"):
                await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service)

    @pytest.mark.asyncio
    async def test_raises_when_shared_has_no_baseurl(self, logger):
        """Shared OAuth app baseUrl empty AND token has no accessible sites → error."""
        config_service = AsyncMock()
        toolset_cfg = {
            "authType": "OAUTH",
            "isAuthenticated": True,
            "credentials": {"access_token": "tok"},
            "auth": {"oauthConfigId": "c-tool-empty"},
        }
        shared = {"config": {}}
        with patch(
            "app.sources.client.confluence.confluence.fetch_toolset_oauth_config_by_id",
            new=AsyncMock(return_value=shared),
        ), patch.object(
            ConfluenceClient, "get_accessible_resources",
            new_callable=AsyncMock, return_value=[],
        ):
            with pytest.raises(ValueError, match="no accessible Confluence sites"):
                await ConfluenceClient.build_from_toolset(toolset_cfg, logger, config_service)
