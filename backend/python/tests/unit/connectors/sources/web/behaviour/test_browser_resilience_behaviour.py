"""``Crawl4AIFetcher`` under failure and load, over the fake browser: crash quarantine, hung
browsers, deadlines, per-call identity, context isolation, memory sizing and the memory gate.
"""

import asyncio
from types import SimpleNamespace

import pytest
from crawl4ai.browser_manager import BrowserManager
from web_behaviour_fakes import (
    DRIVER_GONE,
    FakeMemoryProbe,
    FakeWeb,
    Page,
    browser_crawler_class,
    html_page,
)

from app.connectors.sources.web import browser_supervisor, crawl4ai_fetcher
from app.connectors.sources.web.browser_supervisor import (
    BrowserSupervisor,
    BrowserUnavailableError,
)
from app.connectors.sources.web.crawl4ai_fetcher import (
    BROWSER_CRASH_ERROR,
    Crawl4AIFetcher,
    browser_page_slots,
    get_shared_fetcher,
    release_shared_fetcher,
)
from app.connectors.sources.web.fetch_strategy import PIPESHUB_USER_AGENT
from app.services.resource_governor.models import ResourceSnapshot

GB = 1024 * 1024 * 1024


def _pages(browser: FakeWeb, *names: str) -> list[str]:
    urls = [f"http://site.test/{name}" for name in names]
    for url in urls:
        browser.html(url, url)
    return urls


