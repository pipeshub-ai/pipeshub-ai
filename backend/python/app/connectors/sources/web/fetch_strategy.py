"""
Multi-strategy URL fetcher with fallback chain for the web connector.

Fallback chain:
  1. aiohttp (existing session, cheapest, already async)
  2. curl_cffi with HTTP/2 browser impersonation
  3. curl_cffi with HTTP/1.1 forced
  4. cloudscraper (JS challenge solver)

Each strategy shares the same headers but uses different
TLS fingerprints / impersonation profiles.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

# ---------------------------------------------------------------------------
# Unified response wrapper
# ---------------------------------------------------------------------------


@dataclass
class FetchResponse:
    """Unified response from any fetch strategy."""

    status_code: int
    content_bytes: bytes
    headers: dict
    final_url: str
    strategy: str


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


def _get_supported_profiles() -> list:
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
            s = Session(impersonate=p)
            s.close()
            supported.append(p)
        except Exception:
            continue
    return supported


_CURL_PROFILES: list = _get_supported_profiles()

# ---------------------------------------------------------------------------
# Status code classification
# ---------------------------------------------------------------------------

# 403 -> bot detection, try next strategy
# 429 -> rate limited, retry with backoff on SAME strategy
# 404, 410, 405 -> non-retryable client errors, stop entirely
# 5xx -> server error, stop entirely

_NON_RETRYABLE_CLIENT_ERRORS = {404, 405, 410}


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
        logger.warning(f"⚠️ [aiohttp] Timeout fetching {url}")
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
) -> Optional[FetchResponse]:
    """
    Synchronous curl_cffi fetch with profile rotation.
    Meant to be called via run_in_executor.
    """
    try:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
    except ImportError:
        return None

    pool = profiles or _CURL_PROFILES
    if not pool:
        return None

    profiles_to_try = random.sample(pool, min(3, len(pool)))

    for profile in profiles_to_try:
        try:
            with Session(impersonate=profile, timeout=timeout) as sess:
                if not use_http2:
                    try:
                        sess.curl.setopt(CurlOpt.HTTP_VERSION, 2)  # CURL_HTTP_VERSION_1_1
                    except Exception:
                        pass

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
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _sync_curl_cffi_fetch,
            url,
            headers,
            timeout,
            use_http2,
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
) -> Optional[FetchResponse]:
    """Synchronous cloudscraper fetch. Meant to be called via run_in_executor."""
    try:
        import cloudscraper
    except ImportError:
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
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _sync_cloudscraper_fetch,
            url,
            headers,
            timeout,
        )
        if result is None:
            logger.warning(f"⚠️ [cloudscraper] Failed or not installed for {url}")
        return result
    except Exception as e:
        logger.error(f"❌ [cloudscraper] Unexpected error for {url}: {e}", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Main fallback orchestrator
# ---------------------------------------------------------------------------

MAX_429_RETRIES = 3


async def fetch_url_with_fallback(
    url: str,
    session: aiohttp.ClientSession,
    logger: logging.Logger,
    *,
    referer: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    timeout: int = 15,
    max_429_retries: int = MAX_429_RETRIES,
) -> Optional[FetchResponse]:
    """
    Fetch a URL using a multi-strategy fallback chain.

    Strategy order:
      1. aiohttp        — cheapest, already async
      2. curl_cffi H2   — browser TLS impersonation
      3. curl_cffi H1   — bypasses HTTP/2 fingerprint detection
      4. cloudscraper    — JS challenge solver

    Status code handling:
      - 200-399 : success, return immediately
      - 403     : bot blocked, try next strategy
      - 429     : rate limited, retry same strategy with incremental backoff
      - 404/410/405 : non-retryable, stop and return
      - 5xx     : server error, stop and return

    Args:
        url:              Target URL.
        session:          aiohttp session for strategy 1.
        logger:           Logger instance.
        referer:          Referer header (auto-generated if None).
        extra_headers:    Additional headers to merge in.
        timeout:          Per-request timeout in seconds.
        max_429_retries:  Max retries on 429 per strategy.

    Returns:
        FetchResponse on success or non-retryable error, None if all strategies fail.
    """
    headers = build_stealth_headers(url, referer=referer, extra=extra_headers)

    # Define the strategy chain: (name, async callable returning Optional[FetchResponse])
    strategies: List[Tuple[str, Callable[..., Coroutine[Any, Any, Optional[FetchResponse]]]]] = [
        ("aiohttp", lambda: _try_aiohttp(session, url, headers, timeout, logger)),
        ("curl_cffi(H2)", lambda: _try_curl_cffi(url, headers, timeout, use_http2=True, logger=logger)),
        ("curl_cffi(H1)", lambda: _try_curl_cffi(url, headers, timeout, use_http2=False, logger=logger)),
        ("cloudscraper", lambda: _try_cloudscraper(url, headers, timeout, logger=logger)),
    ]

    for strategy_name, strategy_fn in strategies:
        logger.debug(f"🔄 [{strategy_name}] Attempting {url}")

        # -- 429 retry loop for this strategy --
        for retry_429 in range(max_429_retries + 1):
            result = await strategy_fn()

            # Strategy returned nothing (import missing, all profiles exhausted, connection error)
            if result is None:
                logger.debug(f"🔄 [{strategy_name}] No result, moving to next strategy")
                break

            status = result.status_code

            # ---- SUCCESS ----
            if status < 400:
                logger.info(f"✅ Fetched {url} via {result.strategy}")
                return result

            # ---- 403: Bot detection -> try next strategy ----
            if status == 403:
                logger.warning(f"⚠️ [{strategy_name}] 403 Forbidden for {url}, trying next strategy")
                break

            # ---- 429: Rate limited -> retry with backoff on SAME strategy ----
            if status == 429:
                if retry_429 >= max_429_retries:
                    logger.warning(
                        f"⚠️ [{strategy_name}] 429 persists after {max_429_retries} retries for {url}, "
                        f"trying next strategy"
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
                    f"retrying in {delay}s ({retry_429 + 1}/{max_429_retries})"
                )
                await asyncio.sleep(delay)
                continue

            # ---- 404, 410, 405: Non-retryable client errors -> stop entirely ----
            if status in _NON_RETRYABLE_CLIENT_ERRORS:
                logger.warning(f"⚠️ [{strategy_name}] HTTP {status} for {url}, skipping (non-retryable)")
                return result

            # ---- Other 4xx: Unknown client error -> stop entirely ----
            if 400 <= status < 500:
                logger.warning(f"⚠️ [{strategy_name}] HTTP {status} for {url}, skipping")
                return result

            # ---- 5xx: Server error -> stop entirely ----
            if status >= 500:
                logger.error(f"❌ [{strategy_name}] Server error {status} for {url}")
                return result

    # All strategies exhausted
    logger.error(f"❌ All fetch strategies failed for {url}")
    return None
