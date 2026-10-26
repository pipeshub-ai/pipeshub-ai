import asyncio
import contextlib
import logging
import re
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar
from urllib.parse import urlsplit

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    CrawlResult,
    ProxyConfig,
)
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.browser_adapter import UndetectedAdapter
from playwright.async_api import Page, Route

from app.connectors.sources.web.address_guard import start_guard_proxy
from app.connectors.sources.web.browser_supervisor import (
    BrowserSupervisor,
    BrowserUnavailableError,
)
from app.connectors.sources.web.fetch_strategy import PIPESHUB_USER_AGENT
from app.services.resource_governor.models import ResourceSnapshot
from app.services.resource_governor.probe import ResourceProbe, build_probe
from app.utils.env_config import env_int

T = TypeVar("T")

logger = logging.getLogger(__name__)

_HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")

PAGE_TIMEOUT_MS = 30_000
# How long the readiness check may wait; an empty page then returns what it has instead of failing.
WAIT_FOR_TIMEOUT_MS = 15_000
# crawl4ai waits this long for a hidden <body> (ng-cloak, v-cloak); its own default is 30 s.
BODY_VISIBILITY_TIMEOUT_MS = 5_000
# Covers the tab-reveal script, overlay removal and HTML capture after the waits above.
_DEADLINE_MARGIN_SECONDS = 10.0

_MB = 1024 * 1024
_TAB_BYTES = 400 * _MB
# Left for everything else in the container (the connector service itself, parsers, other services).
_RESERVE_BYTES = 1024 * _MB
_MAX_PAGES_CAP = 8
_UNKNOWN_MEMORY_PAGES = 4
_MIN_FREE_BYTES_DEFAULT_MB = 300
_MEMORY_WAIT_SECONDS = 30
_MEMORY_CHECK_INTERVAL_SECONDS = 1.0

# Never change a page's text, and cost most of its load time: images, media, fonts, and analytics,
# consent and chat widgets. Stylesheets and scripts stay; pages need them to render their content.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
_BLOCKED_HOSTS = (
    "cookielaw.org", "onetrust.com", "qualtrics.com", "google-analytics.com", "googletagmanager.com",
    "doubleclick.net", "hotjar.com", "segment.io", "demdex.net", "omtrdc.net", "adobedtm.com", "dimelo.com",
)

_THIN_PAGE_VERDICTS = (
    "Blocked by anti-bot protection: Structural",
    "Blocked by anti-bot protection: Near-empty content",
)

BROWSER_CRASH_ERROR = "browser_crash: the browser crashed twice while loading this page"


def _available_bytes(snapshot: ResourceSnapshot) -> int | None:
    used = snapshot.mem_working_set_raw_bytes
    if used is None:
        used = snapshot.mem_working_set_bytes
    if snapshot.mem_limit_bytes is None or used is None:
        return None
    return max(0, snapshot.mem_limit_bytes - used)


def browser_page_slots(snapshot: ResourceSnapshot, override: int = 0) -> int:
    """Pages the browser may render at once: about one per 400 MB free beyond a reserve, at most one per CPU.

    ``override`` > 0 (an operator's setting) wins over the computed value.
    """
    if override > 0:
        return override
    by_cpu = max(1, int(snapshot.cpu_quota))
    available = _available_bytes(snapshot)
    by_memory = _UNKNOWN_MEMORY_PAGES if available is None else (available - _RESERVE_BYTES) // _TAB_BYTES
    return max(1, min(by_cpu, by_memory, _MAX_PAGES_CAP))


@dataclass
class FetchResult:
    url: str
    html: str | None = None
    success: bool = False
    status_code: int | None = None
    error: str | None = None
    js_execution_result: dict[str, Any] | None = None
    content_type: str | None = None  # of the response the browser loaded, when it reports one
    # The page response's headers the crawl acts on (KEPT_HEADERS), as the site sent them.
    headers: dict[str, str] = field(default_factory=dict)
    timed_out: bool = False
    # The page took the browser down twice; it is given up on so the crawl can go on (``failed:browser_crash``).
    crashed_browser: bool = False


# Headers of the page's own response that decide what is indexed and when it changed.
KEPT_HEADERS = frozenset({"content-type", "x-robots-tag", "etag", "last-modified"})


