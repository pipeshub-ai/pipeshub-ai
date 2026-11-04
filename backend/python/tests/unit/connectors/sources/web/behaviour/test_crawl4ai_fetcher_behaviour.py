"""``Crawl4AIFetcher`` over a fake browser: its own thread and event loop,
status resolution, failure isolation and the process-wide shared instance.
"""

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from web_behaviour_fakes import (
    DRIVER_GONE,
    FakeWeb,
    Page,
    VirtualClock,
    browser_crawler_class,
    html_page,
)

from app.connectors.sources.web import browser_supervisor, crawl4ai_fetcher
from app.connectors.sources.web.browser_supervisor import BrowserUnavailableError
from app.connectors.sources.web.crawl4ai_fetcher import (
    Crawl4AIFetcher,
    close_shared_fetcher,
    get_shared_fetcher,
    release_shared_fetcher,
    shared_fetcher,
)


def _with_crawler(monkeypatch: pytest.MonkeyPatch, arun: Callable[[str], Awaitable[object]], site: FakeWeb) -> None:
    """Replace the fake browser's ``arun`` with a scripted one."""
    base = browser_crawler_class(site)

    class Scripted(base):  # type: ignore[misc,valid-type]
        async def arun(self, url: str, config: object = None, **kw: object) -> object:
            return await arun(url)

    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", Scripted)


def _crawl_result(url: str, **kw: object) -> SimpleNamespace:
    fields = {"url": url, "redirected_url": url, "html": "", "success": False, "status_code": None,
              "error_message": None, "crawl_stats": None, "js_execution_result": None}
    fields.update(kw)
    return SimpleNamespace(**fields)


async def test_fetch_returns_the_rendered_page_from_the_browser_thread(browser: FakeWeb) -> None:
    browser.html("http://site.test/", "Home", text="Hello from the browser")

    async with Crawl4AIFetcher(concurrency=2) as fetcher:
        result = await fetcher.fetch("http://site.test/")

    assert result.success is True
    assert result.status_code == 200
    assert "Hello from the browser" in (result.html or "")


async def test_fetch_before_start_says_how_to_start_the_fetcher(browser: FakeWeb) -> None:
    fetcher = Crawl4AIFetcher()

    with pytest.raises(RuntimeError, match="call start"):
        await fetcher.fetch("http://site.test/")
    with pytest.raises(RuntimeError, match="call start"):
        await fetcher.fetch_many(["http://site.test/"])


