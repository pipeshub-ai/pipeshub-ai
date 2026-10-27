"""Pages built by scripts are read through the headless browser; everything else never starts it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from web_behaviour_fakes import INTRANET_HOST, FakeWeb, Page

from app.connectors.sources.web.address_guard import ALLOWED_HOSTS_ENV
from app.utils.url_fetcher import FetchError, PublicTarget
from app.web.container import fetchers
from app.web.deadline import Deadline
from app.web.errors import Blocked, NoText, TimedOut, UnsafeUrl, UpstreamError
from app.web.fetch.browser import RENDER_SWITCH_ENV, BrowserRenderer
from app.web.fetch.builtin import SHELL_TEXT_CHARS, BuiltinFetcher, looks_like_shell
from app.web.fetch.http import FetchedBody
from app.web.fetch.parsers import ParsedDocument

SITE = "http://app.test"
SHELL = b'<html><head><title>App</title><script src="/bundle.js"></script></head><body><div id="root"></div></body></html>'
RENDERED = (
    b"<html><head><title>Product docs</title></head><body><main><p>"
    + b"The rendered documentation explains everything in detail. " * 40
    + b"</p></main></body></html>"
)
FULL = b"<html><head><title>Plain</title><script>analytics()</script></head><body><article><p>" + b"Served in full. " * 120 + b"</p></article></body></html>"


def deadline() -> Deadline:
    return Deadline.after(15)


def reader() -> BuiltinFetcher:
    return BuiltinFetcher(BrowserRenderer())


class TestLooksLikeShell:
    def body(self, content: bytes, content_type: str | None = "text/html") -> FetchedBody:
        return FetchedBody("https://a.example/", 200, content_type, content)

    def test_little_text_and_a_script(self) -> None:
        assert looks_like_shell(self.body(SHELL), ParsedDocument())
        assert looks_like_shell(self.body(SHELL), None)

    def test_enough_text(self) -> None:
        from app.utils.html_to_blocks import TextBlock

        assert not looks_like_shell(self.body(SHELL), ParsedDocument(blocks=[TextBlock(content="x" * SHELL_TEXT_CHARS)]))

    def test_no_script(self) -> None:
        assert not looks_like_shell(self.body(b"<html><body>short</body></html>"), ParsedDocument())

    def test_not_html(self) -> None:
        assert not looks_like_shell(self.body(b"<script> in a text file", "text/plain"), ParsedDocument())


class TestThroughTheBrowser:
    async def test_a_shell_is_rendered(self, fake_browser: FakeWeb) -> None:  # WP-6-CSR-01
        fake_browser.add(f"{SITE}/docs", Page(body=SHELL, rendered=RENDERED))
        page = await reader().fetch(f"{SITE}/docs", deadline())
        assert page.fetched_by == "browser"
        assert page.title == "Product docs"
        assert "rendered documentation" in page.blocks[0].content
        assert fake_browser.browser_visits == [f"{SITE}/docs"]

    async def test_a_full_page_never_starts_the_browser(self, fake_browser: FakeWeb) -> None:  # WP-6-CSR-02
        fake_browser.add(f"{SITE}/plain", Page(body=FULL))
        page = await reader().fetch(f"{SITE}/plain", deadline())
        assert page.fetched_by == "builtin"
        assert fake_browser.browser_visits == []

    async def test_a_browser_that_adds_nothing_keeps_the_served_copy(self, fake_browser: FakeWeb) -> None:
        small = b"<html><body><script>x()</script><p>Small but real page.</p></body></html>"
        fake_browser.add(f"{SITE}/small", Page(body=small))
        page = await reader().fetch(f"{SITE}/small", deadline())
        assert page.fetched_by == "builtin"
        assert fake_browser.browser_visits == [f"{SITE}/small"]

    async def test_a_browser_failure_keeps_the_served_copy(self, fake_browser: FakeWeb) -> None:
        small = b"<html><body><script>x()</script><p>Small but real page.</p></body></html>"
        fake_browser.add(f"{SITE}/crash", Page(body=small, browser_aborts=True))
        page = await reader().fetch(f"{SITE}/crash", deadline())
        assert page.fetched_by == "builtin"
        assert "Small but real page." in page.blocks[0].content

    async def test_an_empty_shell_the_browser_cannot_fill_has_no_text(self, fake_browser: FakeWeb) -> None:
        fake_browser.add(f"{SITE}/empty", Page(body=SHELL, rendered=SHELL))
        with pytest.raises(NoText):
            await reader().fetch(f"{SITE}/empty", deadline())


class TestWhenRenderingIsOff:
    async def test_with_internal_hosts_allowlisted(self, fake_browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:  # WP-6-SEC-01
        """The connector's allowlist would let a page's scripts reach internal hosts in the browser."""
        monkeypatch.setenv(ALLOWED_HOSTS_ENV, INTRANET_HOST)
        assert BrowserRenderer.available() is False
        fake_browser.add(f"{SITE}/docs", Page(body=SHELL, rendered=RENDERED))
        [builtin, *_] = fetchers({"provider": "duckduckgo"})
        with pytest.raises(NoText):
            await builtin.fetch(f"{SITE}/docs", deadline())
        assert fake_browser.browser_visits == []

    @pytest.mark.parametrize("value", ["off", "false", "0", "NO"])
    def test_switched_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(RENDER_SWITCH_ENV, value)
        assert BrowserRenderer.available() is False

    def test_on_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RENDER_SWITCH_ENV, raising=False)
        monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
        assert BrowserRenderer.available() is True
        assert fetchers({"provider": "duckduckgo"})[0]._renderer is not None


class TestRendererChecks:
    """The renderer alone, with the browser stubbed."""

    @staticmethod
    def browser(**result: object) -> object:
        fields = {"url": "https://a.example/", "html": "<html><body>ok</body></html>", "success": True, "status_code": 200, "error": None}
        fields.update(result)
        return patch.object(BrowserRenderer, "_shared_fetcher", AsyncMock(return_value=SimpleNamespace(fetch=AsyncMock(return_value=SimpleNamespace(**fields)))))

    @pytest.fixture(autouse=True)
    def public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def resolve(url: str) -> PublicTarget:
            if ".internal" in url:
                raise FetchError("private")
            return PublicTarget("https", "a.example", 443, ())

        monkeypatch.setattr("app.web.fetch.http.resolve_public_http_target", resolve)

    async def test_an_internal_url_never_reaches_the_browser(self) -> None:
        with self.browser() as shared, pytest.raises(UnsafeUrl):
            await BrowserRenderer().render("http://admin.internal/", deadline())
        shared.assert_not_called()

    async def test_landing_on_an_internal_address_is_refused(self) -> None:
        with self.browser(url="http://admin.internal/secrets"), pytest.raises(UnsafeUrl):
            await BrowserRenderer().render("https://a.example/go", deadline())

    async def test_the_sites_status_counts(self) -> None:
        with self.browser(status_code=403, success=False), pytest.raises(Blocked):
            await BrowserRenderer().render("https://a.example/", deadline())

    async def test_no_content(self) -> None:
        with self.browser(html="", success=False, error="net::ERR_EMPTY_RESPONSE", status_code=None), pytest.raises(UpstreamError):
            await BrowserRenderer().render("https://a.example/", deadline())

    async def test_a_slow_render_ends_at_the_deadline(self) -> None:
        import asyncio

        async def slow(*args: object, **kwargs: object) -> object:
            await asyncio.sleep(30)

        with patch.object(BrowserRenderer, "_shared_fetcher", AsyncMock(return_value=SimpleNamespace(fetch=slow))), pytest.raises(TimedOut):
            await BrowserRenderer().render("https://a.example/", Deadline.after(0.2))
