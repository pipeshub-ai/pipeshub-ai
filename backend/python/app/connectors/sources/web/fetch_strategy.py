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
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    List,
    Optional,
    Protocol,
    Tuple,
    cast,
)
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp

from app.config.constants.http_status_code import HttpStatusCode
from app.services.base_client import parse_retry_after

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# ---------------------------------------------------------------------------
# Unified response wrapper
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 15
# Maximum time (seconds) to keep retrying a single strategy on 429/503 responses.
# asyncio.sleep yields to the event loop, so other concurrent domain fetches are
# never blocked while one URL is backing off.
MAX_RATE_LIMIT_BACKOFF = 300  # 5 minutes

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

# 429, 503 -> rate limited / CDN overload, retry with exponential backoff on SAME strategy
# 403, 999, 520-530 -> bot detection / anti-scraping, backoff then retry next strategy attempt
# 404, 410, 405 -> non-retryable client errors, stop entirely
# 5xx (except 503 and Cloudflare 520-530) -> server error, stop entirely

_NON_RETRYABLE_CLIENT_ERRORS = {404, 405, 410}

# Codes where exponential backoff + Retry-After header should be honoured and the
# same strategy retried immediately. 503 is included because Cloudflare and other
# CDNs use it interchangeably with 429 when rate-limiting crawlers.
_RATE_LIMIT_CODES = {429, 503}

# Status codes that indicate bot detection / anti-scraping blocks.
# On these we sleep briefly (honouring Retry-After if present) then move to the
# next strategy attempt rather than continuing on the same one.
# 403: Standard forbidden (Cloudflare, Akamai, AWS WAF, etc.)
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
# Size-check HEAD, walking redirects one hop at a time
# ---------------------------------------------------------------------------

MAX_HEAD_REDIRECTS = 10
_HEAD_REDIRECT_CODES = {301, 302, 303, 307, 308}
_HEAD_REFUSED_CODES = {405, 501}


