"""Unit tests for app.sources.client.http.http_resilient_transport."""

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import pytest

from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.resilience import ResiliencePolicy

URL = "https://api.notion.com/v1/users/me"


def _policy(**kwargs) -> ResiliencePolicy:
    kwargs.setdefault("rate_limit", 1000)
    kwargs.setdefault("max_retries", 3)
    kwargs.setdefault("base_delay", 0.0)
    kwargs.setdefault("max_delay", 10.0)
    return ResiliencePolicy(**kwargs)


def _response(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, json={})


class _RecordingTransport(ResilientHTTPTransport):
    """Replaces the real network send with a scripted list of outcomes."""

    def __init__(self, policy: ResiliencePolicy, outcomes: list) -> None:
        super().__init__(policy)
        self._outcomes = list(outcomes)
        # Attempts beyond the scripted list repeat the last outcome; without
        # seeding this, an extra attempt raises AttributeError instead.
        self._outcomes_last = self._outcomes[-1] if self._outcomes else _response(200)
        self.attempts = 0
        self.closed: list[httpx.Response] = []
        # Holds references, not ids: a script may hand back the same response on
        # several attempts ([resp] * 3 repeats one object), and re-wrapping it
        # would nest the trackers so one close recorded several entries.
        self._wrapped: list[httpx.Response] = []

    async def _send(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        outcome = self._outcomes.pop(0) if self._outcomes else self._outcomes_last
        self._outcomes_last = outcome
        if isinstance(outcome, Exception):
            raise outcome
        if any(seen is outcome for seen in self._wrapped):
            return outcome

        self._wrapped.append(outcome)
        original_aclose = outcome.aclose

        async def tracking_aclose() -> None:
            self.closed.append(outcome)
            await original_aclose()

        outcome.aclose = tracking_aclose  # type: ignore[method-assign]
        return outcome


@pytest.fixture
def transport_factory():
    """Scripted stand-in for the real network send.

    A test may build more than once (see
    `test_does_not_retry_success_or_client_error`), and `patch.object` captures
    whatever is installed at `start()` — so the second patcher's "original" is
    the first patcher's stub. Unwinding in call order therefore restores a stub
    onto the class and leaks it into every later test in the session, where it
    answers every async request with `200 {}`. `ExitStack` unwinds in reverse,
    and does so even if a stop raises.
    """
    original = httpx.AsyncHTTPTransport.handle_async_request

    with ExitStack() as stack:

        def build(policy: ResiliencePolicy, outcomes: list) -> _RecordingTransport:
            transport = _RecordingTransport(policy, outcomes)
            stack.enter_context(
                patch.object(
                    httpx.AsyncHTTPTransport, "handle_async_request", transport._send
                )
            )
            return transport

        yield build

    assert httpx.AsyncHTTPTransport.handle_async_request is original, (
        "transport_factory left httpx.AsyncHTTPTransport patched; every later "
        "async request in this session would be answered by a _RecordingTransport"
    )


async def _run(transport: ResilientHTTPTransport) -> httpx.Response:
    return await transport.handle_async_request(httpx.Request("GET", URL))


class TestRetryOnStatus:
    @pytest.mark.asyncio
    async def test_retries_429_then_succeeds(self, transport_factory):
        transport = transport_factory(_policy(), [_response(429), _response(200)])
        response = await _run(transport)
        assert response.status_code == 200
        assert transport.attempts == 2

    @pytest.mark.asyncio
    async def test_retries_5xx(self, transport_factory):
        transport = transport_factory(_policy(), [_response(503), _response(200)])
        assert (await _run(transport)).status_code == 200
        assert transport.attempts == 2

    @pytest.mark.asyncio
    async def test_retries_529_service_overload(self, transport_factory):
        """Notion returns 529 (service_overload) and documents the same backoff
        treatment as 429; it is not a standard status, so it is easy to miss."""
        transport = transport_factory(_policy(), [_response(529), _response(200)])
        assert (await _run(transport)).status_code == 200
        assert transport.attempts == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_success_or_client_error(self, transport_factory):
        for status in (200, 404):
            transport = transport_factory(_policy(), [_response(status)])
            assert (await _run(transport)).status_code == status
            assert transport.attempts == 1

    @pytest.mark.asyncio
    async def test_returns_last_response_when_retries_exhausted(self, transport_factory):
        policy = _policy(max_retries=2)
        # Distinct objects, so the assertion can pin *which* responses were closed
        # rather than only how many.
        first, second, final = _response(429), _response(429), _response(429)
        transport = transport_factory(policy, [first, second, final])

        response = await _run(transport)

        assert response is final
        assert response.status_code == 429
        assert transport.attempts == 3  # max_retries + 1
        # Every retried response is closed; the one handed back stays open so the
        # caller can still read its body.
        assert transport.closed == [first, second]

    @pytest.mark.asyncio
    async def test_closes_response_before_retrying(self, transport_factory):
        """An unread response holds a pooled connection; leaking one per retry
        eventually exhausts the pool."""
        transport = transport_factory(_policy(), [_response(429), _response(200)])
        await _run(transport)
        assert len(transport.closed) == 1

    @pytest.mark.asyncio
    async def test_failing_aclose_does_not_cost_the_retry(self, transport_factory):
        """A close that itself fails used to escape as the caller's exception,
        turning a retryable 429 into a hard failure."""
        throttled = _response(429)

        async def bad_aclose() -> None:
            raise RuntimeError("connection already gone")

        throttled.aclose = bad_aclose  # type: ignore[method-assign]
        transport = transport_factory(_policy(), [throttled, _response(200)])

        response = await _run(transport)
        assert response.status_code == 200
        assert transport.attempts == 2


class TestRetryAfter:
    @pytest.mark.asyncio
    async def test_honors_retry_after(self, transport_factory):
        policy = _policy(max_delay=10.0)
        transport = transport_factory(
            policy, [_response(429, {"Retry-After": "2"}), _response(200)]
        )
        with patch("asyncio.sleep") as sleep:
            await _run(transport)
        assert sleep.await_args_list[0].args[0] == 2.0

    @pytest.mark.asyncio
    async def test_caps_retry_after_at_max_delay(self, transport_factory):
        policy = _policy(max_delay=5.0)
        transport = transport_factory(
            policy, [_response(429, {"Retry-After": "3600"}), _response(200)]
        )
        with patch("asyncio.sleep") as sleep:
            await _run(transport)
        assert sleep.await_args_list[0].args[0] == 5.0

    @pytest.mark.asyncio
    async def test_arms_the_gate_so_other_requests_wait(self, transport_factory):
        """A 429 pauses the whole connector, not just the retrying coroutine."""
        policy = _policy(max_delay=10.0)
        transport = transport_factory(
            policy, [_response(429, {"Retry-After": "2"}), _response(200)]
        )
        loop = asyncio.get_running_loop()
        gate_while_backing_off = []

        async def observe(_delay: float) -> None:
            gate_while_backing_off.append(policy._resume_at - loop.time())

        with patch("asyncio.sleep", observe):
            await _run(transport)

        # Gate deadline was ~2s out at the moment the retry backed off, so any
        # other request on this connector would have parked instead of firing.
        assert gate_while_backing_off[0] == pytest.approx(2.0, abs=0.5)


class TestNetworkErrors:
    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self, transport_factory):
        transport = transport_factory(
            _policy(), [httpx.ConnectError("boom"), _response(200)]
        )
        assert (await _run(transport)).status_code == 200
        assert transport.attempts == 2

    @pytest.mark.asyncio
    async def test_reraises_after_exhausting_retries(self, transport_factory):
        policy = _policy(max_retries=2)
        transport = transport_factory(policy, [httpx.ReadError("boom")] * 3)
        with pytest.raises(httpx.ReadError):
            await _run(transport)
        assert transport.attempts == 3


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_limiter_charged_once_per_http_attempt(self, transport_factory):
        """Retries are real requests against the upstream budget, so each one
        takes a token; exempting them would exceed the configured send rate."""
        policy = _policy(max_retries=3)
        transport = transport_factory(
            policy, [_response(429), _response(429), _response(200)]
        )
        acquisitions = 0
        original = policy.acquire

        async def counting_acquire() -> None:
            nonlocal acquisitions
            acquisitions += 1
            await original()

        with patch.object(policy, "acquire", counting_acquire), patch("asyncio.sleep"):
            await _run(transport)

        assert transport.attempts == 3
        assert acquisitions == transport.attempts

    @pytest.mark.asyncio
    async def test_no_retries_configured_passes_response_straight_through(
        self, transport_factory
    ):
        transport = transport_factory(_policy(max_retries=0), [_response(429)])
        assert (await _run(transport)).status_code == 429
        assert transport.attempts == 1
