"""Tests for app.utils.web_search_tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.utils.web_search_tool import (
    WebSearchArgs,
    _extract_ddg_url,
    _search_with_duckduckgo,
    _search_with_serper,
    _search_with_tavily,
    create_web_search_tool,
)


# ---------------------------------------------------------------------------
# _extract_ddg_url
# ---------------------------------------------------------------------------


class TestExtractDdgUrl:
    def test_empty_string_returns_empty(self) -> None:
        assert _extract_ddg_url("") == ""

    def test_plain_http_url_returned_unchanged(self) -> None:
        url = "https://example.com/page"
        assert _extract_ddg_url(url) == url

    def test_double_slash_prefix_becomes_https(self) -> None:
        href = "//example.com/page"
        result = _extract_ddg_url(href)
        assert result == "https://example.com/page"

    def test_uddg_param_decoded(self) -> None:
        from urllib.parse import quote
        real_url = "https://real-site.com/article"
        encoded = quote(real_url, safe="")
        href = f"//duckduckgo.com/l/?uddg={encoded}&rut=abc"
        result = _extract_ddg_url(href)
        assert result == real_url

    def test_uddg_param_with_http_prefix(self) -> None:
        from urllib.parse import quote
        real_url = "https://another-site.org/"
        encoded = quote(real_url, safe="")
        href = f"https://duckduckgo.com/l/?uddg={encoded}"
        result = _extract_ddg_url(href)
        assert result == real_url

    def test_non_http_non_slash_returned_as_is(self) -> None:
        href = "relative/path"
        assert _extract_ddg_url(href) == href


# ---------------------------------------------------------------------------
# _search_with_duckduckgo
# ---------------------------------------------------------------------------


class TestSearchWithDuckDuckGo:
    def _make_html_response(self, results: list[dict]) -> str:
        items = ""
        for r in results:
            items += (
                f'<div class="web-result">'
                f'<a class="result__a" href="{r["href"]}">{r["title"]}</a>'
                f'<span class="result__snippet">{r["snippet"]}</span>'
                f'</div>'
            )
        return f"<html><body>{items}</body></html>"

    def test_returns_results_on_success(self) -> None:
        html = self._make_html_response([
            {"href": "https://example.com", "title": "Example", "snippet": "A great site"},
            {"href": "https://other.com", "title": "Other", "snippet": "Another site"},
        ])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        # fetch_url is imported locally inside _search_with_duckduckgo, patch at source
        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo("test query", {})

        assert len(results) == 2
        assert results[0]["title"] == "Example"
        assert results[0]["link"] == "https://example.com"
        assert results[0]["snippet"] == "A great site"

    def test_fetch_error_returns_empty_list(self) -> None:
        from app.utils.url_fetcher import FetchError

        with patch("app.utils.url_fetcher.fetch_url", side_effect=FetchError("timeout")):
            results = _search_with_duckduckgo("test", {})

        assert results == []

    def test_non_200_status_returns_empty_list(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = ""

        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo("test", {})

        assert results == []

    def test_result_without_title_skipped(self) -> None:
        html = (
            '<html><body>'
            '<div class="web-result">'
            '<span class="result__snippet">no title anchor here</span>'
            '</div>'
            '<div class="web-result">'
            '<a class="result__a" href="https://good.com">Good</a>'
            '</div>'
            '</body></html>'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo("test", {})

        assert len(results) == 1
        assert results[0]["title"] == "Good"

    def test_limits_to_10_results(self) -> None:
        items = [
            {"href": f"https://site{i}.com", "title": f"Site {i}", "snippet": f"Snippet {i}"}
            for i in range(15)
        ]
        html = self._make_html_response(items)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo("test", {})

        assert len(results) == 10

    def test_result_without_snippet(self) -> None:
        html = (
            '<html><body>'
            '<div class="web-result">'
            '<a class="result__a" href="https://example.com">Example</a>'
            '</div>'
            '</body></html>'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo("test", {})

        assert len(results) == 1
        assert results[0]["snippet"] == ""


# ---------------------------------------------------------------------------
# _search_with_serper
# ---------------------------------------------------------------------------


class TestSearchWithSerper:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            _search_with_serper("test", {})

    def test_returns_formatted_results(self) -> None:
        api_response = {
            "organic": [
                {"title": "Result 1", "link": "https://r1.com", "snippet": "S1"},
                {"title": "Result 2", "link": "https://r2.com", "snippet": "S2"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("app.utils.web_search_tool.httpx.Client", return_value=mock_client):
            results = _search_with_serper("test", {"apiKey": "key123"})

        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[0]["link"] == "https://r1.com"
        assert results[0]["snippet"] == "S1"

    def test_limits_to_10_results(self) -> None:
        api_response = {
            "organic": [
                {"title": f"R{i}", "link": f"https://r{i}.com", "snippet": f"S{i}"}
                for i in range(15)
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("app.utils.web_search_tool.httpx.Client", return_value=mock_client):
            results = _search_with_serper("test", {"apiKey": "key"})

        assert len(results) == 10

    def test_empty_organic_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("app.utils.web_search_tool.httpx.Client", return_value=mock_client):
            results = _search_with_serper("test", {"apiKey": "key"})

        assert results == []


# ---------------------------------------------------------------------------
# _search_with_tavily
# ---------------------------------------------------------------------------


class TestSearchWithTavily:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            _search_with_tavily("test", {})

    def test_returns_formatted_results(self) -> None:
        api_response = {
            "results": [
                {"title": "Tavily R1", "url": "https://t1.com", "content": "Content 1"},
                {"title": "Tavily R2", "url": "https://t2.com", "content": "Content 2"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("app.utils.web_search_tool.httpx.Client", return_value=mock_client):
            results = _search_with_tavily("test", {"apiKey": "key123"})

        assert len(results) == 2
        assert results[0]["title"] == "Tavily R1"
        assert results[0]["link"] == "https://t1.com"
        assert results[0]["snippet"] == "Content 1"

    def test_empty_results_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("app.utils.web_search_tool.httpx.Client", return_value=mock_client):
            results = _search_with_tavily("test", {"apiKey": "key"})

        assert results == []


# ---------------------------------------------------------------------------
# create_web_search_tool
# ---------------------------------------------------------------------------


class TestCreateWebSearchTool:
    def _make_success_search(self, results: list | None = None) -> MagicMock:
        if results is None:
            results = [{"title": "T", "link": "https://t.com", "snippet": "S"}]
        return MagicMock(return_value=results)

    def test_defaults_to_duckduckgo(self) -> None:
        tool = create_web_search_tool()
        assert tool.name == "web_search"

    def test_no_config_uses_duckduckgo(self) -> None:
        tool = create_web_search_tool(config=None)
        assert tool.name == "web_search"

    def test_creates_tool_for_serper(self) -> None:
        tool = create_web_search_tool(config={"provider": "serper", "configuration": {"apiKey": "k"}})
        assert tool.name == "web_search"

    def test_creates_tool_for_tavily(self) -> None:
        tool = create_web_search_tool(config={"provider": "tavily", "configuration": {"apiKey": "k"}})
        assert tool.name == "web_search"

    def test_unknown_provider_falls_back_to_duckduckgo(self) -> None:
        with patch("app.utils.web_search_tool._search_with_duckduckgo", return_value=[]) as mock_ddg:
            tool = create_web_search_tool(config={"provider": "unknown", "configuration": {}})
            tool.invoke({"query": "test"})
            mock_ddg.assert_called_once()

    def test_successful_search_returns_results(self) -> None:
        results = [{"title": "T", "link": "https://t.com", "snippet": "S"}]
        with patch("app.utils.web_search_tool._search_with_duckduckgo", return_value=results):
            tool = create_web_search_tool()
            output = tool.invoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is True
        assert data["result_type"] == "web_search"
        assert len(data["web_results"]) == 1
        assert data["query"] == "test"

    def test_failed_search_returns_error_after_retries(self) -> None:
        with patch("app.utils.web_search_tool._search_with_duckduckgo", side_effect=RuntimeError("fail")), \
             patch("app.utils.web_search_tool.time.sleep"):
            tool = create_web_search_tool()
            output = tool.invoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is False
        assert "fail" in data["error"]

    def test_retry_on_failure_then_success(self) -> None:
        call_count = 0

        def flaky_search(q, c):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("temporary failure")
            return [{"title": "T", "link": "https://t.com", "snippet": "S"}]

        with patch("app.utils.web_search_tool._search_with_duckduckgo", side_effect=flaky_search), \
             patch("app.utils.web_search_tool.time.sleep"):
            tool = create_web_search_tool()
            output = tool.invoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is True
        assert call_count == 2

    def test_tool_has_correct_args_schema(self) -> None:
        tool = create_web_search_tool()
        # Tool should accept 'query' argument
        args = tool.args_schema(query="hello")
        assert args.query == "hello"