async def _walk_redirects_with_head(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    allow_hop: Callable[[str], Awaitable[bool]] | None,
) -> tuple[str, dict] | FetchResponse | None:
    """HEAD ``url`` and its redirects one hop at a time.

    Returns the landing URL and its headers; a ``redirect_refused`` skip when ``allow_hop`` turns
    a target down before it is requested; or None when HEAD is refused, fails, times out or loops,
    in which case the caller falls back to a GET that follows redirects itself.
    """
    current = url
    try:
        for _ in range(MAX_HEAD_REDIRECTS):
            async with session.head(
                current,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as head_resp:
                status = head_resp.status
                head_headers = dict(head_resp.headers)
            location = head_headers.get("Location") or head_headers.get("location")
            if status in _HEAD_REFUSED_CODES:
                return None
            if status not in _HEAD_REDIRECT_CODES or not location:
                return current, head_headers
            target = urljoin(current, location)
            if allow_hop is not None and not await allow_hop(target):
                return FetchResponse(
                    status_code=200,
                    content_bytes=b"",
                    headers={"X-Fetch-Skip-Reason": "redirect_refused"},
                    final_url=target,
                    strategy="redirect_guard",
                )
            current = target
    except Exception:
        # HEAD not supported, connection error, timeout: proceed with GET, as before
        return None
    return None


# ---------------------------------------------------------------------------
# GET one redirect hop at a time, each target checked before it is requested
# ---------------------------------------------------------------------------

MAX_GET_REDIRECTS = 10
_READ_CHUNK = 64 * 1024


@dataclass
class _HopWalk:
    url: str
    referer: str | None
    extra_headers: dict | None
    allow_hop: Callable[[str], Awaitable[bool]]
    validators_for: Callable[[str], Awaitable[dict | None]] | None
    max_bytes: int | None


class _RequestsLike(Protocol):
    """A curl_cffi Session or a cloudscraper scraper: a requests-style client with its own cookies."""

    def get(self, url: str, **kwargs: object) -> Any: ...  # noqa: ANN401 -- each library's own Response


@dataclass
class _Hop:
    status: int
    headers: dict
    body: bytes = b""
    too_large: bool = False
    # Where the answer came from, when the client followed a redirect on its own (cloudscraper
    # requests the Location of a solved challenge itself).
    url: str | None = None


def _refused(target: str) -> FetchResponse:
    return FetchResponse(
        status_code=200,
        content_bytes=b"",
        headers={"X-Fetch-Skip-Reason": "redirect_refused"},
        final_url=target,
        strategy="redirect_guard",
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    return next((str(v) for k, v in headers.items() if str(k).lower() == wanted), None)


def _declared_too_large(headers: Mapping[str, str], max_bytes: int | None) -> bool:
    length = _header(headers, "Content-Length")
    return max_bytes is not None and bool(length) and str(length).isdigit() and int(length) > max_bytes


def _read_capped(chunks: Iterable[bytes], max_bytes: int | None) -> tuple[bytes, bool]:
    """Read a streamed body, stopping as soon as it passes ``max_bytes``."""
    body = bytearray()
    for chunk in chunks:
        body.extend(chunk)
        if max_bytes is not None and len(body) > max_bytes:
            return b"", True
    return bytes(body), False


async def _walk_hops(
    walk: _HopWalk,
    get: Callable[[str, dict], Awaitable[_Hop]],
    strategy: str,
) -> FetchResponse | None:
    """Follow redirects with ``get`` (one request per hop, on one connection), asking
    ``allow_hop`` before each target is requested. The last hop's answer is the page."""
    current = walk.url
    for _ in range(MAX_GET_REDIRECTS + 1):
        headers = build_stealth_headers(current, referer=walk.referer, extra=walk.extra_headers)
        if walk.validators_for is not None:
            headers.update(await walk.validators_for(current) or {})
        hop = await get(current, headers)
        if hop.url and urldefrag(hop.url).url != urldefrag(current).url:
            # The client went somewhere on its own; that page is already fetched, so check it
            # and drop its bytes if it's refused.
            if not await walk.allow_hop(hop.url):
                return _refused(hop.url)
            current = hop.url
        location = _header(hop.headers, "Location")
        if hop.status in _HEAD_REDIRECT_CODES and location:
            target = urljoin(current, location)  # handles relative and //host/path Locations
            if not await walk.allow_hop(target):
                return _refused(target)
            current = target
            continue
        if hop.too_large:
            return FetchResponse(
                status_code=413,
                content_bytes=b"",
                headers={"X-Fetch-Skip-Reason": "max_size_exceeded"},
                final_url=current,
                strategy="size_guard",
            )
        return FetchResponse(
            status_code=hop.status, content_bytes=hop.body, headers=hop.headers,
            final_url=current, strategy=strategy,
        )
    # A finished answer, filed under the link: None would be retried and sent to the headless
    # browser, which follows redirects without asking.
    return FetchResponse(
        status_code=508,
        content_bytes=b"",
        headers={"X-Fetch-Skip-Reason": "too_many_redirects"},
        final_url=walk.url,
        strategy="redirect_guard",
        success=False,
    )


async def _hops_aiohttp(
    session: aiohttp.ClientSession, walk: _HopWalk, timeout: int, logger: logging.Logger,
) -> FetchResponse | None:
    """aiohttp, hop by hop: the crawl's shared session carries cookies between hops."""
    async def get(url: str, headers: dict) -> _Hop:
        async with session.get(
            url, headers=headers, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as response:
            hop_headers = dict(response.headers)
            if response.status in _HEAD_REDIRECT_CODES or _declared_too_large(hop_headers, walk.max_bytes):
                return _Hop(response.status, hop_headers, too_large=response.status not in _HEAD_REDIRECT_CODES)
            body = bytearray()
            async for chunk in response.content.iter_chunked(_READ_CHUNK):
                body.extend(chunk)
                if walk.max_bytes is not None and len(body) > walk.max_bytes:
                    return _Hop(response.status, hop_headers, too_large=True)
            return _Hop(response.status, hop_headers, bytes(body))

    try:
        return await _walk_hops(walk, get, "aiohttp")
    except asyncio.TimeoutError:
        logger.warning("⚠️ [aiohttp] Timeout fetching %s", walk.url)
    except (aiohttp.ClientError, OSError) as e:
        logger.warning(f"⚠️ [aiohttp] Connection error for {walk.url}: {e}")
    except Exception as e:
        logger.error(f"❌ [aiohttp] Unexpected error for {walk.url}: {e}", exc_info=True)
    return None


def _sync_hop(client: _RequestsLike, url: str, headers: dict, timeout: int, max_bytes: int | None) -> _Hop:
    """One GET on a requests-style client (curl_cffi Session, cloudscraper), redirects not followed."""
    response = client.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True)
    try:
        hop_headers = dict(response.headers)
        answered_by = str(response.url) if getattr(response, "url", None) else None
        if response.status_code in _HEAD_REDIRECT_CODES:
            return _Hop(response.status_code, hop_headers, url=answered_by)
        if _declared_too_large(hop_headers, max_bytes):
            return _Hop(response.status_code, hop_headers, too_large=True, url=answered_by)
        body, too_large = _read_capped(response.iter_content(_READ_CHUNK), max_bytes)
        return _Hop(response.status_code, hop_headers, body, too_large, url=answered_by)
    finally:
        response.close()


async def _hops_curl_cffi(walk: _HopWalk, timeout: int, logger: logging.Logger) -> FetchResponse | None:
    """curl_cffi, hop by hop. One Session and one impersonation profile carry the whole walk, so
    cookies set on a redirect and the TLS fingerprint stay the same; another profile is tried
    only if the connection itself fails."""
    try:
        from curl_cffi.requests import Session
    except ImportError:
        logger.error("❌ [curl_cffi] Not installed")
        return None
    if not _CURL_PROFILES:
        return None
    loop = asyncio.get_running_loop()
    for profile in random.sample(_CURL_PROFILES, min(3, len(_CURL_PROFILES))):
        session = Session(impersonate=profile, timeout=timeout)

        async def get(url: str, headers: dict, session: _RequestsLike = session) -> _Hop:
            return await loop.run_in_executor(None, _sync_hop, session, url, headers, timeout, walk.max_bytes)

        try:
            return await _walk_hops(walk, get, f"curl_cffi({profile}, h2=True)")
        except Exception:
            continue  # TLS error, connection reset -> next profile, from the start of the chain
        finally:
            with contextlib.suppress(Exception):
                session.close()
    logger.warning(f"⚠️ [curl_cffi(h2=True)] All profiles exhausted for {walk.url}")
    return None


async def _hops_cloudscraper(walk: _HopWalk, timeout: int, logger: logging.Logger) -> FetchResponse | None:
    """cloudscraper, hop by hop, on one scraper, which keeps Cloudflare's clearance cookie for the
    hops after a solved challenge. The scraper requests a challenge's own target itself, so
    ``_walk_hops`` checks where each answer came from."""
    try:
        import cloudscraper
    except ImportError:
        logger.error("❌ [cloudscraper] Not installed")
        return None
    loop = asyncio.get_running_loop()
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    except Exception:
        return None

    async def get(url: str, headers: dict) -> _Hop:
        return await loop.run_in_executor(None, _sync_hop, scraper, url, headers, timeout, walk.max_bytes)

    try:
        return await _walk_hops(walk, get, "cloudscraper")
    except Exception:
        logger.warning(f"⚠️ [cloudscraper] Failed for {walk.url}")
        return None
    finally:
        with contextlib.suppress(Exception):
            scraper.close()


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
    max_retries_per_strategy: int = 2,
    max_size_mb: Optional[int] = None,
    preferred_strategy: Optional[str] = None,
    allow_hop: Callable[[str], Awaitable[bool]] | None = None,
    validators_for: Callable[[str], Awaitable[dict | None]] | None = None,
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
      - 429/503 : rate limited / CDN overload, retry same attempt with exponential backoff
      - 403     : bot blocked, backoff then retry next strategy attempt
      - 404/410/405 : non-retryable, stop and return
      - 5xx (non-503) : server error, stop and return

    Args:
        url:                       Target URL.
        session:                   aiohttp session for strategy 1.
        logger:                    Logger instance.
        referer:                   Referer header (auto-generated if None).
        extra_headers:             Additional headers to merge in.
        timeout:                   Per-request timeout in seconds.
        max_retries_per_strategy:  Max attempts per strategy before moving to next (default 2).
        max_size_mb:               Max size in mb of the response.
        allow_hop:                 Asked about each redirect target before it is requested, by the
                                   size-check HEAD and by the GET, which then follows redirects one
                                   hop at a time on the same connection. A refusal returns a
                                   ``redirect_refused`` skip whose ``final_url`` is the refused target.
        validators_for:            Returns conditional-request headers (If-None-Match and so on) for
                                   a URL; sent with the GET to that URL (to each hop, with allow_hop).
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
        walked = await _walk_redirects_with_head(session, url, headers, allow_hop)
        if isinstance(walked, FetchResponse):
            logger.info("Not following %s: redirect to %s refused", url, walked.final_url)
            return walked
        if walked is not None:
            # GET where HEAD landed, so the redirects aren't walked twice.
            url, head_headers = walked
            cl = head_headers.get("Content-Length") or head_headers.get("content-length")
            size = int(cl) if cl and str(cl).isdigit() else None
            if size is not None and size > max_size_bytes:
                logger.warning(
                    "⚠️ Skipping %s: Content-Length %.1fMB exceeds limit of %.0fMB",
                    url,
                    size / (1024 * 1024),
                    max_size_bytes / (1024 * 1024),
                )
                # Return a concrete response so callers can distinguish
                # an intentional size skip from a connection failure.
                return FetchResponse(
                    status_code=413,
                    content_bytes=b"",
                    headers={"X-Fetch-Skip-Reason": "max_size_exceeded"},
                    final_url=url,
                    strategy="size_guard",
                )

    all_strategies: List[Tuple[str, Callable[..., Coroutine[Any, Any, Optional[FetchResponse]]]]]
    if allow_hop is not None:
        # Every GET redirect, including one HEAD didn't show or a site that refuses HEAD, is checked
        # before it is requested; the last hop's GET is the page fetch, so no request is added.
        walk = _HopWalk(
            url=url, referer=referer, extra_headers=extra_headers, allow_hop=allow_hop,
            validators_for=validators_for,
            max_bytes=max_size_mb * 1024 * 1024 if max_size_mb is not None else None,
        )
        all_strategies = [
            ("curl_cffi(H2)", lambda: _hops_curl_cffi(walk, timeout, logger)),
            ("cloudscraper", lambda: _hops_cloudscraper(walk, timeout, logger)),
            ("aiohttp", lambda: _hops_aiohttp(session, walk, timeout, logger)),
        ]
    else:
        if validators_for is not None:
            validators = await validators_for(url)
            if validators:
                headers = {**headers, **validators}
        # Define the strategy chain: (name, async callable returning Optional[FetchResponse])
        all_strategies = [
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

    # Tracks the last FetchResponse received across all strategies/attempts.
    # When all strategies are exhausted due to bot-detection or 429s (not a
    # hard connection failure), this lets callers inspect the status code and
    # decide whether to queue the URL for a post-crawl retry.
    last_failed_result: FetchResponse | None = None

    for strategy_name, strategy_fn in strategies:
        for attempt in range(max_retries_per_strategy):
            if attempt > 0:
                # Backoff between retries of same strategy: 1s, 2s, ...
                retry_delay = attempt + random.uniform(0, 0.5)
                logger.debug(
                    f"🔄 [{strategy_name}] Retry {attempt + 1}/{max_retries_per_strategy} "
                    + f"for {url} after {retry_delay:.1f}s"
                )
                await asyncio.sleep(retry_delay)

            # -- exponential backoff loop: 2s, 4s, 8s, … up to MAX_RATE_LIMIT_BACKOFF (5 min) --
            # asyncio.sleep yields to the event loop, so other domain fetches are never blocked.
            _rl_attempt = 0
            while True:
                result = await strategy_fn()

                # Strategy returned nothing (import missing, all profiles exhausted, connection error)
                if result is None:
                    logger.debug(
                        f"🔄 [{strategy_name}] No result on attempt {attempt + 1}/{max_retries_per_strategy}"
                    )
                    break  # exit backoff loop, go to next strategy attempt

                status = result.status_code

                # ---- SUCCESS ----
                if status < HttpStatusCode.BAD_REQUEST.value:
                    return result

                # ---- 429 / 503: Rate limited or CDN overload -> exponential backoff, same attempt ----
                if status in _RATE_LIMIT_CODES:
                    exp_delay = 2 ** (_rl_attempt + 1)  # 2s, 4s, 8s, 16s, …

                    retry_after_hdr = result.headers.get("Retry-After") or result.headers.get("retry-after")
                    server_delay = parse_retry_after(retry_after_hdr)
                    # A header of 0, or a date already in the past, asks for no wait
                    # at all. Hammering the site immediately is what the backoff
                    # exists to prevent, so treat it as no signal.
                    if server_delay is not None and server_delay <= 0:
                        server_delay = None

                    delay = server_delay if server_delay is not None else exp_delay

                    if server_delay is not None and server_delay > MAX_RATE_LIMIT_BACKOFF:
                        # Server asks for a longer wait than our cap — return immediately
                        # so the caller can re-queue this URL via its own retry mechanism
                        # without blocking this coroutine or the crawl of other domains.
                        logger.warning(
                            "⚠️ [%s] HTTP %s for %s with Retry-After %.0fs exceeds cap (%ds), "
                            "returning for caller to re-queue",
                            strategy_name, status, url, delay, MAX_RATE_LIMIT_BACKOFF,
                        )
                        result.retry_after = delay
                        return result

                    if exp_delay >= MAX_RATE_LIMIT_BACKOFF:
                        logger.warning(
                            "⚠️ [%s] HTTP %s persists after max backoff (%.0fs) for %s, trying next strategy",
                            strategy_name, status, exp_delay, url,
                        )
                        last_failed_result = result
                        break

                    logger.warning(
                        "⚠️ [%s] HTTP %s for %s, backing off %.0fs (attempt %d)",
                        strategy_name, status, url, delay, _rl_attempt + 1,
                    )
                    await asyncio.sleep(delay)

                    _rl_attempt += 1
                    continue

                # ---- Bot detection (403, 999, 520-530) -> backoff, then try next strategy attempt ----
                if status in _BOT_DETECTION_CODES:
                    logger.warning(
                        "⚠️ [%s] Bot blocked (HTTP %s) for %s (attempt %d/%d)",
                        strategy_name, status, url, attempt + 1, max_retries_per_strategy
                    )
                    retry_after = result.headers.get("Retry-After") or result.headers.get("retry-after")
                    server_delay = parse_retry_after(retry_after)
                    delay = server_delay if server_delay else 2.0
                    if delay > MAX_RATE_LIMIT_BACKOFF:
                        result.retry_after = delay
                        last_failed_result = result
                        break
                    await asyncio.sleep(delay)
                    last_failed_result = result
                    break  # break backoff loop, go to next strategy attempt

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

                # ---- 5xx (non-503): Server error -> stop entirely ----
                if status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value and status not in _BOT_DETECTION_CODES and status not in _RATE_LIMIT_CODES:
                    logger.error(f"❌ [{strategy_name}] Server error {status} for {url}")
                    return result

        logger.debug(f"🔄 [{strategy_name}] Exhausted all {max_retries_per_strategy} attempts for {url}")

    # All strategies exhausted.
    # Return the last FetchResponse if we got one (bot-block / 429 exhaustion) so
    # callers can inspect the status code and decide whether to retry the URL later.
    # Returns None only when every strategy failed with a hard connection error.
    if last_failed_result is not None:
        logger.error(
            "❌ All fetch strategies failed for %s (last status: %s)",
            url, last_failed_result.status_code
        )
        return last_failed_result

    logger.error(f"❌ All fetch strategies failed for {url} (connection error)")
    return None


@dataclass
class FetchResponse:
    status_code: int
    content_bytes: bytes
    headers: dict
    final_url: str
    strategy: str
    markdown: str | None = None
    links: dict | None = None
    success: bool = True
    error_message: str | None = None
    retry_after: float | None = None