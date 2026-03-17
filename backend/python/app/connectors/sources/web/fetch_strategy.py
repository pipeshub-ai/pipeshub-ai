"""
Multi-strategy URL fetcher with fallback chain for the web connector.

Fallback chain (default):
  1. aiohttp (existing session, cheapest, already async)
  2. curl_cffi with HTTP/2 browser impersonation
  3. curl_cffi with HTTP/1.1 forced
  4. cloudscraper (JS challenge solver)

Optional headless mode (opt-in per connector instance):
  PlaywrightFetcher — headless Chromium via Playwright.
  Recommended for JavaScript-heavy SPAs or Cloudflare-protected sites.

Each strategy shares the same headers but uses different
TLS fingerprints / impersonation profiles.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, List, Optional, Tuple, cast
from urllib.parse import urlparse

import aiohttp

from app.config.constants.http_status_code import HttpStatusCode

# ---------------------------------------------------------------------------
# Unified response wrapper
# ---------------------------------------------------------------------------

MAX_429_RETRIES = 3
REQUEST_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Shared stealth headers
# ---------------------------------------------------------------------------


def build_stealth_headers(url: str, referer: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """Build browser-like headers shared across all strategies."""
    parsed = urlparse(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer or f"{parsed.scheme}://{parsed.netloc}/",
    }
    if extra:
        headers.update(extra)
    return headers


# ---------------------------------------------------------------------------
# curl_cffi profile discovery (done once at import time)
# ---------------------------------------------------------------------------


def _get_supported_profiles() -> list[str]:
    try:
        from curl_cffi.requests import Session
    except ImportError:
        return []

    candidates = [
        "chrome131", "chrome124", "chrome120", "chrome119", "chrome116",
        "chrome110", "chrome107", "chrome104", "chrome101", "chrome100",
        "chrome99", "chrome", "edge101", "edge99",
        "safari17_0", "safari15_5", "safari15_3",
    ]
    supported = []
    for p in candidates:
        try:
            s = Session(impersonate=cast(Any, p))
            s.close()
            supported.append(p)
        except Exception:
            continue
    return supported


_CURL_PROFILES: list = _get_supported_profiles()

# ---------------------------------------------------------------------------
# Status code classification
# ---------------------------------------------------------------------------

# 403, 999, 520-530 -> bot detection / anti-scraping, retry then next strategy
# 429 -> rate limited, retry with backoff on SAME strategy
# 404, 410, 405 -> non-retryable client errors, stop entirely
# 5xx (except Cloudflare 520-530) -> server error, stop entirely

_NON_RETRYABLE_CLIENT_ERRORS = {404, 405, 410}

# Status codes that indicate bot detection / anti-scraping blocks
# 403: Standard forbidden (Cloudflare, Akamai, etc.)
# 999: LinkedIn's custom bot detection code
# 520-530: Cloudflare-specific error codes (often masking bot blocks)
_BOT_DETECTION_CODES = {403, 999, 520, 521, 522, 523, 524, 525, 526, 527, 528, 529, 530}


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


async def _try_aiohttp(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    timeout: int,
    logger: logging.Logger,
) -> Optional[FetchResponse]:
    """Strategy 1: aiohttp — lightweight, already async."""
    try:
        async with session.get(
            url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as response:
            content_bytes = await response.read()
            return FetchResponse(
                status_code=response.status,
                content_bytes=content_bytes,
                headers=dict(response.headers),
                final_url=str(response.url),
                strategy="aiohttp",
            )
    except asyncio.TimeoutError:
        logger.warning("⚠️ [aiohttp] Timeout fetching %s", url)
        return None
    except (aiohttp.ClientError, OSError) as e:
        logger.warning(f"⚠️ [aiohttp] Connection error for {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ [aiohttp] Unexpected error for {url}: {e}", exc_info=True)
        return None


def _sync_curl_cffi_fetch(
    url: str,
    headers: dict,
    timeout: int,
    use_http2: bool,
    profiles: Optional[list] = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[FetchResponse]:
    """
    Synchronous curl_cffi fetch with profile rotation.
    Meant to be called via run_in_executor.
    """
    try:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
    except ImportError:
        if logger:
            logger.error("❌ [curl_cffi] Not installed")
        return None

    pool = profiles or _CURL_PROFILES
    if not pool:
        return None

    profiles_to_try = random.sample(pool, min(3, len(pool)))

    for profile in profiles_to_try:
        try:
            with Session(impersonate=profile, timeout=timeout) as sess:
                if not use_http2:
                    with contextlib.suppress(Exception):
                        _ = sess.curl.setopt(CurlOpt.HTTP_VERSION, 2)  # CURL_HTTP_VERSION_1_1
                resp = sess.get(url, headers=headers, allow_redirects=True)
                return FetchResponse(
                    status_code=resp.status_code,
                    content_bytes=resp.content,
                    headers=dict(resp.headers),
                    final_url=str(resp.url),
                    strategy=f"curl_cffi({profile}, h2={use_http2})",
                )
        except Exception:
            continue  # TLS error, connection reset -> try next profile

    return None


async def _try_curl_cffi(
    url: str,
    headers: dict,
    timeout: int,
    use_http2: bool,
    logger: logging.Logger,
) -> Optional[FetchResponse]:
    """Strategy 2/3: curl_cffi with browser impersonation (run in executor to avoid blocking)."""
    label = f"curl_cffi(h2={use_http2})"
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            _sync_curl_cffi_fetch,
            url,
            headers,
            timeout,
            use_http2,
            None,  # profiles parameter (5th)
            logger,  # logger parameter (6th)
        )
        if result is None:
            logger.warning(f"⚠️ [{label}] All profiles exhausted for {url}")
        return result
    except Exception as e:
        logger.error(f"❌ [{label}] Unexpected error for {url}: {e}", exc_info=True)
        return None


def _sync_cloudscraper_fetch(
    url: str,
    headers: dict,
    timeout: int,
    logger: logging.Logger,
) -> Optional[FetchResponse]:
    """Synchronous cloudscraper fetch. Meant to be called via run_in_executor."""
    try:
        import cloudscraper
    except ImportError:
        logger.error("❌ [cloudscraper] Not installed")
        return None

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        resp = scraper.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return FetchResponse(
            status_code=resp.status_code,
            content_bytes=resp.content,
            headers=dict(resp.headers),
            final_url=resp.url,
            strategy="cloudscraper",
        )
    except Exception:
        return None


async def _try_cloudscraper(
    url: str,
    headers: dict,
    timeout: int,
    logger: logging.Logger,
) -> Optional[FetchResponse]:
    """Strategy 4: cloudscraper with JS challenge solving (run in executor)."""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            _sync_cloudscraper_fetch,
            url,
            headers,
            timeout,
            logger,
        )
        if result is None:
            logger.warning(f"⚠️ [cloudscraper] Failed for {url}")
        return result
    except Exception as e:
        logger.error(f"❌ [cloudscraper] Unexpected error for {url}: {e}", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Main fallback orchestrator
# ---------------------------------------------------------------------------

async def fetch_url_with_fallback(
    url: str,
    session: aiohttp.ClientSession,
    logger: logging.Logger,
    *,
    referer: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    timeout: int = REQUEST_TIMEOUT,
    max_429_retries: int = MAX_429_RETRIES,
    max_retries_per_strategy: int = 2,
    max_size_mb: Optional[int] = None,
    preferred_strategy: Optional[str] = None,
) -> Optional[FetchResponse]:
    """
    Fetch a URL using a multi-strategy fallback chain.

    Strategy order:
      1. aiohttp        — cheapest, already async
      2. curl_cffi H2   — browser TLS impersonation
      3. curl_cffi H1   — bypasses HTTP/2 fingerprint detection
      4. cloudscraper    — JS challenge solver

    Each strategy is attempted up to max_retries_per_strategy times before
    moving to the next. Within each attempt, 429s are retried with backoff.

    Status code handling:
      - 200-399 : success, return immediately
      - 403     : bot blocked, retry same strategy, then move to next
      - 429     : rate limited, retry same attempt with incremental backoff
      - 404/410/405 : non-retryable, stop and return
      - 5xx     : server error, stop and return

    Args:
        url:                       Target URL.
        session:                   aiohttp session for strategy 1.
        logger:                    Logger instance.
        referer:                   Referer header (auto-generated if None).
        extra_headers:             Additional headers to merge in.
        timeout:                   Per-request timeout in seconds.
        max_429_retries:           Max retries on 429 per attempt.
        max_retries_per_strategy:  Max attempts per strategy before moving to next (default 2).
        max_size_mb:               Max size in mb of the response.
        preferred_strategy:        When set, only this strategy is tried (no fallback). Use the
                                   ``strategy`` field from a prior FetchResponse to pin image/asset
                                   fetches to the same strategy that worked for the parent page.
                                   If the name doesn't match any known strategy the full chain is
                                   used as a safety net.
    Returns:
        FetchResponse on success or non-retryable error, None if all strategies fail.
    """
    headers = build_stealth_headers(url, referer=referer, extra=extra_headers)

    if max_size_mb is not None:
        max_size_bytes = max_size_mb * 1024 * 1024

        try:
            async with session.head(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as head_resp:
                cl = (
                    head_resp.headers.get("Content-Length")
                    or head_resp.headers.get("content-length")
                )
                if cl:
                    size = int(cl)
                    if size > max_size_bytes:
                        logger.warning(
                            f"⚠️ Skipping {url}: Content-Length "
                            + f"{size / (1024 * 1024):.1f}MB exceeds limit of "
                            + f"{max_size_bytes:.0f}MB"
                        )
                        return None
        except Exception:
            # HEAD not supported (405), connection error, timeout — proceed with GET
            pass

    # Define the strategy chain: (name, async callable returning Optional[FetchResponse])
    all_strategies: List[Tuple[str, Callable[..., Coroutine[Any, Any, Optional[FetchResponse]]]]] = [
        ("curl_cffi(H2)", lambda: _try_curl_cffi(url, headers, timeout, use_http2=True, logger=logger)),
        # ("curl_cffi(H1)", lambda: _try_curl_cffi(url, headers, timeout, use_http2=False, logger=logger)),
        ("cloudscraper", lambda: _try_cloudscraper(url, headers, timeout, logger=logger)),
        ("aiohttp", lambda: _try_aiohttp(session, url, headers, timeout, logger)),
    ]

    # When a preferred strategy is given (e.g. from a cached page-level fetch),
    # use ONLY that strategy — no fallback — to avoid wasted attempts.
    if preferred_strategy:
        preferred_lower = preferred_strategy.lower()
        strategies = [
            (name, fn) for name, fn in all_strategies
            if name.lower().split('(')[0].strip() in preferred_lower
        ]
        if not strategies:
            logger.warning(
                f"⚠️ preferred_strategy='{preferred_strategy}' did not match any strategy name; "
                + "falling back to full chain"
            )
            strategies = all_strategies
        else:
            logger.debug(f"🔒 Using pinned strategy '{strategies[0][0]}' for {url}")
    else:
        strategies = all_strategies

    for strategy_name, strategy_fn in strategies:
        logger.debug(f"🔄 [{strategy_name}] Attempting {url}")

        for attempt in range(max_retries_per_strategy):
            if attempt > 0:
                # Backoff between retries of same strategy: 1s, 2s, ...
                retry_delay = attempt + random.uniform(0, 0.5)
                logger.debug(
                    f"🔄 [{strategy_name}] Retry {attempt + 1}/{max_retries_per_strategy} "
                    + f"for {url} after {retry_delay:.1f}s"
                )
                await asyncio.sleep(retry_delay)

            # -- 429 retry loop within this attempt --
            for retry_429 in range(max_429_retries + 1):
                result = await strategy_fn()

                # Strategy returned nothing (import missing, all profiles exhausted, connection error)
                if result is None:
                    logger.debug(
                        f"🔄 [{strategy_name}] No result on attempt {attempt + 1}/{max_retries_per_strategy}"
                    )
                    break  # break 429 loop, go to next attempt

                status = result.status_code

                # ---- SUCCESS ----
                if status < HttpStatusCode.BAD_REQUEST.value:
                    logger.info(f"✅ Fetched {url} via {result.strategy}")
                    return result

                # ---- Bot detection (403, 999, 520-530) -> retry this strategy,
                if status in _BOT_DETECTION_CODES or status > HttpStatusCode.CLOUDFLARE_NETWORK_ERROR.value:
                    logger.warning(
                        f"⚠️ [{strategy_name}] Bot blocked (HTTP {status}) for {url} "
                        + f"(attempt {attempt + 1}/{max_retries_per_strategy})"
                    )
                    break  # break 429 loop, go to next attempt

                # ---- 429: Rate limited -> retry with backoff on SAME attempt ----
                if status == HttpStatusCode.TOO_MANY_REQUESTS.value:
                    if retry_429 >= max_429_retries:
                        logger.warning(
                            f"⚠️ [{strategy_name}] 429 persists after {max_429_retries} "
                            + f"retries for {url}, trying next strategy"
                        )
                        break

                    # Check Retry-After header first
                    retry_after = result.headers.get("Retry-After") or result.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = int(retry_after)
                        except ValueError:
                            delay = 2 ** (retry_429 + 1)
                    else:
                        delay = 2 ** (retry_429 + 1)  # 2s, 4s, 8s

                    logger.warning(
                        f"⚠️ [{strategy_name}] 429 Rate Limited for {url}, "
                        + f"retrying in {delay}s ({retry_429 + 1}/{max_429_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                # ---- 404, 410, 405: Non-retryable client errors -> stop entirely ----
                if status in _NON_RETRYABLE_CLIENT_ERRORS:
                    logger.warning(
                        f"⚠️ [{strategy_name}] HTTP {status} for {url}, skipping (non-retryable)"
                    )
                    return result

                # ---- Other 4xx: Unknown client error -> stop entirely ----
                if (
                    HttpStatusCode.BAD_REQUEST.value <= status < HttpStatusCode.INTERNAL_SERVER_ERROR.value
                    and status not in _BOT_DETECTION_CODES
                ):
                    logger.warning(f"⚠️ [{strategy_name}] HTTP {status} for {url}, skipping")
                    return result

                # ---- 5xx: Server error -> stop entirely ----
                if status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value and status not in _BOT_DETECTION_CODES:
                    logger.error(f"❌ [{strategy_name}] Server error {status} for {url}")
                    return result

        logger.debug(f"🔄 [{strategy_name}] Exhausted all {max_retries_per_strategy} attempts for {url}")

    # All strategies exhausted
    logger.error(f"❌ All fetch strategies failed for {url}")
    return None


# ---------------------------------------------------------------------------
# Headless browser fetcher (opt-in)
# ---------------------------------------------------------------------------
"""
Robust web fetcher built on Crawl4AI (https://docs.crawl4ai.com/).

Replaces the hand-rolled Playwright fetcher with Crawl4AI's AsyncWebCrawler,
which provides out-of-the-box:
  - Anti-bot detection & multi-layer fallback (Cloudflare, Akamai, etc.)
  - Shadow DOM flattening (Salesforce Lightning, Web Components)
  - Stealth mode (navigator.webdriver masking, fingerprint evasion)
  - Built-in retry with proxy escalation
  - Automatic JS rendering wait (networkidle / CSS selector / JS expression)
  - Clean markdown + cleaned HTML extraction
  - iframe processing

This module wraps it behind the same FetchResponse interface so the rest of
your crawl pipeline doesn't need to change.
"""


import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional


# ---------------------------------------------------------------------------
# Response container — identical interface to the old Playwright fetcher
# ---------------------------------------------------------------------------
@dataclass
class FetchResponse:
    status_code: int
    content_bytes: bytes              # raw or cleaned HTML as UTF-8 bytes
    headers: dict
    final_url: str
    strategy: str
    markdown: str | None = None       # bonus: Crawl4AI gives us clean markdown
    links: dict | None = None         # {"internal": [...], "external": [...]}
    success: bool = True
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class NonRetryableError(Exception):
    """Raised when a retry would be pointless (DNS failure, 4xx, etc.)."""


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class Crawl4AIFetcher:
    """
    Headless Chromium fetcher powered by Crawl4AI's AsyncWebCrawler.

    Manages a single crawler instance reused across all fetches.
    A semaphore (MAX_CONCURRENT_PAGES) prevents runaway parallelism.
    """

    MAX_CONCURRENT_PAGES = 8

    # HTTP status codes that should NOT trigger retries
    _NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 451})

    # Minimum meaningful body length
    _MIN_BODY_LENGTH = 256

    # Shorter timeout for tier-1 so fallback to tier-2/3 happens fast
    # when a server tarpits the regular browser.
    _TIER1_PAGE_TIMEOUT = 20_000  # 20s

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        headless: bool = True,
        stealth: bool = True,
        text_mode: bool = False,
        extra_browser_args: list[str] | None = None,
        user_agent: str | None = None,
        proxy_config=None,
    ) -> None:
        # Tier 1 (regular + stealth) is created eagerly in start().
        # Tier 2 (undetected, no stealth) and Tier 3 (undetected + stealth)
        # are lazy-created on first need.
        self._crawler = None
        self._undetected_crawler = None
        self._undetected_stealth_crawler = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self.logger = logger or logging.getLogger(__name__)
        self._patchright_available: Optional[bool] = None

        # Store config params for deferred construction
        self._headless = headless
        self._stealth = stealth
        self._text_mode = text_mode
        self._extra_args = extra_browser_args or []
        self._user_agent = user_agent
        self._proxy_config = proxy_config

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _build_browser_config(self, *, stealth: bool) -> "BrowserConfig":
        """Build a BrowserConfig with the given stealth setting."""
        from crawl4ai.async_configs import BrowserConfig

        return BrowserConfig(
            browser_type="chromium",
            headless=self._headless,
            viewport_width=1280,
            viewport_height=720,
            ignore_https_errors=True,
            java_script_enabled=True,
            enable_stealth=stealth,
            text_mode=self._text_mode,
            extra_args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                *self._extra_args,
            ],
            **({"user_agent": self._user_agent} if self._user_agent else {
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            }),
        )

    def _has_patchright(self) -> bool:
        """Check once whether patchright is available."""
        if self._patchright_available is None:
            try:
                import patchright  # noqa: F401
                self._patchright_available = True
            except ImportError:
                self._patchright_available = False
                self.logger.warning(
                    "patchright not installed — undetected browser tiers unavailable. "
                    "Install with: pip install patchright && python -m patchright install chromium"
                )
        return self._patchright_available

    async def start(self) -> None:
        """Start tier-1 crawler: regular browser + stealth."""
        from crawl4ai import AsyncWebCrawler

        self._crawler = AsyncWebCrawler(
            config=self._build_browser_config(stealth=self._stealth),
        )
        await self._crawler.start()
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_PAGES)
        self.logger.info("Crawl4AIFetcher started (tier-1: regular + stealth)")

    async def _get_undetected_crawler(self):
        """Lazy-create tier-2: undetected browser, no stealth."""
        if self._undetected_crawler is not None:
            return self._undetected_crawler
        if not self._has_patchright():
            return None

        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
        from crawl4ai.browser_adapter import UndetectedAdapter

        config = self._build_browser_config(stealth=False)
        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=config,
            browser_adapter=UndetectedAdapter(),
        )
        self._undetected_crawler = AsyncWebCrawler(
            crawler_strategy=strategy, config=config,
        )
        await self._undetected_crawler.start()
        self.logger.info("Crawl4AIFetcher tier-2 started (undetected, no stealth)")
        return self._undetected_crawler

    async def _get_undetected_stealth_crawler(self):
        """Lazy-create tier-3: undetected browser + stealth."""
        if self._undetected_stealth_crawler is not None:
            return self._undetected_stealth_crawler
        if not self._has_patchright():
            return None

        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
        from crawl4ai.browser_adapter import UndetectedAdapter

        config = self._build_browser_config(stealth=True)
        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=config,
            browser_adapter=UndetectedAdapter(),
        )
        self._undetected_stealth_crawler = AsyncWebCrawler(
            crawler_strategy=strategy, config=config,
        )
        await self._undetected_stealth_crawler.start()
        self.logger.info("Crawl4AIFetcher tier-3 started (undetected + stealth)")
        return self._undetected_stealth_crawler

    async def _restart_crawler(self, tier: str) -> None:
        """Close and recreate a crawler whose browser process has crashed."""
        self.logger.warning(f"[crawl4ai] Restarting {tier} crawler after browser crash")
        if tier == "tier-1":
            if self._crawler:
                try:
                    await self._crawler.close()
                except Exception:
                    pass
            self._crawler = None
            from crawl4ai import AsyncWebCrawler
            self._crawler = AsyncWebCrawler(
                config=self._build_browser_config(stealth=self._stealth),
            )
            await self._crawler.start()
            self.logger.info(f"[crawl4ai] {tier} crawler restarted successfully")
        elif tier == "tier-2":
            if self._undetected_crawler:
                try:
                    await self._undetected_crawler.close()
                except Exception:
                    pass
            self._undetected_crawler = None
            # Next call to _get_undetected_crawler() will lazy-create it
        elif tier == "tier-3":
            if self._undetected_stealth_crawler:
                try:
                    await self._undetected_stealth_crawler.close()
                except Exception:
                    pass
            self._undetected_stealth_crawler = None
            # Next call to _get_undetected_stealth_crawler() will lazy-create it

    @staticmethod
    def _is_target_crashed(result: Optional["FetchResponse"]) -> bool:
        """Check if a failed result was caused by a browser crash."""
        if result is None:
            return False
        msg = result.error_message or ""
        return "Target crashed" in msg or "Target closed" in msg

    async def close(self) -> None:
        for crawler, label in [
            (self._crawler, "tier-1"),
            (self._undetected_crawler, "tier-2"),
            (self._undetected_stealth_crawler, "tier-3"),
        ]:
            if crawler:
                try:
                    await crawler.close()
                except Exception as exc:
                    self.logger.warning(f"Error closing {label} crawler: {exc}")
        self._crawler = None
        self._undetected_crawler = None
        self._undetected_stealth_crawler = None
        self.logger.info("Crawl4AIFetcher closed")

    async def __aenter__(self) -> "Crawl4AIFetcher":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    @staticmethod
    def _is_usable(result: Optional[FetchResponse]) -> bool:
        """Check whether a fetch result has real page content.

        Catches two failure modes:
        - Navigation failed entirely (no headers from server).
        - In-page JS redirect hit a Chromium error page. The initial HTTP
          response has headers, but the captured HTML is Chromium's internal
          error page. We detect this via an exact browser-generated string
          that no real web server would ever send.
        """
        if result is None or not result.success:
            return False
        if not result.headers:
            return False
        content = result.content_bytes.decode("utf-8", errors="replace")
        if "This page has been blocked by Chromium" in content:
            return False
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    # JS expression that waits for meaningful body content to appear.
    # Works for both normal DOM and Shadow DOM (Salesforce Lightning, etc.)
    # by measuring total text length across all shadow roots.
    # Skips cookie-consent / overlay elements so their text doesn't
    # satisfy the threshold before the real page content has rendered.
    # Returns true once there's > 100 chars of visible text (excluding overlays).
    _DEFAULT_CSR_WAIT_JS = (
        "js:() => {"
        "  const SKIP_RE = /onetrust|cookiebot|cookie-?banner|cookie-?consent|"
        "consent-?banner|gdpr|cc-window|cc-banner/i;"
        "  function skip(el) {"
        "    var id = el.id || '';"
        "    var cls = typeof el.className === 'string' ? el.className : '';"
        "    var role = el.getAttribute && (el.getAttribute('role') || '');"
        "    if (SKIP_RE.test(id) || SKIP_RE.test(cls)) return true;"
        "    var s = getComputedStyle(el);"
        "    if (s.position === 'fixed' || s.position === 'sticky') {"
        "      if (role === 'dialog' || role === 'alertdialog' || el.tagName === 'DIALOG') return true;"
        "      if (s.zIndex && parseInt(s.zIndex) > 999) return true;"
        "    }"
        "    return false;"
        "  }"
        "  function measure(n) {"
        "    let len = 0;"
        "    if (n.nodeType === 3) return (n.textContent || '').trim().length;"
        "    if (n.nodeType !== 1) return 0;"
        "    if (skip(n)) return 0;"
        "    if (n.shadowRoot) { for (const c of n.shadowRoot.childNodes) len += measure(c); }"
        "    for (const c of n.childNodes) len += measure(c);"
        "    return len;"
        "  }"
        "  return measure(document.body) > 100;"
        "}"
    )

    # JS snippet to remove common cookie-consent overlays from the DOM.
    # We REMOVE instead of clicking "Accept" because clicking can trigger
    # page navigation/redirects (e.g. zoom.us → ERR_BLOCKED_BY_CLIENT).
    # The underlying page content loads regardless of cookie consent on
    # most sites; remove_overlay_elements=True provides additional cleanup.
    _DISMISS_COOKIE_CONSENT_JS = (
        "(function(){"
        "  var sel = ["
        "    '#onetrust-banner-sdk', '#onetrust-consent-sdk', '#onetrust-pc-sdk',"
        "    '#CybotCookiebotDialog', '#CybotCookiebotDialogBodyUnderlay',"
        "    '.cc-window', '.cc-banner', '.cc-revoke',"
        "    '[class*=\"cookie-banner\"]', '[class*=\"cookie-consent\"]',"
        "    '[class*=\"consent-banner\"]', '[id*=\"cookie-banner\"]',"
        "    '[id*=\"cookie-consent\"]', '[id*=\"consent-banner\"]',"
        "    '[data-testid=\"cookie-banner\"]'"
        "  ];"
        "  for (var i = 0; i < sel.length; i++) {"
        "    var els = document.querySelectorAll(sel[i]);"
        "    for (var j = 0; j < els.length; j++) els[j].remove();"
        "  }"
        "})()"
    )

    async def fetch(
        self,
        url: str,
        *,
        # --- timing ---
        page_timeout: int = 60_000,
        wait_until: str = "commit",
        wait_for: str | None = None,
        delay_before_return_html: float = 3.0,
        # --- retry / anti-bot ---
        magic: bool = False,
        # --- content ---
        process_iframes: bool = False,
        remove_overlay_elements: bool = True,
        exclude_external_links: bool = False,
        word_count_threshold: int = 0,
        css_selector: str | None = None,
        excluded_tags: list[str] | None = None,
        scan_full_page: bool = False,
        # --- JS interaction ---
        js_code: str | list[str] | None = None,
        # --- proxy (per-request override) ---
        proxy_config=None,
        # --- fallback ---
    ) -> Optional[FetchResponse]:
        """
        Fetch a URL with full CSR/SPA support, anti-bot detection, and retries.

        Args:
            page_timeout:              Navigation timeout in ms. Default 60s to
                                       allow slow CSR pages time to load.
            wait_until:                Playwright load state for page.goto():
                                       "commit" (default — returns as soon as the
                                       server responds; actual content readiness is
                                       handled by wait_for), "domcontentloaded",
                                       "load". AVOID "networkidle" for sites with
                                       persistent connections (SPAs with polling).
            wait_for:                  Post-navigation wait condition. CSS selector
                                       ("css:main article") or JS expression
                                       ("js:() => document.querySelector('#app')").
                                       If None, a default Shadow DOM–aware JS wait
                                       is used that waits for >100 chars of visible
                                       text to appear.
            delay_before_return_html:  Extra seconds to wait after all conditions
                                       are met (catches late-rendering components).
            max_retries:               Number of retry rounds on anti-bot blocks.
            magic:                     Enable Crawl4AI's "magic mode" which auto-
                                       adjusts headers, timing, and fingerprints.
            process_iframes:           Merge iframe content into the main HTML.
            remove_overlay_elements:   Strip cookie banners, popups, modals.
            exclude_external_links:    Remove external links from output.
            word_count_threshold:      Minimum words per content block.
            css_selector:              Only extract content matching this selector.
            excluded_tags:             HTML tags to strip (e.g. ["form", "nav"]).
            scan_full_page:            Auto-scroll the page to trigger lazy loading.
            js_code:                   JavaScript to execute after page load
                                       (click "Load More", dismiss modals, etc.).
            proxy_config:              Per-request proxy override (ProxyConfig or
                                       list[ProxyConfig]).
            fallback_fetch_function:   Async function(url) -> raw HTML string,
                                       called as last resort after all retries.
        """
        if not self._crawler or not self._semaphore:
            raise RuntimeError("Crawl4AIFetcher not started — call start() first")

        # If no explicit wait_for, use our Shadow DOM–aware content check
        effective_wait_for = wait_for if wait_for is not None else self._DEFAULT_CSR_WAIT_JS

        fetch_kwargs = dict(
            page_timeout=page_timeout,
            wait_until=wait_until,
            wait_for=effective_wait_for,
            delay_before_return_html=delay_before_return_html,
            magic=magic,
            process_iframes=process_iframes,
            remove_overlay_elements=remove_overlay_elements,
            exclude_external_links=exclude_external_links,
            word_count_threshold=word_count_threshold,
            css_selector=css_selector,
            excluded_tags=excluded_tags,
            scan_full_page=scan_full_page,
            js_code=js_code,
            proxy_config=proxy_config,
        )

        async with self._semaphore:
            failed_tiers: list[str] = []

            # ---- Tier 1: Regular browser + stealth (shorter timeout) ----
            tier1_kwargs = {**fetch_kwargs, "page_timeout": self._TIER1_PAGE_TIMEOUT}
            result = await self._do_fetch(url, crawler=self._crawler, **tier1_kwargs)
            if self._is_usable(result):
                self._log_result(url, result, "tier-1", failed_tiers)
                return result
            failed_tiers.append("tier-1")
            self.logger.warning(f"⚠️ [crawl4ai] tier-1 failed for {url}, trying tier-2")
            # If the browser process crashed, restart it so subsequent
            # URLs aren't poisoned by the dead process.
            if self._is_target_crashed(result) or result is None:
                try:
                    self.logger.info(f"[crawl4ai] Restarting tier-1 crawler after browser crash")
                    await self._restart_crawler("tier-1")
                except Exception as exc:
                    self.logger.error(f"[crawl4ai] Failed to restart tier-1: {exc}")

            # ---- Tier 2: Undetected browser, no stealth ----
            undetected = await self._get_undetected_crawler()
            if undetected:
                result = await self._do_fetch(url, crawler=undetected, **fetch_kwargs)
                if self._is_usable(result):
                    self._log_result(url, result, "tier-2", failed_tiers)
                    return result
                failed_tiers.append("tier-2")
                self.logger.warning(f"⚠️ [crawl4ai] tier-2 failed for {url}, trying tier-3")
                if self._is_target_crashed(result) or result is None:
                    try:
                        self.logger.info(f"[crawl4ai] Restarting tier-2 crawler after browser crash")
                        await self._restart_crawler("tier-2")
                    except Exception as exc:
                        self.logger.error(f"[crawl4ai] Failed to restart tier-2: {exc}")
            else:
                failed_tiers.append("tier-2 (unavailable)")
                self.logger.warning(f"⚠️ [crawl4ai] tier-2 unavailable for {url}, trying tier-3")

            # ---- Tier 3: Undetected browser + stealth ----
            undetected_stealth = await self._get_undetected_stealth_crawler()
            if undetected_stealth:
                result = await self._do_fetch(url, crawler=undetected_stealth, **fetch_kwargs)
                if self._is_usable(result):
                    self._log_result(url, result, "tier-3", failed_tiers)
                    return result
                failed_tiers.append("tier-3")
            else:
                failed_tiers.append("tier-3 (unavailable)")

            # All tiers exhausted — return whatever the last result was
            self._log_result(url, result, "all-tiers-failed", failed_tiers)
            return result

    def _log_result(
        self, url: str, result: Optional[FetchResponse], tier: str,
        failed_tiers: list[str] | None = None,
    ) -> None:
        if result and result.final_url != url:
            self.logger.warning(f"⚠️ [crawl4ai] Redirected {url} → {result.final_url}")
        failed_ctx = f" (failed: {', '.join(failed_tiers)})" if failed_tiers else ""
        if tier == "all-tiers-failed":
            self.logger.error(
                f"❌ [crawl4ai] All tiers failed for {url}"
                + (f" (tried: {', '.join(failed_tiers)})" if failed_tiers else "")
            )
        elif result and result.success:
            self.logger.info(f"✅ [crawl4ai] {tier} succeeded for {url}{failed_ctx}")
        else:
            self.logger.warning(f"⚠️ [crawl4ai] {tier} failed for {url}{failed_ctx}")
    # ------------------------------------------------------------------
    # Internal fetch logic
    # ------------------------------------------------------------------
    async def _do_fetch(
        self,
        url: str,
        *,
        crawler,
        **kwargs,
    ) -> Optional[FetchResponse]:
        from crawl4ai.async_configs import CrawlerRunConfig, CacheMode

        # Always prepend cookie-consent dismissal JS so overlays don't
        # block SPA content from loading.
        user_js = kwargs.get("js_code")
        if user_js:
            js_list = [self._DISMISS_COOKIE_CONSENT_JS] + (
                user_js if isinstance(user_js, list) else [user_js]
            )
        else:
            js_list = [self._DISMISS_COOKIE_CONSENT_JS]

        # Build the per-request crawl config
        run_config = CrawlerRunConfig(
            # --- navigation & timing ---
            page_timeout=kwargs["page_timeout"],
            wait_until=kwargs["wait_until"],
            wait_for=kwargs["wait_for"],
            delay_before_return_html=kwargs["delay_before_return_html"],

            # --- anti-bot & retry ---
            magic=kwargs["magic"],

            # --- content processing ---
            process_iframes=kwargs["process_iframes"],
            remove_overlay_elements=kwargs["remove_overlay_elements"],
            exclude_external_links=kwargs["exclude_external_links"],
            word_count_threshold=kwargs["word_count_threshold"],
            scan_full_page=kwargs["scan_full_page"],

            # --- content selection ---
            **({"css_selector": kwargs["css_selector"]}
               if kwargs["css_selector"] else {}),
            **({"excluded_tags": kwargs["excluded_tags"]}
               if kwargs["excluded_tags"] else {}),

            # --- JS execution (cookie dismissal + user JS) ---
            js_code=js_list,

            # --- proxy (per-request override) ---
            **({"proxy_config": kwargs["proxy_config"]}
               if kwargs["proxy_config"] else {}),

            # Never use cached results for a live fetcher
            cache_mode=CacheMode.BYPASS,
        )

        try:
            self.logger.debug(
                f"[crawl4ai] arun {url} "
                f"(timeout={kwargs['page_timeout']}ms, wait_until={kwargs['wait_until']!r})"
            )
            result = await crawler.arun(url=url, config=run_config)
        except Exception as exc:
            self.logger.error(f"[crawl4ai] Unhandled error for {url}: {exc}")
            return None

        # --- Build our FetchResponse from CrawlResult ---
        if not result.success:
            self.logger.warning(
                f"[crawl4ai] Crawl failed for {url}: {result.error_message}"
            )
            return FetchResponse(
                status_code=result.status_code or 0,
                content_bytes=b"",
                headers=result.response_headers or {},
                final_url=result.url,
                strategy="crawl4ai",
                markdown=None,
                links=None,
                success=False,
                error_message=result.error_message,
            )

        # Prefer cleaned_html; fall back to raw html
        html_content = result.cleaned_html or result.html or ""
        content_bytes = html_content.encode("utf-8")

        # Validate content isn't an empty shell
        if len(content_bytes) < self._MIN_BODY_LENGTH:
            self.logger.warning(
                f"[crawl4ai] Body too small ({len(content_bytes)} bytes) "
                f"for {url}"
                f"html_cleaned: {result.cleaned_html}"
                f"html: {result.html}"
            )

        # Extract markdown safely (can be str or MarkdownGenerationResult)
        markdown_text = None
        if result.markdown:
            if isinstance(result.markdown, str):
                markdown_text = result.markdown
            elif hasattr(result.markdown, "raw_markdown"):
                markdown_text = result.markdown.raw_markdown
        
        return FetchResponse(
            status_code=result.status_code or 200,
            content_bytes=content_bytes,
            headers=result.response_headers or {},
            final_url=result.url,
            strategy="crawl4ai",
            markdown=markdown_text,
            links=result.links if result.links else None,
            success=True,
            error_message=None,
        )