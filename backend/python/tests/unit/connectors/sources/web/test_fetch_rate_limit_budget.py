"""One fetch waits on rate limits for at most RATE_LIMIT_WAIT_BUDGET seconds (scenarios FR-04, RS-07).

Each strategy used to back off from 2 s to 256 s on every attempt, so one always-429 URL could
hold a crawl for about 50 minutes; the crawl's per-site retry is what should wait longer.
"""

import logging
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.sources.web import fetch_strategy
from app.connectors.sources.web.fetch_strategy import (
    RATE_LIMIT_WAIT_BUDGET,
    FetchResponse,
    fetch_url_with_fallback,
)

URL = "https://example.com/page"


def _limited(strategy: str, status: int = 429) -> FetchResponse:
    return FetchResponse(status, b"", {}, URL, strategy)


@pytest.mark.parametrize("status", [429, 503])
async def test_an_always_limited_page_waits_within_one_budget(status: int) -> None:
    calls: list[str] = []

    def hop(name: str) -> Callable[..., Awaitable[FetchResponse]]:
        async def fake(*_: object, **__: object) -> FetchResponse:
            calls.append(name)
            return _limited(name, status)
        return fake

    sleep = AsyncMock()
    with patch.object(fetch_strategy, "_hops_curl_cffi", hop("curl")), \
         patch.object(fetch_strategy, "_hops_cloudscraper", hop("scraper")), \
         patch.object(fetch_strategy, "_hops_aiohttp", hop("aiohttp")), \
         patch.object(fetch_strategy.asyncio, "sleep", sleep):
        result = await fetch_url_with_fallback(URL, AsyncMock(), logging.getLogger("t"))

    waited = sum(c.args[0] for c in sleep.await_args_list)
    assert result is not None and result.status_code == status
    assert waited <= RATE_LIMIT_WAIT_BUDGET
    assert len(calls) <= 12, calls
    if status == 429:
        assert len(set(calls)) == 1, "a 429 isn't asked again through another client"
    else:
        assert set(calls) == {"curl", "scraper", "aiohttp"}, "a 503 may be a challenge page: every strategy tries"


async def test_as_pipeshub_only_plain_http_is_used_and_a_refusal_ends_the_fetch() -> None:
    calls: list[str] = []

    def hop(name: str, status: int) -> Callable[..., Awaitable[FetchResponse]]:
        async def fake(*_: object, **__: object) -> FetchResponse:
            calls.append(name)
            return _limited(name, status)
        return fake

    with patch.object(fetch_strategy, "_hops_curl_cffi", hop("curl", 200)), \
         patch.object(fetch_strategy, "_hops_cloudscraper", hop("scraper", 200)), \
         patch.object(fetch_strategy, "_hops_aiohttp", hop("aiohttp", 403)), \
         patch.object(fetch_strategy.asyncio, "sleep", AsyncMock()):
        result = await fetch_url_with_fallback(URL, AsyncMock(), logging.getLogger("t"), browser_like=False)

    assert result is not None and result.status_code == 403
    assert calls == ["aiohttp"]


def test_pipeshub_headers_name_pipeshub_and_claim_no_browser() -> None:
    headers = fetch_strategy.build_crawler_headers(URL, browser_like=False)
    assert "PipesHub/" in headers["User-Agent"]
    assert not any(k.lower().startswith("sec-ch-ua") for k in headers)


@pytest.mark.parametrize("status", [999, 522])
async def test_a_bot_blocked_page_with_retry_after_waits_within_one_budget(status: int) -> None:
    async def blocked(*_: object, **__: object) -> FetchResponse:
        return FetchResponse(status, b"", {"Retry-After": "120"}, URL, "fake")

    sleep = AsyncMock()
    with patch.object(fetch_strategy, "_hops_curl_cffi", blocked), \
         patch.object(fetch_strategy, "_hops_cloudscraper", blocked), \
         patch.object(fetch_strategy, "_hops_aiohttp", blocked), \
         patch.object(fetch_strategy.asyncio, "sleep", sleep):
        result = await fetch_url_with_fallback(URL, AsyncMock(), logging.getLogger("t"))

    waited = sum(c.args[0] for c in sleep.await_args_list)
    assert result is not None and result.status_code == status
    # Allow the 1-2 s pauses between a strategy's attempts on top of the budget.
    assert waited <= RATE_LIMIT_WAIT_BUDGET + 10, waited
