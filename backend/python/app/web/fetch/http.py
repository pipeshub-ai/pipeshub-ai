"""Download one public URL within a deadline.

Reuses the web connector's fetcher, which pins every redirect hop to a vetted address, stops
reading past a byte cap and falls back to browser-like TLS when a site refuses plain clients.
This module adds the overall deadline, typed errors and a stricter address policy: the
connector honours the operator's internal-host allowlist for crawls, but a URL chosen by a
model must never reach an internal host, so the first URL and every redirect also pass
``resolve_public_http_target``.
"""

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

from app.connectors.sources.web.address_guard import create_guarded_session
from app.connectors.sources.web.fetch_strategy import (
    FetchResponse,
    fetch_url_with_fallback,
)
from app.utils.url_fetcher import (
    FetchError,
    UnresolvableHostError,
    resolve_public_http_target,
)
from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.errors import (
    Blocked,
    InvalidUrl,
    RateLimited,
    TimedOut,
    TooLarge,
    TooManyRedirects,
    Unreachable,
    UnsafeUrl,
    UpstreamError,
    WebError,
)
from app.web.ports import HopCheck

logger = logging.getLogger(__name__)

MAX_PAGE_MB = 10

_HTTP_SUCCESS = range(200, 300)
_HTTP_REFUSED = frozenset({401, 403})
_HTTP_TOO_MANY_REQUESTS = 429

_SKIP_REASON_HEADER = "X-Fetch-Skip-Reason"
_GUARD_STRATEGIES = frozenset({"address_guard", "redirect_guard", "size_guard"})
_SKIP_ERRORS: dict[str, Callable[[str], WebError]] = {
    "unsafe_address": lambda url: UnsafeUrl(f"{url} points at a private or internal address"),
    "redirect_refused": lambda url: UnsafeUrl(f"Redirect to {url} is not allowed"),
    "too_many_redirects": lambda url: TooManyRedirects(f"{url} redirects too many times"),
    "max_size_exceeded": lambda url: TooLarge(f"{url} is larger than {MAX_PAGE_MB} MB"),
}


@dataclass(frozen=True)
class FetchedBody:
    url: str
    status: int
    content_type: str | None
    content: bytes


async def check_public(url: str) -> None:
    """Raise ``UnsafeUrl`` unless every address ``url``'s host resolves to is public."""
    try:
        await asyncio.to_thread(resolve_public_http_target, url)
    except UnresolvableHostError as e:
        raise Unreachable(f"Could not resolve the host of {redact_url(url)}") from e
    except FetchError as e:
        raise UnsafeUrl(f"{redact_url(url)} is not a public address") from e
    except (UnicodeError, ValueError) as e:
        # A host name that can't be encoded (``a..com``) or a port out of range.
        raise InvalidUrl(f"{redact_url(url)} is not a valid web address") from e


def _public_hops(allow_hop: HopCheck | None) -> HopCheck:
    async def allowed(url: str) -> bool:
        try:
            await check_public(url)
        except (UnsafeUrl, InvalidUrl):
            return False
        except Unreachable:
            pass  # the request itself fails and says so
        return allow_hop is None or await allow_hop(url)

    return allowed


def _header(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


async def fetch_public(
    url: str,
    deadline: Deadline,
    *,
    allow_hop: HopCheck | None = None,
) -> FetchedBody:
    """GET ``url``, following redirects to public addresses that ``allow_hop`` also accepts.
    Raises a ``WebError``."""
    if deadline.expired:
        raise TimedOut(f"No time left to fetch {redact_url(url)}")
    try:
        async with asyncio.timeout(deadline.remaining()):
            await check_public(url)
            async with create_guarded_session() as session:
                response: FetchResponse | None = await fetch_url_with_fallback(
                    url,
                    session,
                    logger,
                    timeout=max(1, math.ceil(deadline.remaining())),
                    max_retries_per_strategy=1,
                    max_size_mb=MAX_PAGE_MB,
                    # A 429 is answered at once as rate_limited; waiting it out is the crawl's way,
                    # and would spend the whole fetch deadline.
                    rate_limit_wait_s=0,
                    allow_hop=_public_hops(allow_hop),
                )
    except TimeoutError as e:
        raise TimedOut(f"{redact_url(url)} did not answer in time") from e

    if response is None:
        raise Unreachable(f"Could not connect to {redact_url(url)}")
    # Only the fetcher's own refusals carry a trustworthy skip reason; a page's headers are its own.
    skip_reason = _header(response.headers, _SKIP_REASON_HEADER) if response.strategy in _GUARD_STRATEGIES else None
    if skip_reason in _SKIP_ERRORS:
        raise _SKIP_ERRORS[skip_reason](redact_url(response.final_url or url))
    return FetchedBody(
        url=response.final_url or url,
        status=response.status_code,
        content_type=_header(response.headers, "content-type"),
        content=response.content_bytes,
    )


def check_page_status(body: FetchedBody) -> None:
    """Raise the ``WebError`` a page's non-2xx status means."""
    if body.status in _HTTP_SUCCESS:
        return
    url = redact_url(body.url)
    if body.status in _HTTP_REFUSED:
        error: WebError = Blocked(f"{url} refused the request (HTTP {body.status}); it may need a sign-in", status=body.status)
    elif body.status == _HTTP_TOO_MANY_REQUESTS:
        error = RateLimited(f"{url} is rate limiting requests (HTTP {body.status})", status=body.status)
    else:
        error = UpstreamError(f"{url} returned HTTP {body.status}", status=body.status)
    error.page_level = True
    raise error
