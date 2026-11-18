"""The services with fake sources and gates."""

from dataclasses import dataclass, field

import pytest
from web_behaviour_fakes import FakeWeb, Page

from app.web.chain import ChainFailed, Cooldowns
from app.web.container import (
    NoSearchProvider,
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



def _real_png() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="PNG")
    return out.getvalue()


PNG = _real_png()
IMG = "http://site.test"


class TestFetchImages:
    """A page's images: each asked of the gate like a page, read through the guarded fetch,
    and never failing the page (a missing image is None)."""

    @staticmethod
    def _service(gate: RecordingGate) -> WebFetchService:
        return WebFetchService([], gate, cooldowns=Cooldowns())

    async def test_each_image_in_order_and_one_that_cannot_be_read_is_none(self, fake_web: FakeWeb) -> None:
        fake_web.add(f"{IMG}/a.png", Page(body=PNG, content_type="image/png"))
        fake_web.add(f"{IMG}/page", Page(body=b"<html>not an image</html>"))
        fake_web.add(f"{IMG}/b.png", Page(body=PNG, content_type="image/png"))

        uris = await self._service(RecordingGate()).fetch_images(
            [f"{IMG}/a.png", f"{IMG}/page", f"{IMG}/missing.png", f"{IMG}/b.png"], CALLER,
        )

        assert [u is not None and u.startswith("data:image/png;base64,") for u in uris] == [True, False, False, True]

    async def test_an_image_the_gate_refuses_is_never_requested(self, fake_web: FakeWeb) -> None:
        fake_web.add(f"{IMG}/a.png", Page(body=PNG, content_type="image/png"))
        gate = RecordingGate(denied={f"{IMG}/a.png"})

        assert await self._service(gate).fetch_images([f"{IMG}/a.png"], CALLER) == [None]
        assert fake_web.gets(f"{IMG}/a.png") == 0
        assert [(r.action, r.target, r.caller) for r in gate.seen] == [("fetch", f"{IMG}/a.png", CALLER)]

    async def test_a_redirect_the_gate_refuses_is_not_followed(self, fake_web: FakeWeb) -> None:
        fake_web.redirect(f"{IMG}/a.png", "http://elsewhere.test/b.png").add(
            "http://elsewhere.test/b.png", Page(body=PNG, content_type="image/png"),
        )
        gate = RecordingGate(denied={"http://elsewhere.test/b.png"})

        assert await self._service(gate).fetch_images([f"{IMG}/a.png"], CALLER) == [None]
        assert fake_web.gets("http://elsewhere.test/b.png") == 0

    async def test_a_pages_images_are_read_a_few_at_a_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each image may be several MB and is held until it is sent: never a whole gallery at once."""
        import asyncio

        from app.web import services

        in_flight = peak = 0

        async def slow_read(url: str, deadline: object, *, allow_hop: object = None) -> str:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return "data:image/png;base64,AA=="

        monkeypatch.setattr(services, "fetch_image_data_uri", slow_read)
        uris = await self._service(RecordingGate()).fetch_images([f"{IMG}/{i}.png" for i in range(12)], CALLER)

        assert len(uris) == 12 and all(uris)
        assert peak == services.IMAGES_IN_FLIGHT

    async def test_no_images_asks_nothing(self) -> None:
        gate = RecordingGate()
        assert await self._service(gate).fetch_images([], CALLER) == []
        assert gate.seen == []


class TestContainer:
    def test_a_chosen_duckduckgo_is_the_free_chain(self) -> None:
        assert [p.id for p in search_providers({"provider": "duckduckgo", "configuration": {}})] == ["ddgs", "duckduckgo"]

    def test_nothing_configured_has_no_engine_to_fall_back_on(self) -> None:
        providers = search_providers({})
        assert len(providers) == 1 and isinstance(providers[0], NoSearchProvider)

    async def test_nothing_configured_is_reported_not_searched(self) -> None:
        services = build_web_services({}, DefaultWebAccessGate())
        with pytest.raises(ChainFailed) as info:
            await services.search.search("q", CALLER)
        assert info.value.error_type == "not_configured"

    def test_configured_provider_runs_alone(self) -> None:
        providers = search_providers({"provider": "serper", "configuration": {"apiKey": "k"}})
        assert [p.id for p in providers] == ["serper"]

    async def test_unknown_provider_is_reported_not_replaced(self) -> None:  # WP-0-SRCH-05
        providers = search_providers({"provider": "bing", "configuration": {}})
        assert len(providers) == 1 and isinstance(providers[0], UnknownSearchProvider)
        services = build_web_services({"provider": "bing"}, DefaultWebAccessGate())
        with pytest.raises(ChainFailed) as info:
            await services.search.search("q", CALLER)
        assert info.value.error_type == "unknown_provider"
        assert isinstance(info.value.errors[0], UnknownProvider)
