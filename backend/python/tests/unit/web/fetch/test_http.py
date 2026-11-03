"""fetch_public: the deadline, the public-only address policy and error mapping around the
web connector's fetcher, which is stubbed here (its own suite covers the strategies)."""

import asyncio
import socket
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.sources.web.fetch_strategy import FetchResponse
from app.utils.url_fetcher import FetchError, PublicTarget, UnresolvableHostError
from app.web.deadline import Deadline
from app.web.errors import (
    Blocked,
    RateLimited,
    TimedOut,
    TooLarge,
    TooManyRedirects,
    Unreachable,
    UnsafeUrl,
    UpstreamError,
)
from app.web.fetch.http import MAX_PAGE_MB, FetchedBody, check_page_status, fetch_public

PUBLIC = "https://example.com/page"
# Numeric hosts resolve locally, without DNS; the autouse network guard fakes every lookup.
_REAL_GETADDRINFO = socket.getaddrinfo


def _response(status: int = 200, body: bytes = b"ok", headers: dict | None = None, final_url: str = PUBLIC,
              strategy: str = "aiohttp") -> FetchResponse:
    return FetchResponse(
        status_code=status, content_bytes=body, headers=headers or {"Content-Type": "text/html"},
        final_url=final_url, strategy=strategy,
    )


def _fallback(result: FetchResponse | None | Exception = None, **kwargs) -> patch:
    mock = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result, **kwargs)
    return patch("app.web.fetch.http.fetch_url_with_fallback", mock)


def _resolves_publicly() -> AbstractContextManager[object]:
    """Treat named hosts as public without a DNS lookup."""
    return patch(
        "app.web.fetch.http.resolve_public_http_target",
        return_value=PublicTarget("https", "example.com", 443, ()),
    )


class TestAddressPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://2130706433/",  # 127.0.0.1 as a decimal
            "http://[::ffff:127.0.0.1]/",
            "http://10.0.0.5/",
            "http://localhost/",
        ],
    )
    async def test_internal_addresses_are_refused_before_any_request(
        self, url: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:  # WP-0-SEC-01
        monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
        with _fallback(_response()) as fetch, pytest.raises(UnsafeUrl):
            await fetch_public(url, Deadline.after(5))
        fetch.assert_not_called()

    async def test_unresolvable_host_is_unreachable_not_unsafe(self) -> None:
        with patch("app.web.fetch.http.resolve_public_http_target", side_effect=UnresolvableHostError("nx")), \
                _fallback(_response()) as fetch, pytest.raises(Unreachable):
            await fetch_public("https://no-such-host.invalid/", Deadline.after(5))
        fetch.assert_not_called()

    async def test_redirect_to_metadata_address_is_refused(self) -> None:  # WP-0-FETCH-07
        with _resolves_publicly(), _fallback(_response()) as fetch:
            await fetch_public(PUBLIC, Deadline.after(5))
        allow_hop = fetch.call_args.kwargs["allow_hop"]
        with patch("app.web.fetch.http.resolve_public_http_target", side_effect=FetchError("blocked")):
            assert await allow_hop("http://169.254.169.254/") is False

    async def test_redirect_to_public_host_is_allowed_unless_the_caller_refuses(self) -> None:
        refuse = AsyncMock(return_value=False)
        with _resolves_publicly(), _fallback(_response()) as fetch:
            await fetch_public(PUBLIC, Deadline.after(5))
            assert await fetch.call_args.kwargs["allow_hop"]("https://other.example.org/") is True
            await fetch_public(PUBLIC, Deadline.after(5), allow_hop=refuse)
            assert await fetch.call_args.kwargs["allow_hop"]("https://other.example.org/") is False
        refuse.assert_awaited_once_with("https://other.example.org/")


class TestFetch:
    async def test_success(self) -> None:
        response = _response(body=b"<html>", headers={"content-TYPE": "text/html; charset=utf-8"}, final_url="https://example.com/final")
        with _resolves_publicly(), _fallback(response) as fetch:
            body = await fetch_public(PUBLIC, Deadline.after(5))
        assert body == FetchedBody("https://example.com/final", 200, "text/html; charset=utf-8", b"<html>")
        assert fetch.call_args.kwargs["max_size_mb"] == MAX_PAGE_MB
        assert fetch.call_args.kwargs["max_retries_per_strategy"] == 1

    async def test_expired_deadline_sends_nothing(self) -> None:
        with _fallback(_response()) as fetch, pytest.raises(TimedOut):
            await fetch_public(PUBLIC, Deadline(expires_at=0, clock=lambda: 1))
        fetch.assert_not_called()

    async def test_slow_server_ends_at_the_deadline(self) -> None:  # WP-0-FETCH-05
        async def drip(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(30)

        with _resolves_publicly(), patch("app.web.fetch.http.fetch_url_with_fallback", drip):
            loop = asyncio.get_running_loop()
            started = loop.time()
            with pytest.raises(TimedOut):
                await fetch_public(PUBLIC, Deadline.after(0.2))
            assert loop.time() - started < 2

    async def test_no_answer_is_unreachable(self) -> None:
        with _resolves_publicly(), _fallback(None), pytest.raises(Unreachable):
            await fetch_public(PUBLIC, Deadline.after(5))

    @pytest.mark.parametrize(
        ("reason", "error"),
        [
            ("unsafe_address", UnsafeUrl),
            ("redirect_refused", UnsafeUrl),
            ("too_many_redirects", TooManyRedirects),  # WP-0-FETCH-06
            ("max_size_exceeded", TooLarge),  # WP-0-FETCH-03 / 04
        ],
    )
    async def test_skip_reasons(self, reason: str, error: type) -> None:
        response = _response(status=413, body=b"", headers={"X-Fetch-Skip-Reason": reason}, strategy="redirect_guard")
        with _resolves_publicly(), _fallback(response), pytest.raises(error):
            await fetch_public(PUBLIC, Deadline.after(5))

    @pytest.mark.parametrize("reason", ["unsafe_address", "too_many_redirects", "max_size_exceeded"])
    async def test_a_page_cannot_fake_a_skip_reason(self, reason: str) -> None:
        response = _response(body=b"<p>page</p>", headers={"X-Fetch-Skip-Reason": reason, "Content-Type": "text/html"})
        with _resolves_publicly(), _fallback(response):
            assert (await fetch_public(PUBLIC, Deadline.after(5))).content == b"<p>page</p>"

    async def test_non_2xx_is_returned_for_the_caller_to_judge(self) -> None:
        with _resolves_publicly(), _fallback(_response(status=202)):
            assert (await fetch_public(PUBLIC, Deadline.after(5))).status == 202


class TestCheckPageStatus:
    @pytest.mark.parametrize("status", [200, 203, 299])
    def test_success(self, status: int) -> None:
        check_page_status(FetchedBody(PUBLIC, status, None, b""))

    @pytest.mark.parametrize(
        ("status", "error", "retryable"),
        [(401, Blocked, False), (403, Blocked, False), (429, RateLimited, True),
         (404, UpstreamError, False), (410, UpstreamError, False), (502, UpstreamError, True)],
    )
    def test_failures(self, status: int, error: type, retryable: bool) -> None:
        with pytest.raises(error) as info:
            check_page_status(FetchedBody(PUBLIC, status, None, b""))
        assert info.value.retryable is retryable