def _fast_deadlines(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Shrink every page budget so a stuck page times out in well under a second."""
    monkeypatch.setattr(crawl4ai_fetcher, "BODY_VISIBILITY_TIMEOUT_MS", 50)
    monkeypatch.setattr(crawl4ai_fetcher, "_DEADLINE_MARGIN_SECONDS", 0.1)
    monkeypatch.setattr(browser_supervisor, "_LIVENESS_TIMEOUT_SECONDS", 0.2)
    return {"page_timeout": 50, "wait_for_timeout": 50, "delay_before_return_html": 0}


# -- Crashes (FETCH-01, FETCH-07; RS-11) ------------------------------------------------


async def test_a_page_that_crashes_the_browser_twice_is_given_up_and_its_batch_mates_load(browser: FakeWeb) -> None:
    urls = _pages(browser, "a", "b", "c")
    browser.add("http://site.test/boom", Page(body=html_page("Boom"), kills_browser=True))

    async with Crawl4AIFetcher(concurrency=1) as fetcher:
        results = await fetcher.fetch_many([urls[0], "http://site.test/boom", *urls[1:]])
        after = await fetcher.fetch(urls[0])

    assert [r.success for r in results] == [True, False, True, True]
    assert results[1].crashed_browser is True
    assert results[1].error == BROWSER_CRASH_ERROR
    assert not any(r.crashed_browser for r in (results[0], *results[2:]))
    assert after.success is True


async def test_a_page_that_crashed_the_browser_once_loads_on_its_retry(browser: FakeWeb) -> None:
    browser.add("http://site.test/once", [Page(body=html_page("Once"), kills_browser=True), Page(body=html_page("Once"))])

    async with Crawl4AIFetcher() as fetcher:
        result = await fetcher.fetch("http://site.test/once")

    assert result.success is True
    assert result.crashed_browser is False
    assert browser.browser_starts == 2


async def test_a_page_closed_by_another_callers_relaunch_is_fetched_again(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser.add("http://site.test/killer", [Page(body=html_page("Killer"), kills_browser=True), Page(body=html_page("K"))])
    [victim] = _pages(browser, "victim")
    base = browser_crawler_class(browser)

    class ClosedByRelaunch(base):  # type: ignore[misc,valid-type]
        async def arun(self, url: str, config: object = None, **kw: object) -> object:
            if url == victim and self.launch == 1:
                while browser.browser_starts < 2:  # noqa: ASYNC110 - the fake browser has no event to wait on
                    await asyncio.sleep(0.01)
                # The relaunch closed this page; the new browser is connected, so only the generation tells.
                return SimpleNamespace(url=url, redirected_url=url, html="", success=False, status_code=None,
                                       error_message="Page.goto: Target page, context or browser has been closed",
                                       crawl_stats=None, js_execution_result=None)
            return await super().arun(url, config, **kw)

    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", ClosedByRelaunch)

    async with Crawl4AIFetcher(concurrency=2) as fetcher:
        killer, victim_result = await asyncio.gather(fetcher.fetch("http://site.test/killer"), fetcher.fetch(victim))

    assert killer.success is True
    assert victim_result.success is True
    assert browser.browser_starts == 2


def test_has_been_closed_counts_as_a_death_only_once_the_generation_moved_or_the_browser_is_gone() -> None:
    connected = {"up": True}

    async def launch() -> object:
        return object()

    async def teardown(_: object) -> None:
        return None

    supervisor = BrowserSupervisor(launch, teardown, lambda _: connected["up"])
    supervisor._browser = object()
    closed = "Target page, context or browser has been closed"

    assert supervisor.is_dead_error(closed, started_generation=0) is False
    supervisor.generation = 1
    assert supervisor.is_dead_error(closed, started_generation=0) is True
    assert supervisor.is_dead_error(closed, started_generation=1) is False
    connected["up"] = False
    assert supervisor.is_dead_error(closed, started_generation=1) is True
    assert supervisor.is_dead_error(DRIVER_GONE) is True
    assert supervisor.is_dead_error("net::ERR_NAME_NOT_RESOLVED", started_generation=0) is False


# -- Hung browsers and deadlines (FETCH-03, FETCH-08; RS-12, RS-13, RS-15) ---------------


def _stuck_crawler(browser: FakeWeb, *, stuck: set[str], answers: bool) -> type:
    """A browser whose first launch never finishes ``stuck`` pages; ``answers`` is whether it still replies."""
    base = browser_crawler_class(browser)

    class Session:
        async def send(self, _: str) -> dict:
            if not answers:
                await asyncio.Event().wait()
            return {}

        async def detach(self) -> None:
            return None

    class Browser:
        def is_connected(self) -> bool:
            return True

        async def new_browser_cdp_session(self) -> Session:
            return Session()

    class Stuck(base):  # type: ignore[misc,valid-type]
        crawler_strategy = SimpleNamespace(browser_manager=SimpleNamespace(browser=Browser()))

        async def arun(self, url: str, config: object = None, **kw: object) -> object:
            if url in stuck and self.launch == 1:
                await asyncio.Event().wait()
            return await super().arun(url, config, **kw)

    return Stuck


async def test_a_browser_that_stops_answering_is_relaunched_and_its_stuck_pages_load(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _pages(browser, "a", "b", "c")
    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", _stuck_crawler(browser, stuck=set(urls), answers=False))

    async with Crawl4AIFetcher(concurrency=3, **_fast_deadlines(monkeypatch)) as fetcher:
        results = await asyncio.wait_for(fetcher.fetch_many(urls), timeout=5)

    assert [r.success for r in results] == [True, True, True]
    assert browser.browser_starts == 2


async def test_page_timeouts_on_a_browser_that_still_answers_stay_timeouts(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _pages(browser, "a", "b", "c")
    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", _stuck_crawler(browser, stuck=set(urls), answers=True))

    async with Crawl4AIFetcher(concurrency=3, **_fast_deadlines(monkeypatch)) as fetcher:
        results = await asyncio.wait_for(fetcher.fetch_many(urls), timeout=5)

    assert all(r.timed_out and not r.success for r in results)
    assert "Timed out after" in (results[0].error or "")
    assert browser.browser_starts == 1


async def test_a_stuck_page_times_out_alone_and_its_batch_mates_keep_their_results(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = _pages(browser, *(f"p{i}" for i in range(10)))
    stuck = set(urls[7:])
    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", _stuck_crawler(browser, stuck=stuck, answers=True))

    async with Crawl4AIFetcher(concurrency=10, **_fast_deadlines(monkeypatch)) as fetcher:
        results = await asyncio.wait_for(fetcher.fetch_many(urls), timeout=5)

    assert [r.success for r in results] == [True] * 7 + [False] * 3
    assert [r.timed_out for r in results[7:]] == [True] * 3


def test_the_page_deadline_is_the_navigation_and_wait_budgets_plus_a_margin() -> None:
    fetcher = Crawl4AIFetcher()
    config = fetcher._run_configs[True]

    assert config.page_timeout == 30_000
    assert config.wait_for_timeout == 15_000
    assert config.body_visibility_timeout == 5_000
    assert Crawl4AIFetcher.page_deadline_seconds(config) == pytest.approx(
        30 + 5 + 15 + config.delay_before_return_html + crawl4ai_fetcher._DEADLINE_MARGIN_SECONDS
    )
    assert Crawl4AIFetcher.page_deadline_seconds(
        Crawl4AIFetcher(page_timeout=10_000)._run_configs[False]
    ) == pytest.approx(40.2)


def test_pages_render_with_the_light_config() -> None:
    fetcher = Crawl4AIFetcher()

    for browser_like in (True, False):
        run, launched = fetcher._run_configs[browser_like], fetcher._browser_configs[browser_like]
        assert run.prefetch is True
        assert (launched.light_mode, launched.avoid_ads, launched.java_script_enabled) == (True, True, True)
        assert (launched.viewport_width, launched.viewport_height) == (1366, 900)


async def test_the_first_browser_launch_has_a_deadline(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_supervisor, "_LAUNCH_TIMEOUT_SECONDS", 0.1)
    base = browser_crawler_class(browser)

    class NeverStarts(base):  # type: ignore[misc,valid-type]
        async def start(self) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", NeverStarts)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(get_shared_fetcher(), timeout=5)
    assert crawl4ai_fetcher._shared_instance is None


# -- Identity and isolation (FETCH-13, G1, G3; RS-14) ------------------------------------


async def test_each_identity_gets_its_own_browser_and_profile(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    [url] = _pages(browser, "a")
    strategies: list[dict] = []
    seen: list[tuple[bool, bool]] = []
    base = browser_crawler_class(browser)

    class Recording(base):  # type: ignore[misc,valid-type]
        async def arun(self, url: str, config: object = None, **kw: object) -> object:
            seen.append((config.magic, config.override_navigator))  # type: ignore[attr-defined]
            return await super().arun(url, config, **kw)

    def strategy(**kw: object) -> SimpleNamespace:
        strategies.append(kw)
        return SimpleNamespace(**kw, set_hook=lambda *_: None)

    monkeypatch.setattr(crawl4ai_fetcher, "AsyncWebCrawler", Recording)
    monkeypatch.setattr(crawl4ai_fetcher, "AsyncPlaywrightCrawlerStrategy", strategy)

    fetcher = await get_shared_fetcher(browser_like=False)
    try:
        assert browser.browser_starts == 1
        assert (await fetcher.fetch(url, browser_like=False)).success
        assert (await fetcher.fetch_many([url], browser_like=True))[0].success
    finally:
        await release_shared_fetcher()

    assert browser.browser_starts == 2
    honest, browser_like = strategies
    assert "browser_adapter" not in honest
    assert honest["browser_config"].user_agent == PIPESHUB_USER_AGENT
    assert "browser_adapter" in browser_like
    assert seen == [(False, False), (True, True)]


async def test_an_identity_whose_browser_cannot_start_is_reported_unavailable(
    browser: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    [url] = _pages(browser, "a")

    async with Crawl4AIFetcher() as fetcher:
        browser.browser_broken = True
        with pytest.raises(BrowserUnavailableError, match="could not be started"):
            await fetcher.fetch(url, browser_like=False)
        browser.browser_broken = False
        assert (await fetcher.fetch(url, browser_like=False)).success


async def test_pages_of_different_jobs_get_different_browser_contexts(browser: FakeWeb) -> None:
    async with Crawl4AIFetcher() as fetcher:
        def signature(key: str | None) -> str:
            config = fetcher._config_for(browser_like=False, context_key=key)
            # crawl4ai's own key for which BrowserContext (cookies, storage, cache) a page opens in.
            return BrowserManager._make_config_signature(SimpleNamespace(_browser_version=0), config)

        assert signature("connector-1:run-1") == signature("connector-1:run-1")
        assert signature("connector-1:run-1") != signature("connector-2:run-1")
        assert signature("connector-1:run-1") != signature(None)
        assert signature(None) == signature(None)


# -- Resources (FETCH-11; RS-01..RS-04) -------------------------------------------------


def _snapshot(limit_gb: float | None, used_gb: float, cpus: float) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=cpus, cpu_utilisation=None, cpu_throttled_ratio=None, cpu_pressure=None,
        mem_limit_bytes=None if limit_gb is None else int(limit_gb * GB),
        mem_working_set_bytes=int(used_gb * GB), source="test",
    )


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        pytest.param(_snapshot(2, 0.2, 2), 2, id="2GB-2CPU"),
        pytest.param(_snapshot(2, 1.5, 2), 1, id="2GB-mostly-used"),
        pytest.param(_snapshot(64, 4, 32), 8, id="64GB-32CPU-capped"),
        pytest.param(_snapshot(64, 4, 3), 3, id="cpu-bound"),
        pytest.param(_snapshot(None, 0, 16), 4, id="memory-unknown"),
    ],
)
def test_page_slots_follow_free_memory_and_cpus(snapshot: ResourceSnapshot, expected: int) -> None:
    assert browser_page_slots(snapshot) == expected


def test_an_operator_override_wins_over_the_computed_slots() -> None:
    assert browser_page_slots(_snapshot(2, 0.2, 2), override=12) == 12


async def test_the_shared_fetcher_is_sized_from_the_container(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_BROWSER_MAX_PAGES")
    monkeypatch.delenv("CRAWL4AI_CONCURRENCY", raising=False)
    monkeypatch.setattr(crawl4ai_fetcher, "build_probe", lambda: FakeMemoryProbe(limit_mb=2048, used_mb=200, cpus=2))

    fetcher = await get_shared_fetcher()
    try:
        assert fetcher.concurrency == 2
    finally:
        await release_shared_fetcher()

    monkeypatch.setenv("WEB_BROWSER_MAX_PAGES", "6")
    fetcher = await get_shared_fetcher()
    try:
        assert fetcher.concurrency == 6
    finally:
        await release_shared_fetcher()


async def test_a_new_tab_waits_for_free_memory(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    [url] = _pages(browser, "a")
    monkeypatch.setattr(crawl4ai_fetcher, "_MEMORY_CHECK_INTERVAL_SECONDS", 0.01)
    probe = FakeMemoryProbe(limit_mb=2048, used_mb=1900)

    async with Crawl4AIFetcher(memory_probe=probe, min_free_memory_mb=300) as fetcher:
        pending = asyncio.ensure_future(fetcher.fetch(url))
        await asyncio.sleep(0.1)
        assert not pending.done() and browser.browser_visits == []
        probe.used_mb = 1000
        assert (await asyncio.wait_for(pending, timeout=5)).success


async def test_the_memory_wait_is_bounded(browser: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    [url] = _pages(browser, "a")
    monkeypatch.setattr(crawl4ai_fetcher, "_MEMORY_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(crawl4ai_fetcher, "_MEMORY_WAIT_SECONDS", 0.1)

    async with Crawl4AIFetcher(memory_probe=FakeMemoryProbe(limit_mb=2048, used_mb=2000)) as fetcher:
        assert (await asyncio.wait_for(fetcher.fetch(url), timeout=5)).success


async def test_the_liveness_check_ends_when_a_hung_browser_ignores_the_detach_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review finding: the check's detach waited on the hung browser, so its timeout never fired and
    the supervisor lock stayed held, blocking every later relaunch."""
    monkeypatch.setattr(browser_supervisor, "_LIVENESS_TIMEOUT_SECONDS", 0.2)

    class HungSession:
        async def send(self, *_: object) -> None:
            await asyncio.Event().wait()

        async def detach(self) -> None:
            await asyncio.Event().wait()

    class HungBrowser:
        def is_connected(self) -> bool:
            return True

        async def new_browser_cdp_session(self) -> HungSession:
            return HungSession()

    crawler = SimpleNamespace(crawler_strategy=SimpleNamespace(browser_manager=SimpleNamespace(browser=HungBrowser())))
    relaunches: list[object] = []

    async def launch() -> object:
        relaunches.append(crawler)
        return crawler

    async def teardown(_: object) -> None:
        return None

    supervisor = BrowserSupervisor(launch, teardown, lambda _: True, is_responsive=crawl4ai_fetcher._crawler_responds)
    await supervisor.start()

    assert await asyncio.wait_for(supervisor.recover_if_hung(supervisor.generation), 5) is True
    assert len(relaunches) == 2
