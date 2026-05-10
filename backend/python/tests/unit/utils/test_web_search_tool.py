"""Tests for app.utils.web_search_tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.web_search_tool import (
    WebSearchArgs,
    _extract_ddg_url,
    _search_with_duckduckgo_sync,
    _search_with_exa,
    _search_with_serper,
    _search_with_tavily,
    create_web_search_tool,
)


def _langchain_core_is_stub_module() -> bool:
    """conftest may register a MagicMock for langchain_core when the package is absent."""
    import langchain_core

    return isinstance(langchain_core, MagicMock)


def _patch_async_httpx_client(inner: MagicMock):
    """Patch httpx.AsyncClient so ``async with`` yields ``inner``."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.utils.web_search_tool.httpx.AsyncClient", return_value=cm)


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
# _search_with_duckduckgo_sync
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

        # fetch_url is imported locally inside _search_with_duckduckgo_sync, patch at source
        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo_sync("test query", {})

        assert len(results) == 2
        assert results[0]["title"] == "Example"
        assert results[0]["link"] == "https://example.com"
        assert results[0]["snippet"] == "A great site"

    def test_fetch_error_returns_empty_list(self) -> None:
        from app.utils.url_fetcher import FetchError

        with patch("app.utils.url_fetcher.fetch_url", side_effect=FetchError("timeout")):
            results = _search_with_duckduckgo_sync("test", {})

        assert results == []

    def test_non_200_status_returns_empty_list(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = ""

        with patch("app.utils.url_fetcher.fetch_url", return_value=mock_resp):
            results = _search_with_duckduckgo_sync("test", {})

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
            results = _search_with_duckduckgo_sync("test", {})

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
            results = _search_with_duckduckgo_sync("test", {})

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
            results = _search_with_duckduckgo_sync("test", {})

        assert len(results) == 1
        assert results[0]["snippet"] == ""


# ---------------------------------------------------------------------------
# _search_with_serper
# ---------------------------------------------------------------------------


class TestSearchWithSerper:
    async def test_raises_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            await _search_with_serper("test", {})

    async def test_returns_formatted_results(self) -> None:
        api_response = {
            "organic": [
                {"title": "Result 1", "link": "https://r1.com", "snippet": "S1"},
                {"title": "Result 2", "link": "https://r2.com", "snippet": "S2"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_serper("test", {"apiKey": "key123"})

        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[0]["link"] == "https://r1.com"
        assert results[0]["snippet"] == "S1"

    async def test_limits_to_10_results(self) -> None:
        api_response = {
            "organic": [
                {"title": f"R{i}", "link": f"https://r{i}.com", "snippet": f"S{i}"}
                for i in range(15)
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_serper("test", {"apiKey": "key"})

        assert len(results) == 10

    async def test_empty_organic_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_serper("test", {"apiKey": "key"})

        assert results == []


# ---------------------------------------------------------------------------
# _search_with_tavily
# ---------------------------------------------------------------------------


class TestSearchWithTavily:
    async def test_raises_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            await _search_with_tavily("test", {})

    async def test_returns_formatted_results(self) -> None:
        api_response = {
            "results": [
                {"title": "Tavily R1", "url": "https://t1.com", "content": "Content 1"},
                {"title": "Tavily R2", "url": "https://t2.com", "content": "Content 2"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_tavily("test", {"apiKey": "key123"})

        assert len(results) == 2
        assert results[0]["title"] == "Tavily R1"
        assert results[0]["link"] == "https://t1.com"
        assert results[0]["snippet"] == "Content 1"

    async def test_empty_results_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_tavily("test", {"apiKey": "key"})

        assert results == []


# ---------------------------------------------------------------------------
# _search_with_exa
# ---------------------------------------------------------------------------


class TestSearchWithExa:
    async def test_raises_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            await _search_with_exa("test", {})

    async def test_returns_formatted_results(self) -> None:
        api_response = {
            "results": [
                {"title": "Exa R1", "url": "https://e1.com", "text": "Body 1"},
                {"title": "Exa R2", "url": "https://e2.com", "highlights": ["H2a", "H2b"]},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_exa("test", {"apiKey": "key123"})

        assert len(results) == 2
        assert results[0]["title"] == "Exa R1"
        assert results[0]["link"] == "https://e1.com"
        assert results[0]["snippet"] == "Body 1"
        assert results[1]["snippet"] == "H2a H2b"

    async def test_snippet_falls_back_to_summary(self) -> None:
        api_response = {
            "results": [
                {"title": "S", "url": "https://s.com", "summary": "Sum text"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = api_response

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_exa("test", {"apiKey": "key"})

        assert results[0]["snippet"] == "Sum text"

    async def test_empty_results_returns_empty(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        inner = MagicMock()
        inner.post = AsyncMock(return_value=mock_response)

        with _patch_async_httpx_client(inner):
            results = await _search_with_exa("test", {"apiKey": "key"})

        assert results == []


# ---------------------------------------------------------------------------
# create_web_search_tool
# ---------------------------------------------------------------------------


class TestCreateWebSearchTool:
    @pytest.fixture(autouse=True)
    def _skip_when_langchain_stubbed(self) -> None:
        if _langchain_core_is_stub_module():
            pytest.skip("langchain_core is stubbed (optional import mock); tool factory tests require the real package")

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

    def test_creates_tool_for_exa(self) -> None:
        tool = create_web_search_tool(config={"provider": "exa", "configuration": {"apiKey": "k"}})
        assert tool.name == "web_search"

    async def test_unknown_provider_falls_back_to_duckduckgo(self) -> None:
        with patch(
            "app.utils.web_search_tool._search_with_duckduckgo",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_ddg:
            tool = create_web_search_tool(config={"provider": "unknown", "configuration": {}})
            await tool.ainvoke({"query": "test"})
            mock_ddg.assert_called_once()

    async def test_successful_search_returns_results(self) -> None:
        results = [{"title": "T", "link": "https://t.com", "snippet": "S"}]
        with patch(
            "app.utils.web_search_tool._search_with_duckduckgo",
            new_callable=AsyncMock,
            return_value=results,
        ):
            tool = create_web_search_tool()
            output = await tool.ainvoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is True
        assert data["result_type"] == "web_search"
        assert len(data["web_results"]) == 1
        assert data["query"] == "test"

    async def test_successful_exa_search_returns_results_via_ainvoke(self) -> None:
        """End-to-end happy path: Exa provider binding + JSON payload from tool.ainvoke."""
        results = [{"title": "Exa T", "link": "https://exa.example", "snippet": "Exa snippet"}]
        with patch(
            "app.utils.web_search_tool._search_with_exa",
            new_callable=AsyncMock,
            return_value=results,
        ) as mock_exa:
            tool = create_web_search_tool(
                config={"provider": "exa", "configuration": {"apiKey": "k"}},
            )
            output = await tool.ainvoke({"query": "exa query"})

        mock_exa.assert_awaited_once_with("exa query", {"apiKey": "k"})
        data = json.loads(output)
        assert data["ok"] is True
        assert data["result_type"] == "web_search"
        assert len(data["web_results"]) == 1
        assert data["query"] == "exa query"
        assert data["web_results"][0]["title"] == "Exa T"

    async def test_failed_search_returns_error_after_retries(self) -> None:
        with patch(
            "app.utils.web_search_tool._search_with_duckduckgo",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fail"),
        ), patch("app.utils.web_search_tool.asyncio.sleep", new_callable=AsyncMock):
            tool = create_web_search_tool()
            output = await tool.ainvoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is False
        assert "fail" in data["error"]

    async def test_retry_on_failure_then_success(self) -> None:
        call_count = 0

        async def flaky_search(q: str, c: dict) -> list:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("temporary failure")
            return [{"title": "T", "link": "https://t.com", "snippet": "S"}]

        with patch("app.utils.web_search_tool._search_with_duckduckgo", side_effect=flaky_search), \
             patch("app.utils.web_search_tool.asyncio.sleep", new_callable=AsyncMock):
            tool = create_web_search_tool()
            output = await tool.ainvoke({"query": "test"})

        data = json.loads(output)
        assert data["ok"] is True
        assert call_count == 2

    def test_tool_has_correct_args_schema(self) -> None:
        tool = create_web_search_tool()
        # Tool should accept 'query' argument
        args = tool.args_schema(query="hello")
        assert args.query == "hello"
