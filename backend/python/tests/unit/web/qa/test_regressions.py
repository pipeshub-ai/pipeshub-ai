"""One test per bug the QA pass found, so none comes back."""

import time

import httpx
import pytest
from web_behaviour_fakes import Page

from app.web.chain import Cooldowns
from app.web.deadline import Deadline
from app.web.errors import Blocked, RateLimited, TimedOut
from app.web.fetch.crawl4ai_server import Crawl4AIServerFetcher
from app.web.fetch.firecrawl import FirecrawlFetcher
from app.web.fetch.http import FetchedBody, check_page_status
from app.web.fetch.parsers import parse_body
from app.web.search.firecrawl import FirecrawlSearch
from app.web.search.searxng import SearxngSearch
from app.web.url import display_domain
from tests.unit.web.qa.lab import WebLab, results


class TestCooldownsArePerCredentialAndRole:
    def test_firecrawl_keys_differ_by_api_key_and_by_role(self) -> None:
        one, two = {"apiKey": "org-a"}, {"apiKey": "org-b"}
        assert FirecrawlSearch(one, None).cooldown_key != FirecrawlSearch(two, None).cooldown_key
        assert FirecrawlSearch(one, None).cooldown_key != FirecrawlFetcher(one).cooldown_key
        assert "org-a" not in FirecrawlSearch(one, None).cooldown_key

    def test_self_hosted_keys_differ_by_token(self) -> None:
        base = "http://searxng:8080"
        assert SearxngSearch({"baseUrl": base, "apiKey": "a"}, None).cooldown_key != SearxngSearch({"baseUrl": base, "apiKey": "b"}, None).cooldown_key
        assert Crawl4AIServerFetcher({"baseUrl": "http://c:1", "apiKey": "a"}).cooldown_key != Crawl4AIServerFetcher({"baseUrl": "http://c:1"}).cooldown_key

    def test_a_pages_refusal_never_cools_the_source(self) -> None:
        cooldowns = Cooldowns()
        for status in (401, 403, 429):
            try:
                check_page_status(FetchedBody("https://a.example/", status, None, b""))
            except (Blocked, RateLimited) as e:
                assert e.page_level
                cooldowns.failed("builtin", e)
        assert not cooldowns.cooling("builtin")
        cooldowns.failed("builtin", Blocked("the engine blocked us"))
        assert cooldowns.cooling("builtin")


class TestMalformedInput:
    async def test_one_bad_result_url_does_not_sink_the_search(self, lab: WebLab) -> None:
        lab.engines.set_ddgs(results(("https://good.example/", "Good"), ("http://bad.example:99999/", "Bad port"), ("http://[::1/", "Bad v6")))
        call = await lab.agent().search("q")
        assert [s.url.split("#")[0] for s in call.output.sources] == ["https://good.example/"]

    def test_an_rfc2231_charset_parameter(self) -> None:
        body = "<html><body><p>Café menu</p></body></html>".encode()
        document = parse_body("text/html; charset*=utf-8''utf-8", body, url="https://a.example/")
        assert "Café menu" in document.blocks[0].content

    def test_html_comments_are_not_page_text(self) -> None:
        body = b"<html><body><!-- Start of bodyStart --><main><!--$--><p>Real words here.</p><!--/$--></main></body></html>"
        document = parse_body("text/html", body, url="https://a.example/")
        text = " ".join(block.content for block in document.blocks)
        assert "Real words here." in text
        assert "bodyStart" not in text and "$" not in text

    def test_a_lookalike_domain_is_shown_in_punycode(self) -> None:
        assert display_domain("https://аpple.com/") == "xn--pple-43d.com"
        assert display_domain("https://www.example.com/") == "example.com"
        assert display_domain("http://bad.example:99999/") == "bad.example"


class TestRateLimitedPages:
    async def test_a_429_page_is_reported_at_once_not_after_the_deadline(self, lab: WebLab) -> None:
        lab.site.add("http://busy.test/", Page(status=429, body=b"slow down"))
        started = time.monotonic()
        call = await lab.agent().fetch("http://busy.test/")
        assert call.payload["error_type"] == "rate_limited"
        assert time.monotonic() - started < 5


class TestChainShape:
    async def test_ddgs_stored_as_a_provider_is_tried_once(self, lab: WebLab) -> None:
        from ddgs.exceptions import DDGSException

        lab.engines.set_ddgs(DDGSException("engines failed"))
        lab.engines.set_duckduckgo(results(("https://a.example/", "A")))
        call = await lab.agent("ddgs").search("q")
        assert call.output.success
        assert lab.engines.ddgs_queries == ["q"]


class TestFirecrawlTimeout:
    async def test_no_request_without_time_for_the_server_to_answer(self, monkeypatch) -> None:
        sent: list[httpx.Request] = []
        monkeypatch.setattr("app.web.endpoint.guarded_async_client", lambda endpoint, timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={}))))
        monkeypatch.setattr("app.web.fetch.firecrawl.check_public", lambda url: _noop())
        with pytest.raises(TimedOut):
            await FirecrawlFetcher({"apiKey": "k"}).fetch("https://a.example/", Deadline.after(1.5))
        assert sent == []


async def _noop() -> None:
    return None