@pytest.mark.parametrize(
    ("scripted", "expected_status"),
    [
        pytest.param({"error_message": "Failed on navigating ACS-GOTO: HTTP 403"}, 403, id="status-in-error"),
        pytest.param({"crawl_stats": {"proxies_used": [{"status_code": 502}, {"status_code": None}]}}, 502,
                     id="status-from-proxy-attempts"),
        pytest.param({"error_message": "net::ERR_NAME_NOT_RESOLVED"}, None, id="no-status-anywhere"),
    ],
)
async def test_a_missing_status_is_recovered_from_whatever_the_browser_reported(
    scripted: dict, expected_status: int | None, browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def arun(url: str) -> SimpleNamespace:
        return _crawl_result(url, **scripted)

    _with_crawler(monkeypatch, arun, browser)

    async with Crawl4AIFetcher() as fetcher:
        result = await fetcher.fetch("http://site.test/")

    assert result.success is False
    assert result.status_code == expected_status


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        pytest.param(RuntimeError("Target page, context or browser has been closed"), "browser has been closed",
                     id="browser-crash"),
        pytest.param(asyncio.TimeoutError(), "Timed out after", id="timeout"),
    ],
)
async def test_a_browser_failure_comes_back_as_a_failed_result_not_an_exception(
    failure: BaseException, message: str, browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def arun(url: str) -> SimpleNamespace:
        raise failure

    _with_crawler(monkeypatch, arun, browser)

    async with Crawl4AIFetcher() as fetcher:
        result = await fetcher.fetch("http://site.test/")

    assert result.success is False
    assert message in (result.error or "")


async def test_fetch_many_returns_one_result_per_url_and_isolates_failures(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser.html("http://site.test/a", "A")
    browser.html("http://site.test/c", "C")
    base = browser_crawler_class(browser)

    async def arun(url: str) -> object:
        if url.endswith("/b"):
            raise RuntimeError("tab crashed")
        return await base().arun(url)

    _with_crawler(monkeypatch, arun, browser)
    urls = ["http://site.test/a", "http://site.test/b", "http://site.test/c"]

    async with Crawl4AIFetcher(concurrency=2) as fetcher:
        results = await fetcher.fetch_many(urls)

    assert [r.success for r in results] == [True, False, True]
    assert "tab crashed" in (results[1].error or "")
    assert "<title>C</title>" in (results[2].html or "")


async def test_fetch_many_reports_every_url_as_failed_when_every_page_blows_up(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def arun(url: str) -> SimpleNamespace:
        raise RuntimeError("browser context lost")

    _with_crawler(monkeypatch, arun, browser)

    async with Crawl4AIFetcher() as fetcher:
        results = await fetcher.fetch_many(["http://site.test/a", "http://site.test/b"])

    assert [(r.url, r.success, r.error) for r in results] == [
        ("http://site.test/a", False, "browser context lost"),
        ("http://site.test/b", False, "browser context lost"),
    ]


async def test_closing_the_fetcher_stops_its_browser_thread(browser: FakeWeb) -> None:
    fetcher = Crawl4AIFetcher()
    await fetcher.start()
    thread = fetcher._thread
    assert thread is not None and thread.is_alive()

    await fetcher.close()

    assert not thread.is_alive()


@pytest.fixture
def idle_clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    """The idle grace passes at once, and the delay it waited is recorded."""
    clock = VirtualClock()
    monkeypatch.setattr(crawl4ai_fetcher, "asyncio", clock)
    return clock


async def test_the_shared_fetcher_starts_one_browser_and_keeps_it_while_any_caller_holds_it(browser: FakeWeb) -> None:
    first = await get_shared_fetcher()
    second = await get_shared_fetcher()

    assert first is second
    assert browser.browser_starts == 1

    await release_shared_fetcher()
    assert crawl4ai_fetcher._shared_instance is first
    assert crawl4ai_fetcher._idle_close is None
    await release_shared_fetcher()
    await release_shared_fetcher()
    assert crawl4ai_fetcher._ref_count == 0


async def test_the_browser_closes_once_it_sat_unused_for_the_grace(browser: FakeWeb, idle_clock: VirtualClock) -> None:
    fetcher = await get_shared_fetcher()
    await release_shared_fetcher()
    assert crawl4ai_fetcher._shared_instance is fetcher

    await crawl4ai_fetcher._idle_close

    assert idle_clock.slept(60)
    assert crawl4ai_fetcher._shared_instance is None
    assert fetcher._thread is None
    assert await get_shared_fetcher() is not fetcher
    assert browser.browser_starts == 2
    await release_shared_fetcher()


async def test_a_caller_within_the_grace_reuses_the_browser(browser: FakeWeb) -> None:
    fetcher = await get_shared_fetcher()
    await release_shared_fetcher()
    idle_close = crawl4ai_fetcher._idle_close

    assert await get_shared_fetcher() is fetcher

    await asyncio.sleep(0)
    assert idle_close is not None and idle_close.cancelled()
    assert crawl4ai_fetcher._idle_close is None
    assert browser.browser_starts == 1
    await release_shared_fetcher()


async def test_with_no_grace_the_last_release_closes_at_once(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_BROWSER_IDLE_CLOSE_SECONDS", "0")
    await get_shared_fetcher()

    await release_shared_fetcher()

    assert crawl4ai_fetcher._shared_instance is None
    assert crawl4ai_fetcher._idle_close is None


async def test_a_caller_during_an_idle_close_gets_a_new_browser(
    browser: FakeWeb, idle_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await get_shared_fetcher()
    closing, finish = asyncio.Event(), asyncio.Event()
    close = first.close

    async def slow_close() -> None:
        closing.set()
        await finish.wait()
        await close()

    monkeypatch.setattr(first, "close", slow_close)
    await release_shared_fetcher()
    idle_close = crawl4ai_fetcher._idle_close
    await closing.wait()

    second = await get_shared_fetcher()

    assert second is not first
    assert browser.browser_starts == 2
    browser.html("http://site.test/", "Home")
    assert (await second.fetch("http://site.test/")).success is True
    finish.set()
    await idle_close
    assert crawl4ai_fetcher._shared_instance is second
    await release_shared_fetcher()


async def test_shared_fetcher_gives_the_reference_back_when_the_block_fails(browser: FakeWeb) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with shared_fetcher():
            assert crawl4ai_fetcher._ref_count == 1
            raise RuntimeError("boom")

    assert crawl4ai_fetcher._ref_count == 0


async def test_a_cancelled_first_start_leaves_no_browser_behind(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    starting = asyncio.Event()

    async def hangs(self: Crawl4AIFetcher, *, browser_like: bool = True) -> None:
        starting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Crawl4AIFetcher, "start", hangs)
    getting = asyncio.ensure_future(get_shared_fetcher())
    await starting.wait()

    getting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await getting

    assert crawl4ai_fetcher._shared_instance is None
    assert crawl4ai_fetcher._ref_count == 0


async def test_a_failed_start_on_an_idle_browser_keeps_its_idle_close(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = await get_shared_fetcher()
    await release_shared_fetcher()
    idle_close = crawl4ai_fetcher._idle_close

    async def fails(*, browser_like: bool = True) -> None:
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(fetcher, "start", fails)
    with pytest.raises(RuntimeError):
        await get_shared_fetcher(browser_like=False)

    assert crawl4ai_fetcher._idle_close is idle_close
    assert idle_close is not None and not idle_close.done()
    assert crawl4ai_fetcher._ref_count == 0


async def test_a_release_survives_the_holder_being_cancelled(browser: FakeWeb) -> None:
    inside, leave = asyncio.Event(), asyncio.Event()

    async def holder() -> None:
        async with shared_fetcher():
            inside.set()
            await leave.wait()

    task = asyncio.ensure_future(holder())
    await inside.wait()
    async with crawl4ai_fetcher._get_shared_lock():
        leave.set()
        await asyncio.sleep(0)  # the holder is now waiting for the lock to release
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    for _ in range(3):
        await asyncio.sleep(0)

    assert crawl4ai_fetcher._ref_count == 0


async def test_shutdown_closes_the_browser_whoever_holds_it(browser: FakeWeb) -> None:
    held = await get_shared_fetcher()
    await get_shared_fetcher()
    await release_shared_fetcher()

    await close_shared_fetcher()

    assert crawl4ai_fetcher._shared_instance is None
    assert crawl4ai_fetcher._ref_count == 0
    assert held._thread is None


async def test_shutdown_stops_a_pending_idle_close(browser: FakeWeb) -> None:
    await get_shared_fetcher()
    await release_shared_fetcher()
    idle_close = crawl4ai_fetcher._idle_close

    await close_shared_fetcher()

    await asyncio.sleep(0)
    assert idle_close is not None and idle_close.cancelled()
    assert crawl4ai_fetcher._shared_instance is None


async def test_a_browser_that_fails_to_start_is_tried_again_next_time(browser: FakeWeb) -> None:
    browser.browser_broken = True
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        await get_shared_fetcher()
    assert crawl4ai_fetcher._shared_instance is None

    browser.browser_broken = False
    fetcher = await get_shared_fetcher()

    browser.html("http://site.test/", "Home")
    assert (await fetcher.fetch("http://site.test/")).success is True
    assert browser.browser_starts == 2
    await release_shared_fetcher()


@pytest.mark.parametrize("many", [False, True], ids=["fetch", "fetch_many"])
async def test_a_redirect_in_the_browser_reports_where_the_page_ended_up(many: bool, browser: FakeWeb) -> None:
    browser.redirect("http://site.test/old", "/new")
    browser.add("http://site.test/new", Page(body=html_page("New")))

    async with Crawl4AIFetcher() as fetcher:
        if many:
            [result] = await fetcher.fetch_many(["http://site.test/old"])
        else:
            result = await fetcher.fetch("http://site.test/old")

    assert result.url == "http://site.test/new"


def _pages(browser: FakeWeb, count: int) -> list[str]:
    urls = [f"http://site.test/p{i}" for i in range(count)]
    for url in urls:
        browser.html(url, url)
    return urls


async def test_a_browser_whose_driver_died_is_relaunched_and_the_lost_pages_fetched_again(browser: FakeWeb) -> None:
    urls = _pages(browser, 3)

    async with Crawl4AIFetcher(concurrency=2) as fetcher:
        browser.kill_browser()
        results = await fetcher.fetch_many(urls)

    assert [r.success for r in results] == [True, True, True]
    assert browser.browser_starts == 2


async def test_fetches_that_all_find_the_browser_dead_relaunch_it_once(browser: FakeWeb) -> None:
    urls = _pages(browser, 5)

    async with Crawl4AIFetcher(concurrency=5) as fetcher:
        browser.kill_browser()
        results = await asyncio.gather(*(fetcher.fetch(url) for url in urls))

    assert all(r.success for r in results)
    assert browser.browser_starts == 2


async def test_a_browser_that_cannot_be_relaunched_raises_instead_of_reporting_failed_pages(browser: FakeWeb) -> None:
    urls = _pages(browser, 2)
    browser.browser_stays_dead = True

    async with Crawl4AIFetcher() as fetcher:
        browser.kill_browser()
        with pytest.raises(BrowserUnavailableError):
            await fetcher.fetch_many(urls)
        with pytest.raises(BrowserUnavailableError):
            await fetcher.fetch(urls[0])


async def test_relaunches_that_keep_failing_stop_until_a_cooldown_then_one_more_is_tried(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser_supervisor, "_RELAUNCH_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(browser_supervisor, "_RELAUNCH_COOLDOWN_SECONDS", 0.2)
    [url] = _pages(browser, 1)
    browser.browser_stays_dead = True

    async with Crawl4AIFetcher() as fetcher:
        browser.kill_browser()
        for _ in range(2):
            with pytest.raises(BrowserUnavailableError):
                await fetcher.fetch(url)
        launches = browser.browser_starts

        with pytest.raises(BrowserUnavailableError):
            await fetcher.fetch(url)
        assert browser.browser_starts == launches

        browser.browser_broken = False
        await asyncio.sleep(0.25)
        assert (await fetcher.fetch(url)).success is True
        assert browser.browser_starts == launches + 1


async def test_closing_a_fetcher_whose_browser_died_still_stops_its_thread(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = browser_crawler_class(browser)

    class DiesOnClose(base):  # type: ignore[misc,valid-type]
        async def close(self) -> None:
            raise RuntimeError(DRIVER_GONE)

    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", DiesOnClose)
    fetcher = Crawl4AIFetcher()
    await fetcher.start()
    thread = fetcher._thread
    assert thread is not None

    await fetcher.close()

    assert not thread.is_alive()


async def test_the_browser_is_relaunched_once_it_has_served_its_page_budget(browser: FakeWeb) -> None:
    urls = _pages(browser, 7)

    async with Crawl4AIFetcher(recycle_after_pages=3) as fetcher:
        results = [
            *await fetcher.fetch_many(urls[:3]),
            *await fetcher.fetch_many(urls[3:6]),
            await fetcher.fetch(urls[6]),
        ]

    assert all(r.success for r in results)
    assert browser.browser_starts == 3

