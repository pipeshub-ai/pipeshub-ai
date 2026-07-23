"""Tests for BaseServiceClient shared retry / circuit-breaker / timeout logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from app.services.base_client import (
    BaseServiceClient,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ServiceCallError,
    ServiceUnavailableError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConcreteClient(BaseServiceClient):
    def __init__(self, **kwargs) -> None:
        super().__init__(
            service_url="http://fake:9000",
            service_name="FakeService",
            **kwargs,
        )


def _make_response(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    content = json.dumps(body or {}).encode()
    return httpx.Response(status, content=content, headers=headers or {})


def _patch_client(client: BaseServiceClient, request_side_effect) -> AsyncMock:
    """Patch the client's persistent httpx.AsyncClient with a fake `.request`."""
    mock_httpx = AsyncMock()
    mock_httpx.is_closed = False
    if callable(request_side_effect) and not isinstance(request_side_effect, AsyncMock):
        mock_httpx.request = request_side_effect
    else:
        mock_httpx.request = AsyncMock(side_effect=request_side_effect)
    client._client = mock_httpx
    return mock_httpx


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_retries_on_503() -> None:
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return _make_response(503)
        return _make_response(200, {"ok": True})

    _patch_client(client, _fake_request)

    response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 3


@pytest.mark.asyncio
async def test_request_retries_on_429() -> None:
    """429 is in TRANSIENT_STATUS_CODES and must be retried (regression test)."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return _make_response(429)
        return _make_response(200, {"ok": True})

    _patch_client(client, _fake_request)

    response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 2


@pytest.mark.asyncio
async def test_request_retries_on_remote_protocol_error() -> None:
    """httpx.RemoteProtocolError ("Server disconnected...") must be retried
    (regression test for the bug that caused indexing to fail after 1 real
    attempt while logging "failed after 3 attempts")."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return _make_response(200, {"ok": True})

    _patch_client(client, _fake_request)

    response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 3


@pytest.mark.asyncio
async def test_request_retries_on_read_error() -> None:
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.ReadError("connection reset")
        return _make_response(200, {"ok": True})

    _patch_client(client, _fake_request)

    response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 2


@pytest.mark.asyncio
async def test_request_raises_service_unavailable_on_exhausted_retries() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)
    _patch_client(client, httpx.ConnectError("Connection refused"))

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await client._post_json("/test", {})

    # Error message must report the real attempt count, not a hardcoded config value.
    assert "2 attempt(s)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_raises_service_call_error_on_persistent_5xx() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)
    _patch_client(client, AsyncMock(return_value=_make_response(503)))

    with pytest.raises(ServiceCallError) as exc_info:
        await client._post_json("/test", {})

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_request_does_not_retry_on_4xx() -> None:
    """Client errors (4xx, other than 429) should not be retried."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)
    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_response(422, {"detail": "validation error"})

    _patch_client(client, _fake_request)

    response = await client._post_json("/test", {})

    assert response.status_code == 422
    assert call_count == 1  # Only one attempt — 422 is not in TRANSIENT_STATUS_CODES


@pytest.mark.asyncio
async def test_request_does_not_retry_on_invalid_url() -> None:
    """Non-transport RequestError subclasses (e.g. bad URL) fail fast."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)
    _patch_client(client, httpx.UnsupportedProtocol("unsupported"))

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await client._post_json("/test", {})

    assert "1 attempt(s)" in str(exc_info.value)
    assert "non-retryable" in str(exc_info.value)


