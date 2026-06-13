"""Tests for Jira enrichment issue fetcher."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira.enrichment.issue_fetcher import batch_fetch_issues


def _ok_response(issues: list[dict]) -> MagicMock:
    response = MagicMock()
    response.status = HttpStatusCode.OK.value
    response.json.return_value = {"issues": issues}
    return response


class TestBatchFetchIssues:
    @pytest.mark.asyncio
    async def test_cloud_uses_search_jql_endpoint(self):
        data_source = MagicMock()
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_ok_response([{"id": "20086", "fields": {"labels": ["x"]}}])
        )

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["20086"],
            discovered_custom_ids={},
        )

        data_source.search_and_reconsile_issues_using_jql_post.assert_awaited_once()
        call_kwargs = data_source.search_and_reconsile_issues_using_jql_post.await_args.kwargs
        assert call_kwargs["jql"] == "id in (20086)"
        assert call_kwargs["maxResults"] == 1
        assert isinstance(call_kwargs["fields"], list)
        assert result == {"20086": {"id": "20086", "fields": {"labels": ["x"]}}}

    @pytest.mark.asyncio
    async def test_dc_uses_v2_search(self):
        data_source = MagicMock()
        data_source.search_issues_post_v2 = AsyncMock(
            return_value=_ok_response([{"id": "10024", "fields": {"project": {"name": "PA"}}}])
        )

        result = await batch_fetch_issues(
            data_source,
            is_cloud=False,
            issue_ids=["10024"],
            discovered_custom_ids={},
        )

        data_source.search_issues_post_v2.assert_awaited_once()
        assert "10024" in result
