"""``Crawl4AIFetcher`` driving a real Chromium against a local fixture site.

Skipped when no Chromium is installed (``python -m playwright install chromium``, plus
``python -m patchright install chromium`` for the browser-like identity). Run with ``-m browser``.
"""

import asyncio
import html
import os
import signal
import time
from collections.abc import AsyncIterator, Iterator

import psutil
import pytest
from aiohttp import web

from app.connectors.sources.web import address_guard
from app.connectors.sources.web.crawl4ai_fetcher import Crawl4AIFetcher
from app.connectors.sources.web.csr_detection import (
    CSR_PROBE_JS,
    PRE_HYDRATION_CAPTURE_JS,
)
from app.connectors.sources.web.fetch_strategy import PIPESHUB_USER_AGENT

pytestmark = [pytest.mark.browser, pytest.mark.timeout(240)]

LONG_TEXT = "Plenty of words about the page subject, enough to count as content. " * 5
EMPTY = "<html><head><title>Empty</title></head><body><div id='marker'></div></body></html>"


def _page(title: str, body: str = LONG_TEXT, script: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body><main><p>{body}</p></main>{script}</body></html>"


def _kill_chromium() -> int:
    killed = 0
    for child in psutil.Process().children(recursive=True):
        try:
            if "chrom" in child.name().lower() or "headless_shell" in child.name().lower():
                os.kill(child.pid, signal.SIGKILL)
                killed += 1
        except (psutil.Error, ProcessLookupError):
            pass
    return killed


class Site:
    def __init__(self) -> None:
        self.user_agents: list[str] = []
        self.cookies: list[str] = []
        self.requested: list[str] = []
        self.base = ""

    def app(self) -> web.Application:
        async def page(request: web.Request) -> web.Response:
            self.user_agents.append(request.headers.get("User-Agent", ""))
            return web.Response(text=_page(html.escape(request.path)), content_type="text/html")

        async def ua(request: web.Request) -> web.Response:
            self.user_agents.append(request.headers.get("User-Agent", ""))
            script = "<script>document.querySelector('main').dataset.ua = navigator.userAgent</script>"
            return web.Response(text=_page("UA", script=script), content_type="text/html")

        async def set_cookie(_: web.Request) -> web.Response:
            response = web.Response(text=_page("Set"), content_type="text/html")
            response.set_cookie("session", "connector-one")
            return response

        async def echo_cookie(request: web.Request) -> web.Response:
            self.cookies.append(request.headers.get("Cookie", ""))
            return web.Response(text=_page("Echo"), content_type="text/html")

        async def empty(_: web.Request) -> web.Response:
            return web.Response(text=EMPTY, content_type="text/html")

        async def missing(_: web.Request) -> web.Response:
            return web.Response(status=404, text=_page("Missing"), content_type="text/html")

        async def slow(_: web.Request) -> web.Response:
            await asyncio.sleep(2)
            return web.Response(text=_page("Slow"), content_type="text/html")

        async def boom(_: web.Request) -> web.Response:
            # Every load of this page takes the browser down, like a renderer bug.
            _kill_chromium()
            await asyncio.sleep(1)
            return web.Response(text=_page("Boom"), content_type="text/html")

        async def busy(_: web.Request) -> web.Response:
            return web.Response(text=_page("Busy", script="<script>while (true) {}</script>"), content_type="text/html")

        async def csr(_: web.Request) -> web.Response:
            script = (
                "<script>setTimeout(() => { document.querySelector('main').innerHTML ="
                f" '<p>{LONG_TEXT}</p>'; }}, 300)</script>"
            )
            return web.Response(text=_page("App", body="", script=script), content_type="text/html")

        async def article(_: web.Request) -> web.Response:
            # Like a single-page app: a shell first, then the article from a JSON call once loaded.
            script = (
                "<script>document.addEventListener('DOMContentLoaded', () => setTimeout(() =>"
                " fetch('/api').then(r => r.json()).then(j => {"
                " document.querySelector('main').insertAdjacentHTML('beforeend', '<h1>' + j.title + '</h1>'); }), 600));"
                "</script><img src='/pixel.png'><link rel='stylesheet' href='/style.css'>"
            )
            return web.Response(text=_page("Article", body=LONG_TEXT, script=script), content_type="text/html")

        async def api(request: web.Request) -> web.Response:
            self.requested.append(request.path)
            await asyncio.sleep(1.5)
            return web.json_response({"title": "The real article"})

        async def asset(request: web.Request) -> web.Response:
            self.requested.append(request.path)
            if request.path.endswith(".css"):
                return web.Response(text="@font-face { font-family: x; src: url(/font.woff2); } body { font-family: x; }",
                                    content_type="text/css")
            return web.Response(body=b"\x89PNG", content_type="image/png")

        app = web.Application()
        app.router.add_get("/article", article)
        app.router.add_get("/api", api)
        app.router.add_get("/pixel.png", asset)
        app.router.add_get("/style.css", asset)
        app.router.add_get("/font.woff2", asset)
        app.router.add_get("/ua", ua)
        app.router.add_get("/set", set_cookie)
        app.router.add_get("/echo", echo_cookie)
        app.router.add_get("/empty", empty)
        app.router.add_get("/missing", missing)
        app.router.add_get("/slow", slow)
        app.router.add_get("/boom", boom)
        app.router.add_get("/busy", busy)
        app.router.add_get("/csr", csr)
        app.router.add_get("/{name}", page)
        return app


@pytest.fixture
def allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(address_guard.ALLOWED_HOSTS_ENV, "localhost")
    address_guard._allowed_hosts.cache_clear()
    yield
    address_guard._allowed_hosts.cache_clear()


@pytest.fixture
async def site(allow_localhost: None) -> AsyncIterator[Site]:
    fixture = Site()
    runner = web.AppRunner(fixture.app())
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    fixture.base = f"http://localhost:{port}"
    try:
        yield fixture
    finally:
        await runner.cleanup()


async def _started(*, browser_like: bool = False, **kw: object) -> Crawl4AIFetcher:
    fetcher = Crawl4AIFetcher(page_timeout=10_000, wait_for_timeout=3_000, delay_before_return_html=0.2, **kw)
    try:
        await fetcher.start(browser_like=browser_like)
    except Exception as e:
        await fetcher.close()
        pytest.skip(f"No Chromium for this identity: {e}")
    return fetcher


@pytest.fixture
async def fetcher() -> AsyncIterator[Crawl4AIFetcher]:
    started = await _started(concurrency=3)
    try:
        yield started
    finally:
        await started.close()


async def test_pipeshub_identity_sends_its_own_user_agent(site: Site, fetcher: Crawl4AIFetcher) -> None:
    result = await fetcher.fetch(f"{site.base}/ua", browser_like=False)

    assert result.success, result.error
    assert site.user_agents == [PIPESHUB_USER_AGENT]
    assert f'data-ua="{PIPESHUB_USER_AGENT}"' in (result.html or "")


async def test_browser_like_identity_still_looks_like_a_browser(site: Site) -> None:
    fetcher = await _started(browser_like=True)
    try:
        result = await fetcher.fetch(f"{site.base}/ua", browser_like=True)
    finally:
        await fetcher.close()

    assert result.success, result.error
    assert site.user_agents and "PipesHub" not in site.user_agents[0]
    assert "Mozilla/5.0 (" in site.user_agents[0]


async def test_an_empty_page_returns_its_html_once_the_wait_budget_is_spent(site: Site, fetcher: Crawl4AIFetcher) -> None:
    started = time.monotonic()
    result = await fetcher.fetch(f"{site.base}/empty", browser_like=False)

    assert result.success, result.error
    assert 'id="marker"' in (result.html or "")
    assert time.monotonic() - started < Crawl4AIFetcher.page_deadline_seconds(fetcher._run_configs[False])


async def test_status_and_landing_url_survive_prefetch(site: Site, fetcher: Crawl4AIFetcher) -> None:
    missing, ok = await fetcher.fetch_many([f"{site.base}/missing", f"{site.base}/fine"], browser_like=False)

    assert missing.status_code == 404
    assert ok.success and ok.status_code == 200
    assert ok.content_type and ok.content_type.startswith("text/html")
    assert "<title>/fine</title>" in (ok.html or "")


async def test_cookies_set_for_one_job_are_not_sent_for_another(site: Site, fetcher: Crawl4AIFetcher) -> None:
    await fetcher.fetch(f"{site.base}/set", browser_like=False, context_key="connector-1:run-1")
    await fetcher.fetch(f"{site.base}/echo", browser_like=False, context_key="connector-1:run-1")
    await fetcher.fetch(f"{site.base}/echo", browser_like=False, context_key="connector-2:run-1")
    await fetcher.fetch(f"{site.base}/echo", browser_like=False)

    assert ["session=connector-one" in cookies for cookies in site.cookies] == [True, False, False]


async def test_a_browser_killed_mid_batch_is_relaunched_and_the_lost_pages_load(site: Site, fetcher: Crawl4AIFetcher) -> None:
    urls = [f"{site.base}/slow", f"{site.base}/a", f"{site.base}/b"]

    async def kill_soon() -> int:
        await asyncio.sleep(1)
        return _kill_chromium()

    results, killed = await asyncio.gather(fetcher.fetch_many(urls, browser_like=False), kill_soon())

    assert killed > 0
    assert [r.success for r in results] == [True, True, True], [r.error for r in results]
    assert fetcher._supervisors[False].generation == 1


async def test_a_page_that_kills_the_browser_every_time_is_given_up_on(site: Site, fetcher: Crawl4AIFetcher) -> None:
    results = await fetcher.fetch_many([f"{site.base}/boom", f"{site.base}/a"], browser_like=False)
    after = await fetcher.fetch(f"{site.base}/b", browser_like=False)

    assert results[0].crashed_browser is True
    assert results[1].success, results[1].error
    assert after.success, after.error


async def test_a_page_stuck_in_a_script_loop_fails_within_its_deadline(site: Site, fetcher: Crawl4AIFetcher) -> None:
    deadline = Crawl4AIFetcher.page_deadline_seconds(fetcher._run_configs[False])
    started = time.monotonic()
    busy, fine = await fetcher.fetch_many([f"{site.base}/busy", f"{site.base}/fine"], browser_like=False)

    assert time.monotonic() - started < deadline + 5
    assert busy.success is False
    assert fine.success, fine.error
    assert (await fetcher.fetch(f"{site.base}/after", browser_like=False)).success


async def test_the_render_probe_runs_on_the_shared_browser(site: Site, fetcher: Crawl4AIFetcher) -> None:
    async def probe(path: str) -> dict:
        result = await fetcher.fetch(
            f"{site.base}{path}", browser_like=False, js_code=CSR_PROBE_JS, js_code_before_wait=PRE_HYDRATION_CAPTURE_JS,
        )
        assert result.success, result.error
        return result.js_execution_result or {}

    script_rendered = await probe("/csr")
    server_rendered = await probe("/static")

    assert script_rendered["preLen"] < 50 <= script_rendered["postLen"]
    assert server_rendered["preLen"] == server_rendered["postLen"] > 200


@pytest.mark.parametrize("browser_like", [False, True], ids=["pipeshub", "browser-like"])
async def test_the_wait_lasts_until_the_apps_own_requests_are_done(site: Site, browser_like: bool) -> None:
    fetcher = await _started(browser_like=browser_like)
    try:
        result = await fetcher.fetch(f"{site.base}/article", browser_like=browser_like)
    finally:
        await fetcher.close()

    assert result.success, result.error
    assert "<h1>The real article</h1>" in (result.html or "")


async def test_images_and_fonts_are_not_downloaded(site: Site, fetcher: Crawl4AIFetcher) -> None:
    result = await fetcher.fetch(f"{site.base}/article", browser_like=False)

    assert result.success, result.error
    assert "/style.css" in site.requested
    assert "/pixel.png" not in site.requested
    assert "/font.woff2" not in site.requested
