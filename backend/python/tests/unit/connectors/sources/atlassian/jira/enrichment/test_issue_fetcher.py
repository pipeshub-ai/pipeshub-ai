"""Tests for Jira enrichment issue fetcher."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira.enrichment.issue_fetcher import (
    _build_jql_for_ids,
    _chunk_ids,
    _hostname_from_url,
    _is_atlassian_cloud_site_url,
    batch_fetch_issues,
    is_jira_cloud_base_url,
    resolve_is_cloud_api,
)


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


class TestResolveIsCloudApi:
    def test_atlassian_net_hostname_is_cloud(self):
        assert _is_atlassian_cloud_site_url("https://mycompany.atlassian.net") is True
        assert _is_atlassian_cloud_site_url("mycompany.atlassian.net") is True

    def test_substring_in_path_does_not_match(self):
        assert _is_atlassian_cloud_site_url("https://evil.com/.atlassian.net") is False

    def test_api_atlassian_ex_jira_proxy_url(self):
        assert is_jira_cloud_base_url(
            "https://api.atlassian.com/ex/jira/abc123/rest/api/3",
        ) is True

    def test_dc_connector_name_overrides_atlassian_net_url(self):
        assert resolve_is_cloud_api(Connectors.JIRA_DATA_CENTER, "https://x.atlassian.net") is False

    def test_cloud_connector_name_without_url(self):
        assert resolve_is_cloud_api(Connectors.JIRA, "") is True

    def test_invalid_connector_string_falls_back_to_url(self):
        assert resolve_is_cloud_api("not-a-connector", "https://x.atlassian.net") is True
        assert resolve_is_cloud_api("not-a-connector", "https://jira.example.com") is False

    def test_atlassian_net_url_without_connector_name(self):
        assert resolve_is_cloud_api(None, "https://team.atlassian.net") is True

    def test_ex_jira_proxy_url_without_connector_name(self):
        assert resolve_is_cloud_api(
            None,
            "https://api.atlassian.com/ex/jira/abc123/rest/api/3",
        ) is True

    def test_api_atlassian_without_ex_jira_path(self):
        assert is_jira_cloud_base_url("https://api.atlassian.com/other") is False
        assert resolve_is_cloud_api(None, "https://api.atlassian.com/other") is False

    def test_empty_urls(self):
        assert _hostname_from_url("") == ""
        assert is_jira_cloud_base_url("") is False


class TestChunkHelpers:
    def test_chunk_ids_empty(self):
        assert _chunk_ids([], 50) == []

    def test_build_jql_for_ids(self):
        assert _build_jql_for_ids(["1", "2", "3"]) == "id in (1, 2, 3)"


class TestBatchFetchIssuesErrors:
    @pytest.mark.asyncio
    async def test_empty_issue_ids_returns_empty(self):
        result = await batch_fetch_issues(
            MagicMock(),
            is_cloud=True,
            issue_ids=[],
            discovered_custom_ids={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_search_exception_is_skipped(self):
        data_source = MagicMock()
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(
            side_effect=RuntimeError("network down"),
        )

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["100"],
            discovered_custom_ids={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_non_ok_http_is_skipped(self):
        data_source = MagicMock()
        response = MagicMock()
        response.status = 500
        response.text.return_value = "error"
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["100"],
            discovered_custom_ids={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_json_parse_failure_is_skipped(self):
        data_source = MagicMock()
        response = MagicMock()
        response.status = HttpStatusCode.OK.value
        response.json.side_effect = ValueError("bad json")
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["100"],
            discovered_custom_ids={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_invalid_payload_is_skipped(self):
        data_source = MagicMock()
        response = MagicMock()
        response.status = HttpStatusCode.OK.value
        response.json.return_value = {"not_issues": []}
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["100"],
            discovered_custom_ids={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_issues_without_id_are_skipped(self):
        data_source = MagicMock()
        response = MagicMock()
        response.status = HttpStatusCode.OK.value
        response.json.return_value = {
            "issues": [{"fields": {}}, {"id": "200", "fields": {"labels": ["x"]}}],
        }
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=response)

        result = await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=["200"],
            discovered_custom_ids={},
        )
        assert result == {"200": {"id": "200", "fields": {"labels": ["x"]}}}

    @pytest.mark.asyncio
    async def test_chunks_large_id_lists(self):
        ids = [str(i) for i in range(55)]
        data_source = MagicMock()
        data_source.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_ok_response([]),
        )

        await batch_fetch_issues(
            data_source,
            is_cloud=True,
            issue_ids=ids,
            discovered_custom_ids={},
        )

        assert data_source.search_and_reconsile_issues_using_jql_post.await_count == 2
