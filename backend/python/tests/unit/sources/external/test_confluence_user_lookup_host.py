"""find_user_account_id_by_email only calls Jira for Atlassian Cloud hosts; a port must not defeat that."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.confluence.confluence import ConfluenceDataSource


def _source(base_url: str) -> ConfluenceDataSource:
    http = MagicMock()
    http.get_base_url.return_value = base_url
    http.execute = AsyncMock(return_value=None)
    client = MagicMock()
    client.get_client.return_value = http
    return ConfluenceDataSource(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("https://acme.atlassian.net/wiki/api/v2", "https://acme.atlassian.net/rest/api/3/user/search"),
        ("http://acme.atlassian.net:8101/wiki/api/v2", "http://acme.atlassian.net:8101/rest/api/3/user/search"),
        ("https://evil.example/acme.atlassian.net/wiki/api/v2", None),
        ("https://acme.atlassian.net.evil.example/wiki/api/v2", None),
    ],
)
async def test_jira_user_search_host_check(base_url: str, expected: str | None) -> None:
    source = _source(base_url)
    await source.find_user_account_id_by_email("a@b.com")
    calls = source._client.execute.await_args_list
    assert [c.args[0].url for c in calls] == ([expected] if expected else [])
