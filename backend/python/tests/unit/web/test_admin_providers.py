"""Providers an admin adds: SearXNG, Brave, Firecrawl (search and pages) and a Crawl4AI server."""

import json
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.utils.url_fetcher import FetchError, PublicTarget
from app.web.container import fetchers, search_providers
from app.web.deadline import Deadline
from app.web.errors import (
    AccessDenied,
    AuthFailed,
    Blocked,
    NotConfigured,
    NoText,
    UnknownProvider,
    UnsafeUrl,
    UpstreamError,
)
from app.web.fetch.crawl4ai_server import Crawl4AIServerFetcher
from app.web.fetch.firecrawl import FirecrawlFetcher
from app.web.fetch.remote import MAX_BLOCK_CHARS, markdown_blocks, markdown_page
from app.web.health import check_provider
from app.web.search.brave import BraveSearch
from app.web.search.firecrawl import FirecrawlSearch
from app.web.search.searxng import SearxngSearch

Handler = Callable[[httpx.Request], httpx.Response]


class Server:
    """Answers every request a provider sends to its own server, and records them."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.endpoints: list[str] = []

    def client(self, endpoint: str, timeout: float) -> httpx.AsyncClient:
        self.endpoints.append(endpoint)

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(record))


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Server:
    fake = Server(lambda request: httpx.Response(500))
    monkeypatch.setattr("app.web.endpoint.guarded_async_client", fake.client)
    return fake


@pytest.fixture
def public_hosts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Hosts resolve as public, except any under ``.internal``; checked URLs are recorded."""
    checked: list[str] = []

    def resolve(url: str) -> PublicTarget:
        checked.append(url)
        if ".internal" in url:
            raise FetchError("private address")
        return PublicTarget("https", "x", 443, ())

    monkeypatch.setattr("app.web.fetch.http.resolve_public_http_target", resolve)
    return checked


def deadline() -> Deadline:
    return Deadline(expires_at=10, clock=lambda: 0.0)


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


class TestSearxng:
    async def test_searches_its_server_as_json(self, server: Server) -> None:
        server.handler = lambda r: httpx.Response(200, json={"results": [
            {"url": "https://a.example/", "title": "A", "content": "about", "publishedDate": "2026-01-02"}, "junk",
        ]})
        results = await SearxngSearch({"baseUrl": "http://searxng:8080/"}, None).search("q", deadline())
        assert [(r.url, r.snippet, r.published, r.source) for r in results] == [("https://a.example/", "about", "2026-01-02", "searxng")]
        assert str(server.requests[0].url) == "http://searxng:8080/search?q=q&format=json"
        assert server.endpoints == ["http://searxng:8080"]

    async def test_json_output_turned_off(self, server: Server) -> None:  # WP-3-PROV-02
        server.handler = lambda r: httpx.Response(403)
        with pytest.raises(NotConfigured) as info:
            await SearxngSearch({"baseUrl": "http://searxng:8080"}, None).search("q", deadline())
        assert "settings.yml" in info.value.message

    async def test_a_token_is_sent_when_set(self, server: Server) -> None:
        server.handler = lambda r: httpx.Response(200, json={"results": []})
        await SearxngSearch({"baseUrl": "https://s.example", "apiKey": "t"}, None).search("q", deadline())
        assert server.requests[0].headers["Authorization"] == "Bearer t"

    async def test_needs_an_address(self, server: Server) -> None:
        with pytest.raises(NotConfigured):
            await SearxngSearch({}, None).search("q", deadline())
        assert server.requests == []


class TestBrave:
    async def test_search(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"web": {"results": [{"url": "https://a.example/", "title": "A", "description": "d", "page_age": "2026-02-03"}]}})

        results = await BraveSearch({"apiKey": "k"}, httpx.AsyncClient(transport=httpx.MockTransport(handler))).search("q", deadline())
        assert [(r.url, r.snippet, r.published) for r in results] == [("https://a.example/", "d", "2026-02-03")]
        assert seen[0].headers["X-Subscription-Token"] == "k"
        assert seen[0].method == "GET"

    async def test_bad_key(self) -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
        with pytest.raises(AuthFailed):
            await BraveSearch({"apiKey": "k"}, http).search("q", deadline())