@pytest.mark.asyncio
async def test_retry_after_header_floors_backoff_delay() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)
    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(503, headers={"Retry-After": "0.05"})
        return _make_response(200, {"ok": True})

    _patch_client(client, _fake_request)

    with patch("app.services.base_client.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        await client._post_json("/test", {})

    # Retry-After floors the jittered delay.
    assert mock_sleep.await_args.args[0] >= 0.05


@pytest.mark.asyncio
async def test_backoff_is_jittered_and_capped() -> None:
    client = _ConcreteClient(retry_delay=10.0, max_backoff=1.0)
    delays = {client._compute_backoff(1, None) for _ in range(20)}
    assert all(0.0 <= d <= 1.0 for d in delays)
    assert len(delays) > 1  # jitter should produce varying delays


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold_failures() -> None:
    client = _ConcreteClient(max_retries=1, retry_delay=0.0, circuit_breaker_threshold=2)
    _patch_client(client, httpx.ConnectError("refused"))

    with pytest.raises(ServiceUnavailableError):
        await client._post_json("/test", {})
    assert client.circuit_breaker.state == CircuitState.CLOSED

    with pytest.raises(ServiceUnavailableError):
        await client._post_json("/test", {})
    assert client.circuit_breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_fails_fast_without_network_call_when_open() -> None:
    client = _ConcreteClient(max_retries=1, retry_delay=0.0, circuit_breaker_threshold=1)
    mock_httpx = _patch_client(client, httpx.ConnectError("refused"))

    with pytest.raises(ServiceUnavailableError):
        await client._post_json("/test", {})
    assert client.circuit_breaker.state == CircuitState.OPEN

    mock_httpx.request.reset_mock()
    with pytest.raises(CircuitBreakerOpenError):
        await client._post_json("/test", {})
    mock_httpx.request.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_recovers_on_success() -> None:
    breaker = CircuitBreaker(service_name="Test", failure_threshold=1, reset_seconds=0.0)
    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # Cooldown of 0s means the very next allow_request() call transitions to half-open.
    await breaker.allow_request()
    assert breaker.state == CircuitState.HALF_OPEN

    await breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_probe_reopens_on_failure() -> None:
    breaker = CircuitBreaker(service_name="Test", failure_threshold=1, reset_seconds=0.0)
    await breaker.record_failure()
    await breaker.allow_request()
    assert breaker.state == CircuitState.HALF_OPEN

    await breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_success_resets_consecutive_failures() -> None:
    client = _ConcreteClient(max_retries=1, retry_delay=0.0, circuit_breaker_threshold=2)
    _patch_client(client, httpx.ConnectError("refused"))
    with pytest.raises(ServiceUnavailableError):
        await client._post_json("/test", {})
    assert client.circuit_breaker._consecutive_failures == 1

    _patch_client(client, AsyncMock(return_value=_make_response(200, {"ok": True})))
    await client._post_json("/test", {})
    assert client.circuit_breaker._consecutive_failures == 0
    assert client.circuit_breaker.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Persistent client lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_is_reused_across_calls() -> None:
    client = _ConcreteClient(max_retries=1, retry_delay=0.0)
    _patch_client(client, AsyncMock(return_value=_make_response(200, {"ok": True})))

    first = await client._get_client()
    second = await client._get_client()
    assert first is second


@pytest.mark.asyncio
async def test_close_clears_client() -> None:
    client = _ConcreteClient()
    real_client = client._make_client()
    client._client = real_client

    await client.close()

    assert client._client is None
    assert real_client.is_closed


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_returns_true_for_200() -> None:
    client = _ConcreteClient()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = AsyncMock(return_value=httpx.Response(200, json={"status": "ok"}))
        mock_cls.return_value = mock_instance

        result = await client.health_check()

    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_for_connection_error() -> None:
    client = _ConcreteClient()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value = mock_instance

        result = await client.health_check()

    assert result is False


# ---------------------------------------------------------------------------
# Cached pre-flight health check vs. circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_cached_recovers_open_breaker_during_cooldown() -> None:
    client = _ConcreteClient(circuit_breaker_reset_seconds=300.0)
    client.circuit_breaker._state = CircuitState.OPEN
    client.circuit_breaker._opened_at = 0.0

    with patch.object(
        client, "health_check", new=AsyncMock(return_value=True)
    ) as mock_hc:
        result = await client.health_check_cached()

    assert result is True
    assert client.circuit_breaker.state == CircuitState.CLOSED
    mock_hc.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_cached_retries_negative_result_after_failure_ttl() -> None:
    import time as _time

    client = _ConcreteClient(circuit_breaker_reset_seconds=300.0)
    client.circuit_breaker._state = CircuitState.OPEN
    client._health_cache = False
    client._health_cache_at = _time.monotonic() - 2.0

    with patch.object(
        client, "health_check", new=AsyncMock(return_value=True)
    ) as mock_hc:
        result = await client.health_check_cached(failure_ttl=1.0)

    assert result is True
    assert client.circuit_breaker.state == CircuitState.CLOSED
    mock_hc.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_check_cached_reuses_result_within_ttl() -> None:
    client = _ConcreteClient()

    with patch.object(client, "health_check", new=AsyncMock(return_value=True)) as mock_hc:
        first = await client.health_check_cached(ttl=60.0)
        second = await client.health_check_cached(ttl=60.0)

    assert first is True and second is True
    mock_hc.assert_awaited_once()  # second call served from cache


def test_circuit_breaker_is_blocking_reflects_cooldown() -> None:
    import time as _time

    breaker = CircuitBreaker(service_name="Test", failure_threshold=1, reset_seconds=300.0)
    assert breaker.is_blocking() is False  # CLOSED

    breaker._state = CircuitState.OPEN
    breaker._opened_at = _time.monotonic()
    assert breaker.is_blocking() is True  # OPEN, cooldown remaining

    breaker._opened_at = _time.monotonic() - 301.0
    assert breaker.is_blocking() is False  # OPEN, cooldown elapsed — probe allowed

    breaker._state = CircuitState.HALF_OPEN
    assert breaker.is_blocking() is False  # HALF_OPEN never blocks the health probe
