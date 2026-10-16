"""Each search provider against a fake network: ``httpx.MockTransport`` for the APIs, a stubbed
``fetch_public`` for DuckDuckGo and a stubbed library for ddgs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from app.web.deadline import Deadline
from app.web.errors import (
    AuthFailed,
    Blocked,
    NotConfigured,
    QuotaExceeded,
    RateLimited,
    TimedOut,
    UnknownProvider,
    Unreachable,
    UpstreamError,
)
from app.web.fetch.http import FetchedBody
from app.web.health import provider_name
from app.web.search import PROVIDERS, spec_for
from app.web.search.ddgs_search import DdgsSearch
from app.web.search.duckduckgo_html import (
    MAX_RESULTS,
    DuckDuckGoHtml,
    parse_results_page,
    unwrap_result_link,
)
from app.web.search.exa import ExaSearch
from app.web.search.serper import SerperSearch
from app.web.search.tavily import TavilySearch

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

Handler = Callable[[httpx.Request], httpx.Response]


def deadline(seconds: float = 8.0) -> Deadline:
    return Deadline(expires_at=seconds, clock=lambda: 0.0)


def client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def respond(status: int, body: object = None, headers: dict[str, str] | None = None) -> Handler:
    return lambda request: httpx.Response(status, json=body if body is not None else {}, headers=headers)


def ddg_page(items: list[tuple[str, str, str]]) -> str:
    rows = "".join(
        f'<div class="web-result"><a class="result__a" href="{href}">{title}</a>'
        f'<span class="result__snippet">{snippet}</span></div>'
        for href, title, snippet in items
    )
    return f"<html><body>{rows}</body></html>"


DDG_BOT_CHECK = (
    '<html><body><div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>'
    '<form id="challenge-form"></form></body></html>'
)
DDG_NO_RESULTS = '<html><body><div class="no-results">No results.</div></body></html>'


def ddg_body(status: int, html: str) -> FetchedBody:
    return FetchedBody(url="https://html.duckduckgo.com/html/", status=status, content_type="text/html", content=html.encode())


def stub_ddg(body: FetchedBody | Exception) -> AbstractContextManager[AsyncMock]:
    mock = AsyncMock(side_effect=body) if isinstance(body, Exception) else AsyncMock(return_value=body)
    return patch("app.web.search.duckduckgo_html.fetch_public", mock)


# ---------------------------------------------------------------------------
# DuckDuckGo
# ---------------------------------------------------------------------------


class TestUnwrapResultLink:
    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            ("", ""),
            ("https://example.com/page", "https://example.com/page"),
            ("//example.com/page", "https://example.com/page"),
            (f"//duckduckgo.com/l/?uddg={quote('https://real-site.com/a', safe='')}&rut=abc", "https://real-site.com/a"),
            (f"https://duckduckgo.com/l/?uddg={quote('https://another.org/', safe='')}", "https://another.org/"),
            ("relative/path", "relative/path"),
        ],
    )
    def test_unwraps(self, href: str, expected: str) -> None:
        assert unwrap_result_link(href) == expected


class TestParseResultsPage:
    def test_parses_results(self) -> None:
        results = parse_results_page(ddg_page([("https://example.com", "Example", "A great site"), ("https://other.com", "Other", "")]), "duckduckgo")
        assert [(r.url, r.title, r.snippet, r.source) for r in results] == [
            ("https://example.com", "Example", "A great site", "duckduckgo"),
            ("https://other.com", "Other", "", "duckduckgo"),
        ]

    def test_caps_results(self) -> None:
        page = ddg_page([(f"https://s{i}.com", f"Site {i}", "") for i in range(MAX_RESULTS + 5)])
        assert len(parse_results_page(page, "duckduckgo")) == MAX_RESULTS

    def test_skips_items_without_a_title_link(self) -> None:
        page = (
            '<html><body><div class="web-result"><span class="result__snippet">x</span></div>'
            '<div class="web-result"><a class="result__a" href="https://good.com">Good</a></div></body></html>'
        )
        assert [r.url for r in parse_results_page(page, "duckduckgo")] == ["https://good.com"]

    def test_bot_check_page_is_blocked(self) -> None:
        with pytest.raises(Blocked):
            parse_results_page(DDG_BOT_CHECK, "duckduckgo")

    def test_genuine_no_results_page_is_empty(self) -> None:
        assert parse_results_page(DDG_NO_RESULTS, "duckduckgo") == []

    def test_unrecognised_page_is_blocked_not_empty(self) -> None:
        with pytest.raises(Blocked):
            parse_results_page("<html><body><p>Something else</p></body></html>", "duckduckgo")


class TestDuckDuckGoHtml:
    async def test_returns_results(self) -> None:
        with stub_ddg(ddg_body(200, ddg_page([("https://example.com", "Example", "s")]))) as fetch:
            results = await DuckDuckGoHtml().search("python", deadline())
        assert results[0].url == "https://example.com"
        assert "q=python" in fetch.call_args.args[0]

    async def test_202_anomaly_is_blocked(self) -> None:  # WP-0-SRCH-01
        with stub_ddg(ddg_body(202, DDG_BOT_CHECK)), pytest.raises(Blocked) as info:
            await DuckDuckGoHtml().search("q", deadline())
        assert info.value.to_tool_payload()["error_type"] == "blocked"

    async def test_200_captcha_is_blocked(self) -> None:  # WP-0-SRCH-02
        with stub_ddg(ddg_body(200, DDG_BOT_CHECK)), pytest.raises(Blocked):
            await DuckDuckGoHtml().search("q", deadline())

    async def test_server_error_is_retryable(self) -> None:
        with stub_ddg(ddg_body(503, "")), pytest.raises(UpstreamError) as info:
            await DuckDuckGoHtml().search("q", deadline())
        assert info.value.retryable

    async def test_fetch_failure_propagates(self) -> None:
        with stub_ddg(Unreachable("down")), pytest.raises(Unreachable):
            await DuckDuckGoHtml().search("q", deadline())


# ---------------------------------------------------------------------------
# ddgs
# ---------------------------------------------------------------------------


class TestDdgs:
    @staticmethod
    def stub(result: list[dict] | Exception) -> AbstractContextManager[object]:
        side = result if isinstance(result, Exception) else None
        return patch.object(DdgsSearch, "_text", side_effect=side, return_value=None if side else result)

    async def test_maps_results(self) -> None:
        with self.stub([{"href": "https://a.com", "title": "A", "body": "about a"}, {"title": "no link"}]):
            results = await DdgsSearch().search("q", deadline())
        # The entry without a link is kept here and dropped by the service, like any other bad link.
        assert [(r.url, r.title, r.snippet, r.source) for r in results] == [("https://a.com", "A", "about a", "ddgs"), ("", "no link", "", "ddgs")]

    async def test_no_results_is_empty(self) -> None:
        with self.stub(DDGSException("No results found.")):
            assert await DdgsSearch().search("q", deadline()) == []

    @pytest.mark.parametrize(
        ("raised", "error"),
        [(RatelimitException("slow"), RateLimited), (TimeoutException("t"), TimedOut), (DDGSException("engines failed"), UpstreamError)],
    )
    async def test_failures(self, raised: Exception, error: type) -> None:
        with self.stub(raised), pytest.raises(error):
            await DdgsSearch().search("q", deadline())

    async def test_expired_deadline_never_starts(self) -> None:
        with self.stub([]) as text, pytest.raises(TimedOut):
            await DdgsSearch().search("q", deadline(0))
        text.assert_not_called()


# ---------------------------------------------------------------------------
# API providers
# ---------------------------------------------------------------------------


class TestApiProviders:
    async def test_serper(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"organic": [{"title": "T", "link": "https://a.com", "snippet": "S", "date": "2 days ago"}]})

        results = await SerperSearch({"apiKey": "k"}, client(handler)).search("q", deadline())
        assert [(r.url, r.title, r.snippet, r.published, r.source) for r in results] == [("https://a.com", "T", "S", "2 days ago", "serper")]
        assert seen[0].headers["X-API-KEY"] == "k"

    async def test_tavily_uses_basic_depth(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a.com", "content": "C"}]})

        results = await TavilySearch({"apiKey": "k"}, client(handler)).search("q", deadline())
        assert seen[0]["search_depth"] == "basic"
        assert results[0].snippet == "C"

    async def test_exa_skips_summaries_and_prefers_highlights(self) -> None:
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [
                {"title": "A", "url": "https://a.com", "highlights": ["h1", "h2"], "text": "full", "publishedDate": "2026-01-02"},
                {"title": "B", "url": "https://b.com", "text": " body "},
            ]})

        results = await ExaSearch({"apiKey": "k"}, client(handler)).search("q", deadline())
        assert "summary" not in seen[0]["contents"]
        assert [(r.snippet, r.published) for r in results] == [("h1 h2", "2026-01-02"), ("body", None)]

    @pytest.mark.parametrize("provider", [SerperSearch, TavilySearch, ExaSearch])
    async def test_missing_key_never_sends_a_request(self, provider: type) -> None:
        handler = AsyncMock()
        with pytest.raises(NotConfigured):
            await provider({}, client(handler)).search("q", deadline())
        handler.assert_not_called()

    @pytest.mark.parametrize(
        ("status", "error", "retryable"),
        [(401, AuthFailed, False), (403, AuthFailed, False), (402, QuotaExceeded, False),
         (429, RateLimited, True), (500, UpstreamError, True), (400, UpstreamError, False)],
    )
    async def test_status_maps_to_typed_error(self, status: int, error: type, retryable: bool) -> None:
        with pytest.raises(error) as info:
            await SerperSearch({"apiKey": "k"}, client(respond(status, {"error": "x"}))).search("q", deadline())
        assert info.value.retryable is retryable
        assert info.value.status == status
        assert info.value.source == "serper"

    async def test_retry_after_is_read(self) -> None:
        with pytest.raises(RateLimited) as info:
            await TavilySearch({"apiKey": "k"}, client(respond(429, {}, {"Retry-After": "2"}))).search("q", deadline())
        assert info.value.retry_after_s == 2.0

    async def test_non_json_body(self) -> None:
        with pytest.raises(UpstreamError):
            await ExaSearch({"apiKey": "k"}, client(lambda r: httpx.Response(200, text="<html>"))).search("q", deadline())

    async def test_timeout_and_connection_errors(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(TimedOut):
            await SerperSearch({"apiKey": "k"}, client(timeout)).search("q", deadline())
        with pytest.raises(Unreachable):
            await SerperSearch({"apiKey": "k"}, client(refused)).search("q", deadline())

    async def test_expired_deadline_sends_nothing(self) -> None:
        handler = AsyncMock()
        with pytest.raises(TimedOut):
            await SerperSearch({"apiKey": "k"}, client(handler)).search("q", deadline(0))
        handler.assert_not_called()

    def test_cooldown_key_is_per_key_and_hides_it(self) -> None:
        http = client(respond(200))
        one, other = SerperSearch({"apiKey": "secret-1"}, http), SerperSearch({"apiKey": "secret-2"}, http)
        assert one.cooldown_key != other.cooldown_key
        assert one.cooldown_key == SerperSearch({"apiKey": "secret-1"}, http).cooldown_key
        assert "secret" not in one.cooldown_key


class TestRegistry:
    def test_every_stored_type_is_registered(self) -> None:
        assert {"duckduckgo", "serper", "tavily", "exa", "brave", "searxng", "firecrawl"} <= set(PROVIDERS)

    def test_unknown_type(self) -> None:
        with pytest.raises(UnknownProvider):
            spec_for("bing")

    def test_names(self) -> None:
        assert provider_name("serper") == "Serper"
        assert provider_name("crawl4ai") == "Crawl4AI"
        assert provider_name("bing") == "bing"
        assert provider_name(None) == "The web search provider"