class TestFirecrawlSearch:
    async def test_cloud_by_default(self, server: Server) -> None:
        server.handler = lambda r: httpx.Response(200, json={"success": True, "data": {"web": [{"url": "https://a.example/", "title": "A", "description": "d"}]}})
        results = await FirecrawlSearch({"apiKey": "fc-key"}, None).search("q", deadline())
        assert results[0].snippet == "d"
        assert str(server.requests[0].url) == "https://api.firecrawl.dev/v2/search"
        assert server.requests[0].headers["Authorization"] == "Bearer fc-key"
        assert body(server.requests[0]) == {"query": "q", "limit": 10}

    async def test_self_hosted_v1_list(self, server: Server) -> None:
        server.handler = lambda r: httpx.Response(200, json={"success": True, "data": [{"url": "https://a.example/", "title": "A"}]})
        results = await FirecrawlSearch({"baseUrl": "http://firecrawl:3002"}, None).search("q", deadline())
        assert results[0].url == "https://a.example/"
        assert "Authorization" not in server.requests[0].headers


class TestFirecrawlFetcher:
    def page(self, **metadata: object) -> Handler:
        return lambda r: httpx.Response(200, json={"success": True, "data": {
            "markdown": "# Heading\n\nFirst paragraph.\n\nSecond paragraph.", "metadata": metadata,
        }})

    async def test_reads_the_page(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = self.page(title="Real title", sourceURL="https://a.example/doc", statusCode=200)
        page = await FirecrawlFetcher({"apiKey": "k"}).fetch("https://a.example/doc", deadline())
        assert page.title == "Real title"
        assert page.fetched_by == "firecrawl"
        assert "Second paragraph." in page.blocks[0].content
        assert body(server.requests[0])["formats"] == ["markdown"]

    async def test_an_internal_url_is_never_sent(self, server: Server, public_hosts: list[str]) -> None:  # WP-3-FETCH-04
        with pytest.raises(UnsafeUrl):
            await FirecrawlFetcher({"apiKey": "k"}).fetch("http://db.internal/", deadline())
        assert server.requests == []

    async def test_landing_on_an_internal_address_is_refused(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = self.page(url="http://admin.internal/", statusCode=200)
        with pytest.raises(UnsafeUrl):
            await FirecrawlFetcher({"apiKey": "k"}).fetch("https://a.example/go", deadline())

    async def test_landing_on_a_refused_page(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = self.page(url="https://elsewhere.example/", statusCode=200)
        refuse = AsyncMock(return_value=False)
        with pytest.raises(AccessDenied):
            await FirecrawlFetcher({"apiKey": "k"}).fetch("https://a.example/go", deadline(), allow_hop=refuse)
        refuse.assert_awaited_once_with("https://elsewhere.example/")

    async def test_the_sites_status_counts(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = self.page(statusCode=403)
        with pytest.raises(Blocked):
            await FirecrawlFetcher({"apiKey": "k"}).fetch("https://a.example/", deadline())


def crawled(version: str = "0.9.4", **result: object) -> Handler:
    """A Crawl4AI server: ``/health`` reports ``version``, ``/crawl`` answers one ``CrawlResult``."""
    page = {"url": "https://a.example/", "success": True, "status_code": 200,
            "markdown": {"raw_markdown": "## The Title\n\nAll of it.", "fit_markdown": "## The Title\n\nBody text."},
            "metadata": {}, **result}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": version})
        return httpx.Response(200, json={"success": True, "results": [page]})

    return answer


@pytest.fixture(autouse=True)
def unverified_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.fetch.crawl4ai_server._VERIFIED", set())


class TestCrawl4AI:
    async def test_reads_the_page_and_titles_it_from_its_heading(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled()
        page = await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235", "apiKey": "t"}).fetch("https://a.example/", deadline())
        assert page.title == "The Title"
        assert "Body text." in page.blocks[0].content
        assert [r.url.path for r in server.requests] == ["/health", "/crawl"]
        assert body(server.requests[1])["urls"] == ["https://a.example/"]
        assert server.requests[1].headers["Authorization"] == "Bearer t"

    async def test_an_unsafe_version_is_never_sent_a_page(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(version="0.9.3")
        with pytest.raises(NotConfigured):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/", deadline())
        assert [r.url.path for r in server.requests] == ["/health"]

    async def test_the_version_is_checked_once_per_server(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled()
        reader = Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"})
        await reader.fetch("https://a.example/", deadline())
        await reader.fetch("https://a.example/", deadline())
        assert [r.url.path for r in server.requests] == ["/health", "/crawl", "/crawl"]

    async def test_a_redirect_is_judged_by_the_page_it_landed_on(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(status_code=302, redirected_status_code=200, redirected_url="https://a.example/moved")
        page = await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/", deadline())
        assert page.url == "https://a.example/moved"

    async def test_a_redirect_onto_an_internal_address_is_unsafe(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(status_code=302, redirected_status_code=200, redirected_url="http://metadata.internal/")
        with pytest.raises(UnsafeUrl):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/", deadline())

    async def test_the_page_title_and_landing_address_are_used(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(redirected_url="https://a.example/moved", metadata={"title": "Moved page"})
        page = await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/", deadline())
        assert (page.url, page.title) == ("https://a.example/moved", "Moved page")

    async def test_landing_on_an_internal_address_is_refused(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(redirected_url="http://metadata.internal/latest/")
        with pytest.raises(UnsafeUrl):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/go", deadline())

    async def test_landing_on_a_refused_page(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(redirected_url="https://elsewhere.example/")
        refuse = AsyncMock(return_value=False)
        with pytest.raises(AccessDenied):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch(
                "https://a.example/go", deadline(), allow_hop=refuse)
        refuse.assert_awaited_once_with("https://elsewhere.example/")

    async def test_the_sites_status_counts(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(status_code=403)
        with pytest.raises(Blocked):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/", deadline())

    async def test_a_failed_read(self, server: Server, public_hosts: list[str]) -> None:
        server.handler = crawled(success=False, error_message="navigation failed for https://a.example/?token=s3cret")
        with pytest.raises(UpstreamError) as info:
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("https://a.example/?token=s3cret", deadline())
        assert "could not read" in info.value.message
        assert "s3cret" not in info.value.message

    async def test_an_internal_url_is_never_sent(self, server: Server, public_hosts: list[str]) -> None:
        with pytest.raises(UnsafeUrl):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).fetch("http://redis.internal:6379/", deadline())
        assert server.requests == []

    @pytest.mark.parametrize("version", ["0.9.3", "0.8.8", "0.6.3", "", "unknown"])
    async def test_an_unsafe_version_fails_the_probe(self, server: Server, version: str) -> None:  # WP-3-FETCH-01
        server.handler = lambda r: httpx.Response(200, json={"status": "ok", "version": version})
        with pytest.raises(NotConfigured) as info:
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).probe(deadline())
        assert "0.9.4" in info.value.message
        assert info.value.reason == "server_version"

    @pytest.mark.parametrize("version", ["0.9.4", "0.10.0", "1.0.0"])
    async def test_a_safe_version_passes(self, server: Server, version: str) -> None:
        server.handler = lambda r: httpx.Response(200, json={"version": version})
        await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).probe(deadline())

    async def test_a_server_that_wants_a_token(self, server: Server) -> None:  # WP-3-FETCH-02
        server.handler = lambda r: httpx.Response(401)
        with pytest.raises(AuthFailed):
            await Crawl4AIServerFetcher({"baseUrl": "http://crawl4ai:11235"}).probe(deadline())


class TestMarkdown:
    def test_paragraphs_are_grouped_and_never_exceed_the_limit(self) -> None:
        markdown = "\n\n".join(["short paragraph"] * 3 + ["word " * 1000] + ["tail"])
        blocks = markdown_blocks(markdown)
        assert all(len(b.content) <= MAX_BLOCK_CHARS for b in blocks)
        assert blocks[0].content.startswith("short paragraph\n\nshort paragraph")
        assert blocks[-1].content.endswith("tail")
        assert "".join(b.content.replace("\n", "").replace(" ", "") for b in blocks) == markdown.replace("\n", "").replace(" ", "")

    def test_an_empty_page(self) -> None:
        with pytest.raises(NoText):
            markdown_page("https://a.example/", "  \n\n ", "", "crawl4ai")


class TestOrder:
    STORED = [
        {"provider": "crawl4ai", "configuration": {"baseUrl": "http://crawl4ai:11235"}},
        {"provider": "tavily", "configuration": {"apiKey": "t"}},
        {"provider": "firecrawl", "configuration": {"apiKey": "f"}},
        {"provider": "bing", "configuration": {}},
    ]

    def test_search_tries_the_chosen_then_the_other_searchers_then_the_free_ones(self) -> None:
        selection = {"provider": "serper", "configuration": {"apiKey": "s"}, "providers": self.STORED}
        assert [p.id for p in search_providers(selection)] == ["serper", "tavily", "firecrawl", "ddgs", "duckduckgo"]

    def test_with_no_chosen_provider_the_stored_searchers_still_come_first(self) -> None:
        selection = {"provider": "duckduckgo", "configuration": {}, "providers": self.STORED}
        assert [p.id for p in search_providers(selection)] == ["tavily", "firecrawl", "ddgs", "duckduckgo"]

    def test_firecrawl_chosen_searches_first(self) -> None:
        selection = {"provider": "firecrawl", "configuration": {"apiKey": "f"}, "providers": self.STORED}
        assert [p.id for p in search_providers(selection)] == ["firecrawl", "tavily", "ddgs", "duckduckgo"]

    def test_a_page_reader_chosen_leaves_search_to_the_others(self) -> None:
        selection = {"provider": "crawl4ai", "configuration": {"baseUrl": "http://crawl4ai:11235"}, "providers": self.STORED}
        assert [p.id for p in search_providers(selection)] == ["tavily", "firecrawl", "ddgs", "duckduckgo"]

    def test_pages_are_read_built_in_first_then_by_the_stored_readers(self) -> None:
        selection = {"provider": "serper", "configuration": {}, "providers": self.STORED}
        assert [f.id for f in fetchers(selection)] == ["builtin", "crawl4ai", "firecrawl"]

    def test_the_chosen_provider_can_also_read_pages(self) -> None:
        assert [f.id for f in fetchers({"provider": "firecrawl", "configuration": {"apiKey": "f"}})] == ["builtin", "firecrawl"]


class TestHealthCheck:
    async def test_a_page_reader_is_probed_then_reads_a_page(self) -> None:
        with patch.object(Crawl4AIServerFetcher, "probe", AsyncMock()) as probe, \
                patch.object(Crawl4AIServerFetcher, "fetch", AsyncMock()) as fetch:
            await check_provider("crawl4ai", {"baseUrl": "http://crawl4ai:11235"}, deadline())
        probe.assert_awaited_once()
        assert fetch.call_args.args[0] == "https://example.com/"

    async def test_a_searcher_runs_a_search(self) -> None:
        with patch.object(SearxngSearch, "search", AsyncMock(return_value=[])) as search:
            await check_provider("searxng", {"baseUrl": "http://searxng:8080"}, deadline())
        search.assert_awaited_once()

    async def test_unknown(self) -> None:
        with pytest.raises(UnknownProvider):
            await check_provider("bing", {}, deadline())
