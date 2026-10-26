"""The services with fake sources and gates."""

from dataclasses import dataclass, field

import pytest

from app.web.chain import ChainFailed, Cooldowns
from app.web.container import (
    UnknownSearchProvider,
    build_web_services,
    search_providers,
)
from app.web.deadline import Deadline
from app.web.errors import AccessDenied, Blocked, UnknownProvider
from app.web.gate import DefaultWebAccessGate
from app.web.models import CallerContext, WebPage, WebResult
from app.web.ports import AccessDecision, AccessRequest
from app.web.services import WebFetchService, WebSearchService

CALLER = CallerContext(org_id="org-1", user_id="user-1", conversation_id="conv-1")


@dataclass
class RecordingGate:
    denied: set[str] = field(default_factory=set)  # targets to refuse
    seen: list[AccessRequest] = field(default_factory=list)

    async def check(self, request: AccessRequest) -> AccessDecision:
        self.seen.append(request)
        return AccessDecision(request.target not in self.denied, "not on the list")


@dataclass
class FakeSearch:
    id: str
    results: list[WebResult] | Exception
    cooldown_key: str = ""

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


@dataclass
class FakeFetcher:
    id: str = "fake"
    cooldown_key: str = "fake"
    redirect_to: str | None = None
    hop_answers: list[bool] = field(default_factory=list)

    async def fetch(self, url: str, deadline: Deadline, *, allow_hop=None) -> WebPage:
        if self.redirect_to:
            allowed = await allow_hop(self.redirect_to)
            self.hop_answers.append(allowed)
            if not allowed:
                raise Blocked("redirect refused")
        return WebPage(url=url, title="T", blocks=(), fetched_by=self.id)


def result(url: str, source: str = "a") -> WebResult:
    return WebResult(url=url, title=url, snippet="s", source=source)


class TestSearch:
    async def test_dedupes_by_page_and_drops_non_http_links(self) -> None:
        provider = FakeSearch("a", [
            result("https://example.com/doc?utm_source=x"),
            result("https://EXAMPLE.com/doc#part"),
            result("javascript:alert(1)"),
            result("https://other.org/"),
        ])
        outcome = await WebSearchService([provider], RecordingGate(), cooldowns=Cooldowns()).search("q", CALLER)
        assert [r.url for r in outcome.results] == ["https://example.com/doc?utm_source=x", "https://other.org/"]
        assert outcome.served_by == "a"

    async def test_hides_results_the_gate_refuses(self) -> None:
        gate = RecordingGate(denied={"https://blocked.example/"})
        provider = FakeSearch("a", [result("https://blocked.example/"), result("https://fine.example/")])
        outcome = await WebSearchService([provider], gate, cooldowns=Cooldowns()).search("q", CALLER)
        assert [r.url for r in outcome.results] == ["https://fine.example/"]
        assert gate.seen[0] == AccessRequest("search", "q", CALLER)

    async def test_a_refused_search_never_reaches_a_provider(self) -> None:
        provider = FakeSearch("a", AssertionError("must not run"))  # type: ignore[arg-type]
        with pytest.raises(AccessDenied) as info:
            await WebSearchService([provider], RecordingGate(denied={"secret"}), cooldowns=Cooldowns()).search("secret", CALLER)
        assert info.value.error_type == "access_denied"

    async def test_falls_back(self) -> None:
        providers = [FakeSearch("a", Blocked("bot"), "a"), FakeSearch("b", [result("https://x.example/", "b")], "b")]
        outcome = await WebSearchService(providers, RecordingGate(), cooldowns=Cooldowns()).search("q", CALLER)
        assert outcome.served_by == "b"
        assert [a.error_type for a in outcome.attempts] == ["blocked", None]


class TestFetch:
    async def test_checks_the_canonical_url_then_every_redirect(self) -> None:
        gate = RecordingGate()
        fetcher = FakeFetcher(redirect_to="https://Next.example/page#x")
        outcome = await WebFetchService([fetcher], gate, cooldowns=Cooldowns()).fetch("https://Start.example/a?utm_source=z", CALLER)
        assert outcome.value.title == "T"
        assert [r.target for r in gate.seen] == ["https://start.example/a", "https://next.example/page"]
        assert all(r.action == "fetch" and r.caller == CALLER for r in gate.seen)

    async def test_a_refused_redirect_stops_the_fetch(self) -> None:
        gate = RecordingGate(denied={"https://next.example/page"})
        fetcher = FakeFetcher(redirect_to="https://next.example/page")
        with pytest.raises(ChainFailed):
            await WebFetchService([fetcher], gate, cooldowns=Cooldowns()).fetch("https://start.example/", CALLER)
        assert fetcher.hop_answers == [False]

    async def test_a_refused_url_is_never_fetched(self) -> None:
        fetcher = FakeFetcher()
        gate = RecordingGate(denied={"https://start.example/"})
        with pytest.raises(AccessDenied):
            await WebFetchService([fetcher], gate, cooldowns=Cooldowns()).fetch("https://start.example/", CALLER)


class TestContainer:
    def test_default_is_the_free_chain(self) -> None:
        assert [p.id for p in search_providers({"provider": "duckduckgo", "configuration": {}})] == ["ddgs", "duckduckgo"]
        assert [p.id for p in search_providers({})] == ["ddgs", "duckduckgo"]

    def test_configured_provider_goes_first(self) -> None:
        providers = search_providers({"provider": "serper", "configuration": {"apiKey": "k"}})
        assert [p.id for p in providers] == ["serper", "ddgs", "duckduckgo"]

    async def test_unknown_provider_is_reported_not_replaced(self) -> None:  # WP-0-SRCH-05
        providers = search_providers({"provider": "bing", "configuration": {}})
        assert len(providers) == 1 and isinstance(providers[0], UnknownSearchProvider)
        services = build_web_services({"provider": "bing"}, DefaultWebAccessGate())
        with pytest.raises(ChainFailed) as info:
            await services.search.search("q", CALLER)
        assert info.value.error_type == "unknown_provider"
        assert isinstance(info.value.errors[0], UnknownProvider)