def _kept_headers(response_headers: object) -> dict[str, str]:
    if not isinstance(response_headers, dict):
        return {}
    return {str(k): str(v) for k, v in response_headers.items() if str(k).lower() in KEPT_HEADERS}


def _content_type(response_headers: object) -> str | None:
    if not isinstance(response_headers, dict):
        return None
    return next((str(v) for k, v in response_headers.items() if str(k).lower() == "content-type"), None)


def _crawler_connected(crawler: AsyncWebCrawler) -> bool:
    # crawl4ai has no liveness check of its own, so this reads the Playwright browser it wraps.
    return crawler.crawler_strategy.browser_manager.browser.is_connected()


async def _crawler_responds(crawler: AsyncWebCrawler) -> bool:
    """A round trip to the browser process: a hung browser stays "connected" but never answers."""
    try:
        browser = crawler.crawler_strategy.browser_manager.browser
    except AttributeError:
        return True  # nothing to ask, so nothing proves it hung
    session = await browser.new_browser_cdp_session()
    try:
        await session.send("Browser.getVersion")
    finally:
        # Bounded: a hung browser doesn't answer the detach either, and the caller's timeout would wait on it.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.detach(), timeout=2)
    return True


def _blocked_request(url: str, resource_type: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return resource_type in _BLOCKED_RESOURCE_TYPES or any(host == h or host.endswith("." + h) for h in _BLOCKED_HOSTS)


async def _block_heavy_requests(route: Route) -> None:
    request = route.request
    # shortcut: a page's own subresources on a listed host are blocked too; fine until someone crawls those sites.
    if not request.is_navigation_request() and _blocked_request(request.url, request.resource_type):
        await route.abort()
    else:
        await route.fallback()


async def _route_page(page: Page, **_: object) -> Page:
    await page.route("**/*", _block_heavy_requests)
    return page


def _consume_abandoned(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


def resolve_fetch_status_code(
    status_code: int | None,
    error_message: str | None = None,
    crawl_stats: dict[str, Any] | None = None,
) -> int | None:
    """Resolve the best available HTTP status from a crawl4ai result."""
    if status_code:
        return status_code

    if crawl_stats:
        for attempt in reversed(crawl_stats.get("proxies_used", [])):
            attempt_code = attempt.get("status_code")
            if attempt_code:
                return attempt_code

    if error_message:
        match = _HTTP_STATUS_RE.search(error_message)
        if match:
            return int(match.group(1))

    return None


def _to_fetch_result(result: CrawlResult, url: str) -> FetchResult:
    js_result = None
    raw = result.js_execution_result
    if isinstance(raw, dict) and raw.get("success"):
        results_list = raw.get("results") or []
        if results_list and isinstance(results_list[0], dict):
            js_result = results_list[0]
    # crawl4ai calls a 2xx page with little text "blocked"; it is just a thin page, and the connector judges it.
    thin = bool(result.html) and 200 <= (result.status_code or 0) < 300 and (
        (result.error_message or "").startswith(_THIN_PAGE_VERDICTS)
    )
    return FetchResult(
        # crawl4ai's ``url`` is the one asked for; the connector needs where it landed.
        url=result.redirected_url or url,
        html=result.html,
        success=result.success or thin,
        status_code=resolve_fetch_status_code(result.status_code, result.error_message, result.crawl_stats),
        error=None if thin else result.error_message,
        js_execution_result=js_result,
        content_type=_content_type(getattr(result, "response_headers", None)),
        headers=_kept_headers(getattr(result, "response_headers", None)),
    )


class Crawl4AIFetcher:
    """Async fetcher for client-side rendered pages using crawl4ai.

    Runs the browser in a dedicated background thread with its own event loop
    to avoid blocking or conflicting with the main application event loop.
    Supports concurrent fetching with rate limiting and anti-bot measures.
    """

    # An init script, so it runs in the page's own JS world before any page script: a fetch the app
    # starts while loading is counted, which a script injected after DOMContentLoaded would miss.
    _CSR_MONITOR_JS = """
(() => {
  if (window.__csrMon) return;
  const m = window.__csrMon = { reqs: 0, lastActivity: Date.now() };
  const touch = () => { m.lastActivity = Date.now(); };
  const done = () => { m.reqs = Math.max(0, m.reqs - 1); touch(); };
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function () {
      m.reqs++; touch();
      return origFetch.apply(this, arguments).then(r => { done(); return r; }, e => { done(); throw e; });
    };
  }
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    m.reqs++; touch();
    this.addEventListener('loadend', done, { once: true });
    return origSend.apply(this, arguments);
  };
  try { new PerformanceObserver(touch).observe({ type: 'resource', buffered: false }); } catch (e) {}
  new MutationObserver(ms => {
    for (const mu of ms) for (const n of mu.addedNodes) {
      if (n.nodeType === 1 && (n.textContent || '').trim().length > 30) { touch(); return; }
    }
  }).observe(document, { childList: true, subtree: true });
})();
"""

    # Ready once the network has been quiet for 500 ms (no fetch/XHR in flight, no resource or content
    # arriving) and the text then holds still. "window.__" keeps it in the page's world under Patchright.
    # wait_for_timeout caps it, and crawl4ai then returns the page as it is.
    _CONTENT_STABLE_JS = (
        "js:() => {"
        "  const now = Date.now();"
        "  if (!window.__vstab_t0) window.__vstab_t0 = now;"
        "  const m = window.__csrMon;"
        "  if (m && (m.reqs > 0 || now - m.lastActivity < 500)) return false;"
        "  const len = ((document.body && document.body.innerText) || '').trim().length;"
        "  if (window.__vstab !== len) { window.__vstab = len; window.__vstab_ts = now; return false; }"
        "  const stable = now - window.__vstab_ts;"
        "  if (len >= 100 && stable >= 800) return true;"
        "  return now - window.__vstab_t0 >= 5000 && stable >= 800;"
        "}"
    )

    REVEAL_ALL_TABS_JS = """
// crawl4ai wraps this in an async IIFE — top-level await is available.
// Do NOT wrap in (async () => { ... })() or the result is fire-and-forget.

// Step 1: Find tab groups by locating active tab indicators and their siblings.
const __activeSel = [
  '[role="tab"][aria-selected="true"]',
  '.nav-tabs .active, .nav-pills .active',
  '[class*="tab"][class*="active"]',
  '[class*="tabs-button"][class*="active"]',
];
const __groups = new Map();
for (const sel of __activeSel) {
  for (const active of document.querySelectorAll(sel)) {
    const container = active.parentElement;
    if (!container || __groups.has(container)) continue;
    const siblings = Array.from(container.children).filter(
      c => (c.tagName === 'BUTTON' || c.tagName === 'A') && c.textContent.trim()
    );
    if (siblings.length >= 2) __groups.set(container, siblings);
  }
}

// Step 2: Find all panel containers that may receive swapped content.
const __panelSel = [
  '[role="tabpanel"]',
  '.tab-pane',
  '[class*="tab-content"] > div',
  '[class*="pills-content"] > div',
  '[id^="tab-pills"]',
  '[class*="secondary-content"]',
];
const __panels = new Set();
for (const sel of __panelSel) {
  for (const p of document.querySelectorAll(sel)) __panels.add(p);
}

// Step 3: For each tab group, click every tab, snapshot panel content,
// then restore all snapshots so content from every tab coexists in the DOM.
const __saved = new Map();
for (const p of __panels) {
  if (p.innerHTML.trim().length > 50) __saved.set(p, p.innerHTML);
}

for (const [, tabs] of __groups) {
  for (const tab of tabs) {
    tab.click();
    await new Promise(r => setTimeout(r, 800));
    for (const p of __panels) {
      if (p.innerHTML.trim().length > 50 && !__saved.has(p)) {
        __saved.set(p, p.innerHTML);
      }
    }
  }
}

for (const [panel, html] of __saved) {
  panel.innerHTML = html;
}

// Step 4: Force all panels visible.
for (const p of __panels) {
  p.style.setProperty('display', 'block', 'important');
  p.style.setProperty('visibility', 'visible', 'important');
  p.style.setProperty('opacity', '1', 'important');
  p.style.setProperty('height', 'auto', 'important');
  p.style.setProperty('overflow', 'visible', 'important');
  p.style.setProperty('position', 'static', 'important');
  p.removeAttribute('hidden');
  p.classList.add('show', 'active');
}
"""

    def __init__(
        self,
        *,
        headless: bool = True,
        stealth: bool = True,
        concurrency: int = 5,
        page_timeout: int = PAGE_TIMEOUT_MS,
        wait_for_timeout: int = WAIT_FOR_TIMEOUT_MS,
        wait_for: str | None = None,
        # The readiness wait already holds until the app's requests and text settle.
        delay_before_return_html: float = 0.2,
        max_retries: int = 0,
        viewport: tuple[int, int] = (1366, 900),
        js_code: str | list[str] | None = REVEAL_ALL_TABS_JS,
        js_code_before_wait: str | list[str] | None = None,
        init_scripts: list[str] | None = None,
        recycle_after_pages: int = 0,
        memory_probe: ResourceProbe | None = None,
        min_free_memory_mb: int = _MIN_FREE_BYTES_DEFAULT_MB,
    ) -> None:
        common = {
            "headless": headless,
            # Chrome's background services, extensions and ads cost memory and never change the text.
            "light_mode": True,
            "avoid_ads": True,
            "viewport_width": viewport[0],
            "viewport_height": viewport[1],
            "java_script_enabled": True,
            "init_scripts": [self._CSR_MONITOR_JS, *(init_scripts or [])],
        }
        # Keyed by ``browser_like``: each identity is its own browser, so neither can change the other's User-Agent.
        self._browser_configs = {
            True: BrowserConfig(enable_stealth=stealth, **common),
            False: BrowserConfig(user_agent=PIPESHUB_USER_AGENT, **common),
        }
        honest = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_until="domcontentloaded",
            wait_for=wait_for or self._CONTENT_STABLE_JS,
            wait_for_timeout=wait_for_timeout,
            page_timeout=page_timeout,
            body_visibility_timeout=BODY_VISIBILITY_TIMEOUT_MS,
            # Only html, status, headers and the script results are read: skip crawl4ai's markdown and scraping.
            prefetch=True,
            delay_before_return_html=delay_before_return_html,
            js_code=js_code,
            js_code_before_wait=js_code_before_wait,
            max_retries=max_retries,
            remove_overlay_elements=True,
            remove_consent_popups=True,
            verbose=False,
        )
        self._run_configs = {
            True: honest.clone(override_navigator=True, user_agent_mode="random", magic=True),
            False: honest,
        }
        self._concurrency = concurrency
        self._recycle_after_pages = recycle_after_pages
        self._memory_probe = memory_probe or build_probe()
        self._min_free_bytes = max(0, min_free_memory_mb) * _MB
        self._semaphore: asyncio.Semaphore | None = None
        self._supervisors: dict[bool, BrowserSupervisor[AsyncWebCrawler]] = {}
        self._supervisors_lock: asyncio.Lock | None = None
        self._proxy: asyncio.Server | None = None
        self._proxy_url: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def concurrency(self) -> int:
        return self._concurrency

    async def start(self, *, browser_like: bool = True) -> None:
        """Start the browser thread (once) and the browser for this identity."""
        if self._loop is None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            started = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                started.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._thread = threading.Thread(target=_run_loop, daemon=True, name="crawl4ai-browser")
            self._thread.start()
            started.wait()
            await self._run_in_browser_thread(self._start_proxy())
        await self._run_in_browser_thread(self._supervisor_for(browser_like=browser_like))

    async def _start_proxy(self) -> None:
        # The proxy outlives every relaunch: closing it cancels all other tasks on this loop.
        self._proxy = await start_guard_proxy()
        proxy_port = self._proxy.sockets[0].getsockname()[1]
        self._proxy_url = f"http://127.0.0.1:{proxy_port}"
        # Playwright also sends loopback through the proxy (<-loopback>), so the proxy refuses it.
        for config in self._browser_configs.values():
            config.proxy_config = ProxyConfig(server=self._proxy_url)
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._supervisors_lock = asyncio.Lock()

    async def _supervisor_for(self, *, browser_like: bool) -> BrowserSupervisor[AsyncWebCrawler]:
        """This identity's supervisor, its browser launched the first time it is asked for."""
        async with self._supervisors_lock:
            supervisor = self._supervisors.get(browser_like)
            if supervisor is None:
                supervisor = BrowserSupervisor(
                    partial(self._launch_crawler, browser_like=browser_like),
                    lambda crawler: crawler.close(),
                    _crawler_connected,
                    is_responsive=_crawler_responds,
                    recycle_after_pages=self._recycle_after_pages,
                )
                await supervisor.start()
                self._supervisors[browser_like] = supervisor
            return supervisor

    async def _launch_crawler(self, *, browser_like: bool) -> AsyncWebCrawler:
        config = self._browser_configs[browser_like]
        if browser_like:
            strategy = AsyncPlaywrightCrawlerStrategy(browser_config=config, browser_adapter=UndetectedAdapter())
        else:
            strategy = AsyncPlaywrightCrawlerStrategy(browser_config=config)
        strategy.set_hook("on_page_context_created", _route_page)
        crawler = AsyncWebCrawler(crawler_strategy=strategy)
        await crawler.start()
        return crawler

    async def _close_proxy(self) -> None:
        """Stop the proxy and the browser connections it still relays, before the loop stops."""
        assert self._proxy is not None
        self._proxy.close()
        self._proxy = None
        relays = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for relay in relays:
            relay.cancel()
        if relays:
            # Bounded: a page task a hung browser abandoned may never finish; the loop is stopped next anyway.
            await asyncio.wait(relays, timeout=5)

    async def _run_in_browser_thread(self, coro: Coroutine[Any, Any, T]) -> T:
        """Schedule a coroutine on the browser thread's loop and await the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    async def _stop_browsers(self) -> None:
        supervisors, self._supervisors = list(self._supervisors.values()), {}
        for supervisor in supervisors:
            await supervisor.stop()

    async def close(self) -> None:
        try:
            if self._supervisors and self._loop:
                await self._run_in_browser_thread(self._stop_browsers())
            if self._proxy and self._loop:
                await self._run_in_browser_thread(self._close_proxy())
        finally:
            # Whatever a dead browser does to the steps above, its thread must not outlive the fetcher.
            if self._loop:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None
            if self._thread:
                thread = self._thread
                self._thread = None
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: thread.join(timeout=5)
                )

    async def __aenter__(self) -> "Crawl4AIFetcher":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _config_for(
        self,
        *,
        browser_like: bool,
        context_key: str | None,
        wait_until: str | None = None,
        wait_for: str | None = None,
        js_code: str | list[str] | None = None,
        js_code_before_wait: str | list[str] | None = None,
    ) -> CrawlerRunConfig:
        if not self._loop:
            raise RuntimeError("Fetcher not started. Use 'async with' or call start().")
        overrides: dict[str, Any] = {}
        if wait_until:
            overrides["wait_until"] = wait_until
        if wait_for:
            overrides["wait_for"] = wait_for
        if js_code is not None:
            overrides["js_code"] = js_code
        if js_code_before_wait is not None:
            overrides["js_code_before_wait"] = js_code_before_wait
        if context_key:
            # crawl4ai shares one context (cookies, storage, cache) per run-config signature, and the
            # proxy credentials are part of it; the guard proxy never asks for them, so they only key the context.
            overrides["proxy_config"] = ProxyConfig(server=self._proxy_url, username=context_key, password="-")
        config = self._run_configs[browser_like]
        return config.clone(**overrides) if overrides else config

    async def fetch(
        self,
        url: str,
        *,
        browser_like: bool = True,
        context_key: str | None = None,
        wait_until: str | None = None,
        wait_for: str | None = None,
        js_code: str | list[str] | None = None,
        js_code_before_wait: str | list[str] | None = None,
    ) -> FetchResult:
        """Fetch a single URL, returning rendered HTML.

        ``browser_like=False`` loads it as PipesHub (its own User-Agent, no evasion). Pages that pass
        the same ``context_key`` share cookies and storage, and no other pages do.
        """
        config = self._config_for(
            browser_like=browser_like, context_key=context_key, wait_until=wait_until, wait_for=wait_for,
            js_code=js_code, js_code_before_wait=js_code_before_wait,
        )
        return (await self._run_in_browser_thread(self._fetch_with_recovery([url], config, browser_like=browser_like)))[0]

    async def fetch_many(
        self,
        urls: list[str],
        *,
        browser_like: bool = True,
        context_key: str | None = None,
        wait_for: str | None = None,
        js_code: str | list[str] | None = None,
        js_code_before_wait: str | list[str] | None = None,
    ) -> list[FetchResult]:
        """Fetch several URLs concurrently, one result per URL in order; see ``fetch``."""
        config = self._config_for(
            browser_like=browser_like, context_key=context_key, wait_for=wait_for, js_code=js_code, js_code_before_wait=js_code_before_wait,
        )
        return await self._run_in_browser_thread(self._fetch_with_recovery(urls, config, browser_like=browser_like))

    async def _fetch_with_recovery(
        self, urls: list[str], config: CrawlerRunConfig, *, browser_like: bool,
    ) -> list[FetchResult]:
        """Fetch ``urls``, relaunching the browser if it died or hung under them.

        Pages lost to a death are fetched again one at a time, so a page that takes the browser down
        a second time is given up on (``crashed_browser``) and the others still load. Raises
        ``BrowserUnavailableError`` when the browser can't be brought back, so a dead browser is
        never reported as pages that failed to load.
        """
        try:
            supervisor = await self._supervisor_for(browser_like=browser_like)
        except Exception as e:
            raise BrowserUnavailableError(f"The headless browser could not be started: {e}") from e
        await supervisor.recycle_if_due(self._semaphore, self._concurrency)
        await supervisor.ensure_ready()
        generation = supervisor.generation
        results = await self._fetch_pages(supervisor.browser, urls, config)
        lost = {i for i, result in enumerate(results) if supervisor.is_dead_error(result.error, generation)}
        timed_out = [i for i, result in enumerate(results) if result.timed_out]
        answered = len(results) - len(timed_out) - len(lost)
        if supervisor.note_timeouts(len(timed_out), answered) and await supervisor.recover_if_hung(generation):
            lost.update(timed_out)
        if lost:
            await supervisor.recover(generation)
            for i in sorted(lost):
                results[i] = await self._fetch_alone(supervisor, urls[i], config)
        supervisor.note_pages(len(urls))
        return results

    async def _fetch_alone(
        self, supervisor: BrowserSupervisor[AsyncWebCrawler], url: str, config: CrawlerRunConfig,
    ) -> FetchResult:
        # shortcut: other callers' pages share the browser, so a death here is pinned on this page
        # even when one of theirs caused it; a page then loses one load. Needs a browser per caller to fix.
        generation = supervisor.generation
        [result] = await self._fetch_pages(supervisor.browser, [url], config)
        if not supervisor.is_dead_error(result.error, generation):
            return result
        logger.warning("Giving up on %s: the headless browser crashed twice while loading it", url)
        await supervisor.recover(generation)
        return FetchResult(url=url, error=BROWSER_CRASH_ERROR, crashed_browser=True)

    async def _fetch_pages(self, crawler: AsyncWebCrawler, urls: list[str], config: CrawlerRunConfig) -> list[FetchResult]:
        # One task per page, each with its own deadline, so a stuck page never costs its batch-mates their results.
        return list(await asyncio.gather(*(self._fetch_one(crawler, url, config) for url in urls)))

    @staticmethod
    def page_deadline_seconds(config: CrawlerRunConfig) -> float:
        """How long one page may take once it has a tab: navigation, the two waits, then the scripts."""
        wait = (config.wait_for_timeout or config.page_timeout) if config.wait_for else 0
        budget_ms = config.page_timeout + config.body_visibility_timeout + wait
        return budget_ms / 1000 + (config.delay_before_return_html or 0) + _DEADLINE_MARGIN_SECONDS

    async def _fetch_one(self, crawler: AsyncWebCrawler, url: str, config: CrawlerRunConfig) -> FetchResult:
        deadline = self.page_deadline_seconds(config)
        timed_out = FetchResult(url=url, error=f"Timed out after {deadline:.0f}s", timed_out=True)
        async with self._semaphore:
            await self._wait_for_memory()
            task = asyncio.ensure_future(crawler.arun(url, config=config))
            try:
                done, _ = await asyncio.wait({task}, timeout=deadline)
            except asyncio.CancelledError:
                task.cancel()
                raise
        if not done:
            # Not awaited: a hung browser can block the page's own cleanup, and the caller must not wait on it.
            task.cancel()
            task.add_done_callback(_consume_abandoned)
            return timed_out
        try:
            return _to_fetch_result(task.result(), url)
        except asyncio.TimeoutError:
            return timed_out
        except Exception as e:
            return FetchResult(url=url, error=str(e), success=False)

    async def _wait_for_memory(self) -> None:
        """Hold a new tab back, for a while, when the container is nearly out of memory."""
        loop = asyncio.get_running_loop()
        give_up_at = loop.time() + _MEMORY_WAIT_SECONDS
        while True:
            available = _available_bytes(self._memory_probe.snapshot())
            if available is None or available >= self._min_free_bytes:
                return
            if loop.time() >= give_up_at:
                logger.warning(
                    "Opening a browser tab with only %d MB of memory free, after waiting %ds for more",
                    available // _MB, _MEMORY_WAIT_SECONDS,
                )
                return
            await asyncio.sleep(_MEMORY_CHECK_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Process-wide shared fetcher
# ---------------------------------------------------------------------------

_shared_instance: Crawl4AIFetcher | None = None
_ref_count: int = 0
_shared_lock: asyncio.Lock | None = None


def _get_shared_lock() -> asyncio.Lock:
    global _shared_lock
    if _shared_lock is None:
        _shared_lock = asyncio.Lock()
    return _shared_lock


def _shared_page_slots() -> int:
    override = env_int("WEB_BROWSER_MAX_PAGES", 0) or env_int("CRAWL4AI_CONCURRENCY", 0)
    snapshot = build_probe().snapshot()
    slots = browser_page_slots(snapshot, override)
    logger.info(
        "Headless browser renders up to %d pages at once (memory limit %s MB, CPUs %.1f%s)",
        slots,
        snapshot.mem_limit_bytes // _MB if snapshot.mem_limit_bytes else "unknown",
        snapshot.cpu_quota,
        ", set by WEB_BROWSER_MAX_PAGES" if override > 0 else "",
    )
    return slots


async def get_shared_fetcher(*, browser_like: bool = True) -> Crawl4AIFetcher:
    """Return the process-wide Crawl4AIFetcher, creating it and the browser for this identity on first call.

    Pages rendered at once are sized from the container's memory and CPUs (``browser_page_slots``);
    WEB_BROWSER_MAX_PAGES (or the older CRAWL4AI_CONCURRENCY) overrides that. WEB_BROWSER_MIN_FREE_MB
    (default 300) is the free memory a new tab waits for. CRAWL4AI_RECYCLE_AFTER_PAGES (default 1000,
    0 to disable) is how many pages a browser serves before it is relaunched. Each caller must pair
    this with a call to release_shared_fetcher() when done.
    """
    global _shared_instance, _ref_count
    async with _get_shared_lock():
        created = _shared_instance is None
        if created:
            _shared_instance = Crawl4AIFetcher(
                concurrency=_shared_page_slots(),
                recycle_after_pages=max(0, env_int("CRAWL4AI_RECYCLE_AFTER_PAGES", 1000)),
                min_free_memory_mb=env_int("WEB_BROWSER_MIN_FREE_MB", _MIN_FREE_BYTES_DEFAULT_MB),
            )
        try:
            await _shared_instance.start(browser_like=browser_like)
        except Exception:
            if created:
                instance, _shared_instance = _shared_instance, None
                await instance.close()
            raise
        _ref_count += 1
        return _shared_instance


async def release_shared_fetcher() -> None:
    """Decrement the shared fetcher ref count; close when the last caller releases."""
    global _shared_instance, _ref_count
    async with _get_shared_lock():
        if _ref_count <= 0:
            return
        _ref_count -= 1
        if _ref_count == 0 and _shared_instance is not None:
            try:
                await _shared_instance.close()
            finally:
                _shared_instance = None
