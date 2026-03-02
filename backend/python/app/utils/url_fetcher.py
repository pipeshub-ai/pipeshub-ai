"""
Robust & fast URL fetcher with multi-strategy fallback.

Fallback chain:
  1. curl_cffi with browser impersonation (rotates profiles)
  2. curl_cffi with HTTP/1.1 forced (some sites reject HTTP/2 fingerprints)
  3. cloudscraper (JS challenge solver, if installed)
  4. Plain requests with stealth headers (last resort)

Install:
  pip install curl_cffi
  pip install cloudscraper requests   # optional fallbacks
"""

import random
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional, cast
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Response wrapper (unified across all strategies)
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    status_code: int
    text: str
    content: bytes
    headers: dict
    url: str
    strategy: str  # which strategy succeeded

    def json(self):
        import json
        return json.loads(self.text)


class FetchError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Shared headers
# ---------------------------------------------------------------------------

def _build_headers(url: str, referer: Optional[str], extra: Optional[dict]) -> dict:
    parsed = urlparse(url)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
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
# Strategy 1: curl_cffi with impersonation
# ---------------------------------------------------------------------------

def _get_supported_profiles() -> list[str]:
    try:
        from curl_cffi.requests import Session
    except ImportError:
        return []

    candidates = [
        "chrome131", "chrome124", "chrome120", "chrome119", "chrome116",
        "chrome110", "chrome107", "chrome104", "chrome101", "chrome100",
        "chrome99", "chrome"
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


_PROFILES = _get_supported_profiles()


def _try_curl_cffi(
    url: str,
    headers: dict,
    timeout: int,
    use_http2: bool = True,
    profiles: Optional[list[str]] = None,
) -> Optional[FetchResult]:
    """Try curl_cffi with rotating profiles or an explicit profile list."""
    try:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
    except ImportError:
        return None

    if profiles is None:
        if not _PROFILES:
            return None
        # Default behavior: try up to 3 different profiles.
        profiles_to_try = random.sample(_PROFILES, min(3, len(_PROFILES)))
    else:
        # Constrained mode: callsites can force a single profile.
        profiles_to_try = profiles

    if not profiles_to_try:
        return None

    for profile in profiles_to_try:
        try:
            with Session(impersonate=profile, timeout=timeout) as session:
                # Force HTTP/1.1 if requested (bypasses HTTP/2 fingerprinting)
                if not use_http2:
                    try:
                        session.curl.setopt(CurlOpt.HTTP_VERSION, 2)  # CURL_HTTP_VERSION_1_1
                    except Exception:
                        pass

                resp = session.get(url, headers=headers, allow_redirects=True)

                if resp.status_code == 200:
                    return FetchResult(
                        status_code=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        headers=dict(resp.headers),
                        url=str(resp.url),
                        strategy=f"curl_cffi({profile}, h2={use_http2})",
                    )

                # 403 → try next profile
                if resp.status_code == 403:
                    print(f"403 error for {url} with profile {profile}")
                    continue

                # Other non-retryable errors
                if 400 <= resp.status_code < 500:
                    return FetchResult(
                        status_code=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        headers=dict(resp.headers),
                        url=str(resp.url),
                        strategy=f"curl_cffi({profile})",
                    )

        except Exception:
            print(f"Exception for {url} with profile {profile}")
            continue  # TLS error, connection reset → try next profile

    return None


# ---------------------------------------------------------------------------
# Strategy 2: cloudscraper
# ---------------------------------------------------------------------------

def _try_cloudscraper(url: str, headers: dict, timeout: int) -> Optional[FetchResult]:
    try:
        import cloudscraper
    except ImportError:
        return None

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        resp = scraper.get(url, headers=headers, timeout=timeout, allow_redirects=True)

        if resp.status_code == 200:
            return FetchResult(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=resp.url,
                strategy="cloudscraper",
            )
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Strategy 3: plain requests with stealth UA
# ---------------------------------------------------------------------------

def _try_requests(url: str, headers: dict, timeout: int) -> Optional[FetchResult]:
    try:
        import requests as req
    except ImportError:
        return None

    try:
        session = req.Session()
        session.headers.update(headers)

        # Add a realistic User-Agent (requests doesn't set one by default)
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        session.headers["User-Agent"] = random.choice(ua_list)

        resp = session.get(url, timeout=timeout, allow_redirects=True)

        if resp.status_code == 200:
            return FetchResult(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=resp.url,
                strategy="requests",
            )
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------
MAX_RETRIES = 0
def fetch_url(
    url: str,
    *,
    headers: Optional[dict] = None,
    referer: Optional[str] = None,
    timeout: int = 15,
    max_retries: int = MAX_RETRIES,
    strategy: Optional[Literal["curl_cffi_h2", "curl_cffi_h1", "cloudscraper", "requests"]] = None,
    profile: Optional[str] = None,
    verbose: bool = False,
) -> FetchResult:
    """
    Fetch a URL using a multi-strategy fallback chain.

    Tries (in order):
      1. curl_cffi with HTTP/2 impersonation (3 profiles)
      2. curl_cffi with HTTP/1.1 forced (3 profiles)
      3. cloudscraper (if installed)
      4. Plain requests

    Each top-level strategy is retried up to max_retries times with backoff.

    Args:
        url:         Target URL.
        headers:     Extra headers (optional).
        referer:     Referer header (auto-generated if None).
        timeout:     Request timeout in seconds.
        max_retries: Retries per strategy (the whole chain runs once).
        strategy:    Optional single strategy to run (no fallback chain).
        profile:     Optional curl_cffi profile to use (e.g. "chrome120").
        verbose:     Print which strategy is being tried.

    Returns:
        FetchResult with .text, .content, .status_code, .strategy, etc.

    Raises:
        FetchError: If all strategies fail.
    """
    req_headers = _build_headers(url, referer, headers)
    selected_profiles = [profile] if profile else None

    strategy_map = {
        "curl_cffi_h2": (
            "curl_cffi (HTTP/2)",
            lambda: _try_curl_cffi(
                url, req_headers, timeout, use_http2=True, profiles=selected_profiles
            ),
        ),
        "curl_cffi_h1": (
            "curl_cffi (HTTP/1.1)",
            lambda: _try_curl_cffi(
                url, req_headers, timeout, use_http2=False, profiles=selected_profiles
            ),
        ),
        "cloudscraper": ("cloudscraper", lambda: _try_cloudscraper(url, req_headers, timeout)),
        "requests": ("requests", lambda: _try_requests(url, req_headers, timeout)),
    }

    if strategy is not None:
        if strategy not in strategy_map:
            raise FetchError(f"Unknown fetch strategy: {strategy}")
        strategies = [strategy_map[strategy]]
    else:
        strategies = [
            strategy_map["curl_cffi_h2"],
            strategy_map["curl_cffi_h1"],
            strategy_map["cloudscraper"],
            strategy_map["requests"],
        ]

    errors = []

    for name, strategy_fn in strategies:
        for attempt in range(max_retries + 1):
            if verbose:
                print(f"  [{name}] attempt {attempt + 1}/{max_retries + 1}...")

            try:
                result = strategy_fn()
                if result is not None:
                    if verbose:
                        print(f"  ✓ Success via {result.strategy}")
                    return result
            except Exception as e:
                errors.append(f"{e}")

            # Small backoff between retries of same strategy
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.3))

        if verbose:
            print(f"  ✗ {name} exhausted")

    raise FetchError(
        errors[0] if errors else "No error details"
    )


# ---------------------------------------------------------------------------
# Fetcher class (for repeated fetches with connection reuse)
# ---------------------------------------------------------------------------

class Fetcher:
    """Session-based fetcher for batch operations. Reuses connections."""

    def __init__(self, timeout: int = 15, max_retries: int = 2, verbose: bool = False) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose

    def fetch(self, url: str, **kwargs) -> FetchResult:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("max_retries", self.max_retries)
        kwargs.setdefault("verbose", self.verbose)
        return fetch_url(url, **kwargs)

    def fetch_many(
        self,
        urls: list[str],
        *,
        delay: float = 1.0,
        **kwargs,
    ) -> list[FetchResult | FetchError]:
        """Fetch multiple URLs sequentially with a delay between each."""
        results = []
        for i, url in enumerate(urls):
            try:
                result = self.fetch(url, **kwargs)
                results.append(result)
            except FetchError as e:
                results.append(e)

            if i < len(urls) - 1 and delay > 0:
                time.sleep(delay + random.uniform(0, 0.5))

        return results

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
