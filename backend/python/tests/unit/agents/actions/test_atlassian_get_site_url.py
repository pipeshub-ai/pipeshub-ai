"""Unit tests for `_get_site_url` in Jira and Confluence agent actions.

Covers the cloud_id extraction + match logic added to support OAuth tokens
that can access multiple Atlassian sites. Regression guard for PR review
items #6 (coverage) and #7 (structured-log format).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.external.common.atlassian import AtlassianCloudResource


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resource(resource_id: str, url: str) -> AtlassianCloudResource:
    return AtlassianCloudResource(id=resource_id, name=resource_id, url=url, scopes=[])


def _build_jira_with_client(inner_client):
    """Instantiate Jira bypassing the ToolsetBuilder decorator and wire up
    ``self.client._client`` to the supplied mock inner client."""
    from app.agents.actions.jira.jira import Jira

    wrapper = MagicMock()
    wrapper._client = inner_client
    jira = Jira.__new__(Jira)
    jira.client = wrapper
    jira._site_url = None
    return jira


def _build_confluence_with_client(inner_client):
    from app.agents.actions.confluence.confluence import Confluence

    wrapper = MagicMock()
    wrapper._client = inner_client
    conf = Confluence.__new__(Confluence)
    conf.client = wrapper
    conf._site_url = None
    return conf


def _oauth_inner(base_url: str, token: str = "access-token"):
    """Inner REST client mock that mimics a token-based (OAuth) client."""
    inner = MagicMock(spec=['get_token', 'get_base_url'])
    inner.get_token.return_value = token
    inner.get_base_url.return_value = base_url
    return inner


def _api_token_inner(base_url: str):
    """Inner REST client mock that mimics an API-token / basic-auth client
    (no ``get_token`` attribute)."""
    inner = MagicMock(spec=['get_base_url'])
    inner.get_base_url.return_value = base_url
    return inner


# ===========================================================================
# Jira
# ===========================================================================

class TestJiraGetSiteUrl:
    @pytest.mark.asyncio
    async def test_cloud_id_matches_returns_correct_site(self):
        jira = _build_jira_with_client(
            _oauth_inner("https://api.atlassian.com/ex/jira/abc-123")
        )
        resources = [
            _resource("xyz", "https://wrong.atlassian.net"),
            _resource("abc-123", "https://right.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await jira._get_site_url()

        assert result == "https://right.atlassian.net"
        assert jira._site_url == "https://right.atlassian.net"

    @pytest.mark.asyncio
    async def test_cloud_id_not_in_resources_returns_none_and_warns(self):
        jira = _build_jira_with_client(
            _oauth_inner("https://api.atlassian.com/ex/jira/missing")
        )
        resources = [
            _resource("one", "https://one.atlassian.net"),
            _resource("two", "https://two.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ), patch("app.agents.actions.jira.jira.logger") as mock_logger:
            result = await jira._get_site_url()

        assert result is None
        assert jira._site_url is None
        mock_logger.warning.assert_called_once_with(
            "Jira _get_site_url: cloud_id %s not found in accessible resources (%s); "
            "refusing to fall back to a different site.",
            "missing", ["one", "two"],
        )

    @pytest.mark.asyncio
    async def test_regex_miss_single_site_falls_back_to_only_resource(self):
        jira = _build_jira_with_client(
            _oauth_inner("https://unexpected.example/path")  # no /ex/jira/<id>
        )
        resources = [_resource("only", "https://only.atlassian.net")]
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await jira._get_site_url()

        assert result == "https://only.atlassian.net"

    @pytest.mark.asyncio
    async def test_empty_gateway_multi_site_returns_first_resource(self):
        # Documents current legacy behaviour when cloud_id can't be extracted
        # and multiple sites are accessible. Tightening this is PR review item #2.
        jira = _build_jira_with_client(_oauth_inner(""))
        resources = [
            _resource("one", "https://one.atlassian.net"),
            _resource("two", "https://two.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await jira._get_site_url()

        assert result == "https://one.atlassian.net"

    @pytest.mark.asyncio
    async def test_api_token_path_uses_base_url_directly(self):
        jira = _build_jira_with_client(
            _api_token_inner("https://site.atlassian.net/")
        )
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(),
        ) as mock_resources:
            result = await jira._get_site_url()

        assert result == "https://site.atlassian.net"
        mock_resources.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_site_url_short_circuits(self):
        jira = _build_jira_with_client(_oauth_inner(""))
        jira._site_url = "https://cached.atlassian.net"
        with patch(
            "app.agents.actions.jira.jira.JiraClient.get_accessible_resources",
            new=AsyncMock(),
        ) as mock_resources:
            result = await jira._get_site_url()

        assert result == "https://cached.atlassian.net"
        mock_resources.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_logged_with_placeholder_format(self):
        # Simulate inner client blowing up when we call get_token.
        inner = MagicMock(spec=['get_token', 'get_base_url'])
        inner.get_token.side_effect = RuntimeError("boom")
        inner.get_base_url.return_value = "https://api.atlassian.com/ex/jira/x"
        jira = _build_jira_with_client(inner)

        with patch("app.agents.actions.jira.jira.logger") as mock_logger:
            result = await jira._get_site_url()

        assert result is None
        mock_logger.warning.assert_called_once()
        args, _ = mock_logger.warning.call_args
        assert args[0] == "Could not get site URL: %s"
        assert isinstance(args[1], RuntimeError)


# ===========================================================================
# Confluence
# ===========================================================================

class TestConfluenceGetSiteUrl:
    @pytest.mark.asyncio
    async def test_cloud_id_matches_returns_correct_site(self):
        conf = _build_confluence_with_client(
            _oauth_inner("https://api.atlassian.com/ex/confluence/abc-123/wiki/api/v2")
        )
        resources = [
            _resource("xyz", "https://wrong.atlassian.net"),
            _resource("abc-123", "https://right.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await conf._get_site_url()

        assert result == "https://right.atlassian.net"

    @pytest.mark.asyncio
    async def test_cloud_id_not_in_resources_returns_none_and_warns(self):
        conf = _build_confluence_with_client(
            _oauth_inner("https://api.atlassian.com/ex/confluence/missing/wiki/api/v2")
        )
        resources = [
            _resource("one", "https://one.atlassian.net"),
            _resource("two", "https://two.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ), patch("app.agents.actions.confluence.confluence.logger") as mock_logger:
            result = await conf._get_site_url()

        assert result is None
        mock_logger.warning.assert_called_once_with(
            "Confluence _get_site_url: cloud_id %s not found in accessible resources (%s); "
            "refusing to fall back to a different site.",
            "missing", ["one", "two"],
        )

    @pytest.mark.asyncio
    async def test_regex_miss_single_site_falls_back_to_only_resource(self):
        conf = _build_confluence_with_client(
            _oauth_inner("https://unexpected.example/path")
        )
        resources = [_resource("only", "https://only.atlassian.net")]
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await conf._get_site_url()

        assert result == "https://only.atlassian.net"

    @pytest.mark.asyncio
    async def test_empty_gateway_multi_site_returns_first_resource(self):
        conf = _build_confluence_with_client(_oauth_inner(""))
        resources = [
            _resource("one", "https://one.atlassian.net"),
            _resource("two", "https://two.atlassian.net"),
        ]
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(return_value=resources),
        ):
            result = await conf._get_site_url()

        assert result == "https://one.atlassian.net"

    @pytest.mark.asyncio
    async def test_api_token_path_strips_wiki_api_v2(self):
        conf = _build_confluence_with_client(
            _api_token_inner("https://site.atlassian.net/wiki/api/v2")
        )
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(),
        ) as mock_resources:
            result = await conf._get_site_url()

        assert result == "https://site.atlassian.net"
        mock_resources.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_site_url_short_circuits(self):
        conf = _build_confluence_with_client(_oauth_inner(""))
        conf._site_url = "https://cached.atlassian.net"
        with patch(
            "app.agents.actions.confluence.confluence.ConfluenceClient.get_accessible_resources",
            new=AsyncMock(),
        ) as mock_resources:
            result = await conf._get_site_url()

        assert result == "https://cached.atlassian.net"
        mock_resources.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_logged_with_placeholder_format(self):
        inner = MagicMock(spec=['get_token', 'get_base_url'])
        inner.get_token.side_effect = RuntimeError("boom")
        inner.get_base_url.return_value = "https://api.atlassian.com/ex/confluence/x/wiki/api/v2"
        conf = _build_confluence_with_client(inner)

        with patch("app.agents.actions.confluence.confluence.logger") as mock_logger:
            result = await conf._get_site_url()

        assert result is None
        mock_logger.warning.assert_called_once()
        args, _ = mock_logger.warning.call_args
        assert args[0] == "Could not get site URL: %s"
        assert isinstance(args[1], RuntimeError)
