import asyncio
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Coroutine, Optional, TypeVar, Union

T = TypeVar("T")

_HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.async_dispatcher import SemaphoreDispatcher
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.browser_adapter import UndetectedAdapter


class _SharedSemaphoreDispatcher(SemaphoreDispatcher):
    """SemaphoreDispatcher that uses an externally-owned semaphore.

    The base class creates a new asyncio.Semaphore in every run_urls call,
    so concurrent fetch_many invocations each get independent limits.
    This subclass accepts a pre-existing semaphore so all calls share
    the same concurrency gate.
    """

    def __init__(self, semaphore: asyncio.Semaphore, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._shared_semaphore = semaphore

    async def run_urls(self, crawler: AsyncWebCrawler, urls: list[str], config: CrawlerRunConfig) -> list[Any]:
        self.crawler = crawler
        if self.monitor:
            self.monitor.start()
        try:
            tasks = []
            for url in urls:
                task_id = str(uuid.uuid4())
                if self.monitor:
                    self.monitor.add_task(task_id, url)
                task = asyncio.create_task(
                    self.crawl_url(url, config, task_id, self._shared_semaphore)
                )
                tasks.append(task)
            return await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self.monitor:
                self.monitor.stop()

@dataclass
class FetchResult:
    url: str
    html: Optional[str] = None
    success: bool = False
    status_code: Optional[int] = None
    error: Optional[str] = None
    js_execution_result: Optional[dict[str, Any]] = None


def resolve_fetch_status_code(
    status_code: Optional[int],
    error_message: Optional[str] = None,
    crawl_stats: Optional[dict[str, Any]] = None,
) -> Optional[int]:
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


class Crawl4AIFetcher:
    """Async fetcher for client-side rendered pages using crawl4ai.

    Runs the browser in a dedicated background thread with its own event loop
    to avoid blocking or conflicting with the main application event loop.
    Supports concurrent fetching with rate limiting and anti-bot measures.
    """

    _CSR_MONITOR_JS = (
        "(() => {"
        "  const w = window;"
        "  if (w.__csrMon) return;"
        "  w.__csrMon = { reqs: 0, lastDone: 0, lastMut: 0, hasMut: false };"
        "  const _f = window.fetch;"
        "  window.fetch = function() {"
        "    w.__csrMon.reqs++;"
        "    return _f.apply(this, arguments).then("
        "      r => { w.__csrMon.reqs--; w.__csrMon.lastDone = Date.now(); return r; },"
        "      e => { w.__csrMon.reqs--; w.__csrMon.lastDone = Date.now(); throw e; }"
        "    );"
        "  };"
        "  const _s = XMLHttpRequest.prototype.send;"
        "  XMLHttpRequest.prototype.send = function() {"
        "    w.__csrMon.reqs++;"
        "    this.addEventListener('loadend',"
        "      () => { w.__csrMon.reqs--; w.__csrMon.lastDone = Date.now(); },"
        "      { once: true });"
        "    return _s.apply(this, arguments);"
        "  };"
        "  try {"
        "    new PerformanceObserver(list => {"
        "      for (const e of list.getEntries()) {"
        "        if (e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')"
        "          w.__csrMon.lastDone = Date.now();"
        "      }"
        "    }).observe({ type: 'resource', buffered: false });"
        "  } catch(e) {}"
        "  new MutationObserver(ms => {"
        "    for (const m of ms) {"
        "      if (m.type !== 'childList') continue;"
        "      for (const n of m.addedNodes) {"
        "        if (n.nodeType === 1 && (n.textContent || '').trim().length > 30) {"
        "          w.__csrMon.lastMut = Date.now();"
        "          w.__csrMon.hasMut = true;"
        "          return;"
        "        }"
        "      }"
        "    }"
        "  }).observe(document.body, { childList: true, subtree: true });"
        "})()"
    )

    _CONTENT_STABLE_JS = (
        "js:() => {"
        "  const w = window;"
        "  const now = Date.now();"
        "  if (!w.__vstab_t0) w.__vstab_t0 = now;"
        "  const elapsed = now - w.__vstab_t0;"
        "  function textLen(root) {"
        "    if (!root) return 0;"
        "    return (root.innerText || '').trim().length;"
        "  }"
        "  const main = document.querySelector("
        "    'main,[role=main],article,[role=article],"
        "    .content,.content-body,.article-body,.post-content'"
        "  );"
        "  const len = main ? textLen(main) : textLen(document.body);"
        "  if (!w.__vstab || len !== w.__vstab) {"
        "    w.__vstab = len;"
        "    w.__vstab_ts = now;"
        "    return false;"
        "  }"
        "  const stable = now - w.__vstab_ts;"
        "  if (main && len >= 100 && stable >= 800) return true;"
        "  const m = w.__csrMon;"
        "  if (m) {"
        "    if (m.reqs > 0 && elapsed < 10000) return false;"
        "    if (m.lastDone > 0 && (now - m.lastDone) < 1500 && elapsed < 15000) return false;"
        "    if (m.hasMut && (now - m.lastMut) < 1500 && elapsed < 15000) return false;"
        "  }"
        "  if (len >= 100 && stable >= 800) return true;"
        "  if (elapsed >= 15000 && len > 0 && stable >= 500) return true;"
        "  return false;"
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
        page_timeout: int = 15000,
        wait_for: Optional[str] = None,
        delay_before_return_html: float = 1,
        simulate_user: bool = False,
        max_retries: int = 0,
        viewport: tuple[int, int] = (1920, 1080),
        js_code: Optional[Union[str, list[str]]] = REVEAL_ALL_TABS_JS,
        js_code_before_wait: Optional[Union[str, list[str]]] = None,
        init_scripts: Optional[list[str]] = None,
    ) -> None:
        self._browser_config = BrowserConfig(
            headless=headless,
            enable_stealth=stealth,
            light_mode=False,
            viewport_width=viewport[0],
            viewport_height=viewport[1],
            java_script_enabled=True,
            **({"init_scripts": init_scripts} if init_scripts else {}),
        )
        before_wait = [self._CSR_MONITOR_JS]
        if js_code_before_wait:
            if isinstance(js_code_before_wait, list):
                before_wait.extend(js_code_before_wait)
            else:
                before_wait.append(js_code_before_wait)

        self._run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_until="domcontentloaded",
            wait_for=wait_for or self._CONTENT_STABLE_JS,
            delay_before_return_html=delay_before_return_html,
            js_code=js_code,
            js_code_before_wait=before_wait,
            # page_timeout=page_timeout,
            # simulate_user=simulate_user,
            override_navigator=True,
            max_retries=max_retries,
            semaphore_count=concurrency,
            # scan_full_page=False,
            user_agent_mode="random",
            remove_overlay_elements=True,
            remove_consent_popups=True,
            magic=True,
        )
        self._concurrency = concurrency
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._crawler: Optional[AsyncWebCrawler] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    async def start(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        started = threading.Event()

        def _run_loop():
            asyncio.set_event_loop(loop)
            started.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._thread = threading.Thread(target=_run_loop, daemon=True, name="crawl4ai-browser")
        self._thread.start()
        started.wait()

        self._crawler = await self._run_in_browser_thread(self._create_and_start_crawler())
        self._semaphore = await self._run_in_browser_thread(self._create_semaphore())

    async def _create_semaphore(self) -> asyncio.Semaphore:
        return asyncio.Semaphore(self._concurrency)

    async def _create_and_start_crawler(self) -> AsyncWebCrawler:
        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=self._browser_config,
            browser_adapter=UndetectedAdapter(),
        )
        crawler = AsyncWebCrawler(crawler_strategy=strategy)
        await crawler.start()
        return crawler

    async def _run_in_browser_thread(self, coro: Coroutine[Any, Any, T]) -> T:
        """Schedule a coroutine on the browser thread's loop and await the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    async def close(self):
        if self._crawler and self._loop:
            await self._run_in_browser_thread(self._crawler.close())
            self._crawler = None
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None
        if self._thread:
            thread = self._thread
            self._thread = None
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: thread.join(timeout=5)
            )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def fetch(
        self,
        url: str,
        *,
        wait_until: Optional[str] = None,
        wait_for: Optional[str] = None,
        js_code: Optional[Union[str, list[str]]] = None,
        js_code_before_wait: Optional[Union[str, list[str]]] = None,
    ) -> FetchResult:
        """Fetch a single URL, returning rendered HTML."""
        if not self._crawler or not self._loop:
            raise RuntimeError("Fetcher not started. Use 'async with' or call start().")

        overrides = {}
        if wait_until:
            overrides["wait_until"] = wait_until
        if wait_for:
            overrides["wait_for"] = wait_for
        if js_code is not None:
            overrides["js_code"] = js_code
        if js_code_before_wait is not None:
            overrides["js_code_before_wait"] = js_code_before_wait
        config = self._run_config.clone(**overrides) if overrides else self._run_config

        return await self._run_in_browser_thread(self._do_fetch(url, config))

    async def _do_fetch(self, url: str, config: CrawlerRunConfig) -> FetchResult:
        page_timeout_s = (config.page_timeout or 15000) / 1000
        timeout = page_timeout_s * 2 + 10

        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self._crawler.arun(url, config=config),
                    timeout=timeout,
                )
            js_result = None
            raw = result.js_execution_result
            if isinstance(raw, dict) and raw.get("success"):
                results_list = raw.get("results") or []
                if results_list and isinstance(results_list[0], dict):
                    js_result = results_list[0]
            return FetchResult(
                url=url,
                html=result.html,
                success=result.success,
                status_code=resolve_fetch_status_code(
                    result.status_code,
                    result.error_message,
                    result.crawl_stats,
                ),
                error=result.error_message,
                js_execution_result=js_result,
            )
        except asyncio.TimeoutError:
            return FetchResult(url=url, error=f"Timed out after {timeout:.0f}s", success=False)
        except Exception as e:
            return FetchResult(url=url, error=str(e), success=False)

    async def fetch_many(
        self,
        urls: list[str],
        *,
        wait_for: Optional[str] = None,
        js_code: Optional[Union[str, list[str]]] = None,
        js_code_before_wait: Optional[Union[str, list[str]]] = None,
    ) -> list[FetchResult]:
        """Fetch multiple URLs concurrently with rate limiting."""
        if not self._crawler or not self._loop:
            raise RuntimeError("Fetcher not started. Use 'async with' or call start().")

        overrides = {}
        if wait_for:
            overrides["wait_for"] = wait_for
        if js_code is not None:
            overrides["js_code"] = js_code
        if js_code_before_wait is not None:
            overrides["js_code_before_wait"] = js_code_before_wait
        config = self._run_config.clone(**overrides) if overrides else self._run_config

        return await self._run_in_browser_thread(self._do_fetch_many(urls, config))

    async def _do_fetch_many(self, urls: list[str], config: CrawlerRunConfig) -> list[FetchResult]:
        page_timeout_s = (config.page_timeout or 15000) / 1000
        batch_timeout = math.ceil(len(urls) / self._concurrency) * page_timeout_s * 2 + 30

        dispatcher = _SharedSemaphoreDispatcher(
            semaphore=self._semaphore,
            semaphore_count=self._concurrency,
        )

        try:
            results = await asyncio.wait_for(
                self._crawler.arun_many(urls, config=config, dispatcher=dispatcher),
                timeout=batch_timeout,
            )
            out: list[FetchResult] = []
            for r, u in zip(results, urls):
                if isinstance(r, BaseException):
                    out.append(FetchResult(url=u, error=str(r), success=False))
                else:
                    out.append(FetchResult(
                        url=r.url,
                        html=r.html,
                        success=r.success,
                        status_code=resolve_fetch_status_code(
                            r.status_code,
                            r.error_message,
                            r.crawl_stats,
                        ),
                        error=r.error_message,
                    ))
            return out
        except asyncio.TimeoutError:
            return [FetchResult(url=u, error=f"Batch timed out after {batch_timeout:.0f}s") for u in urls]
        except Exception as e:
            return [FetchResult(url=u, error=str(e)) for u in urls]


# ---------------------------------------------------------------------------
# Process-wide shared fetcher
# ---------------------------------------------------------------------------

_shared_instance: Optional[Crawl4AIFetcher] = None
_ref_count: int = 0
_shared_lock: Optional[asyncio.Lock] = None


def _get_shared_lock() -> asyncio.Lock:
    global _shared_lock
    if _shared_lock is None:
        _shared_lock = asyncio.Lock()
    return _shared_lock


async def get_shared_fetcher() -> Crawl4AIFetcher:
    """Return the process-wide Crawl4AIFetcher, creating it on first call.

    Concurrency is controlled by the CRAWL4AI_CONCURRENCY env var (default 5).
    Each caller must pair this with a call to release_shared_fetcher() when done.
    """
    global _shared_instance, _ref_count
    async with _get_shared_lock():
        if _shared_instance is None:
            concurrency = int(os.environ.get("CRAWL4AI_CONCURRENCY", "5"))
            _shared_instance = Crawl4AIFetcher(concurrency=concurrency)
            try:
                await _shared_instance.start()
            except Exception:
                _shared_instance = None
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
