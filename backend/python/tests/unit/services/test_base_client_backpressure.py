"""Tests for BaseServiceClient backpressure handling (429 + Retry-After).

Regression coverage for defect 1.4 in the adaptive-concurrency plan: a
saturated-but-healthy downstream service signalling backpressure must never
be treated as a failure, so it can never open the circuit breaker.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.base_client import (
    BaseServiceClient,
    CircuitState,
    ServiceBackpressureError,
)
from app.services.messaging.backpressure import BackpressureCoordinator


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


@contextmanager
def _fake_http_client(client: BaseServiceClient, request_impl):
    mock_httpx = AsyncMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
    mock_httpx.__aexit__ = AsyncMock(return_value=False)
    mock_httpx.request = request_impl
    with patch.object(client, "_make_client", return_value=mock_httpx):
        yield mock_httpx


@pytest.mark.asyncio
async def test_backpressure_retries_then_succeeds_without_touching_breaker() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0, max_backpressure_attempts=5)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return _make_response(429, headers={"Retry-After": "0.001"})
        return _make_response(200, {"ok": True})

    with _fake_http_client(client, _fake_request):
        response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 3
    # Backpressure retries must never count as failures or successes.
    assert client.circuit_breaker._state == CircuitState.CLOSED
    assert client.circuit_breaker._consecutive_failures == 0


@pytest.mark.asyncio
async def test_repeated_backpressure_leaves_circuit_closed_when_exhausted() -> None:
    """The regression test for defect 1.4: repeated 429 backpressure
    responses must leave circuit_breaker.is_open False, even after the
    backpressure attempt budget is exhausted."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0, max_backpressure_attempts=3)

    async def _always_backpressured(method, url, **kwargs):
        return _make_response(429, headers={"Retry-After": "0.001"})

    with _fake_http_client(client, _always_backpressured), pytest.raises(ServiceBackpressureError) as exc_info:
        await client._post_json("/test", {})

    assert client.circuit_breaker.is_open is False
    assert client.circuit_breaker._state == CircuitState.CLOSED
    assert client.circuit_breaker._consecutive_failures == 0
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_backpressure_honours_retry_after_header() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0, max_backpressure_attempts=5)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(429, headers={"Retry-After": "7"})
        return _make_response(200, {"ok": True})

    with _fake_http_client(client, _fake_request):
        with patch("app.services.base_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await client._post_json("/test", {})

    assert response.status_code == 200
    mock_sleep.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_backpressure_wait_is_capped_for_huge_retry_after() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0, max_backpressure_attempts=5)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_response(429, headers={"Retry-After": "9999"})
        return _make_response(200, {"ok": True})

    with _fake_http_client(client, _fake_request):
        with patch("app.services.base_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client._post_json("/test", {})

    from app.services.base_client import DEFAULT_BACKPRESSURE_WAIT_CAP

    mock_sleep.assert_awaited_once_with(DEFAULT_BACKPRESSURE_WAIT_CAP)


@pytest.mark.asyncio
async def test_bare_429_without_retry_after_uses_ordinary_transient_path() -> None:
    """A 429 with no Retry-After header is not a backpressure signal — it
    still counts against max_retries and can open the circuit breaker, same
    as any other transient status."""
    client = _ConcreteClient(
        max_retries=1, retry_delay=0.0, circuit_breaker_threshold=1
    )

    async def _bare_429(method, url, **kwargs):
        return _make_response(429)

    from app.services.base_client import ServiceCallError

    with _fake_http_client(client, _bare_429), pytest.raises(ServiceCallError) as exc_info:
        await client._post_json("/test", {})

    assert exc_info.value.status_code == 429
    assert client.circuit_breaker.is_open is True


@pytest.mark.asyncio
async def test_backpressure_attempts_independent_of_max_retries() -> None:
    """Backpressure must be retried on its own budget, not max_retries — a
    single-retry client must still survive several backpressure rounds."""
    client = _ConcreteClient(max_retries=1, retry_delay=0.0, max_backpressure_attempts=10)

    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 5:
            return _make_response(429, headers={"Retry-After": "0.001"})
        return _make_response(200, {"ok": True})

    with _fake_http_client(client, _fake_request):
        response = await client._post_json("/test", {})

    assert response.status_code == 200
    assert call_count == 5
    assert client.circuit_breaker._state == CircuitState.CLOSED


class TestBackpressureCoordinatorSignal:
    """A 429 + Retry-After must reach the shared BackpressureCoordinator
    (if one is configured) so a consumer sharing it can pause reading more
    messages off the event bus, independent of this client's own retry."""

    @pytest.mark.asyncio
    async def test_signals_coordinator_on_429_with_retry_after(self) -> None:
        coordinator = BackpressureCoordinator()
        client = _ConcreteClient(
            max_retries=2, retry_delay=0.0, max_backpressure_attempts=5,
            backpressure_coordinator=coordinator,
        )

        call_count = 0

        async def _fake_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(429, headers={"Retry-After": "7"})
            return _make_response(200, {"ok": True})

        with _fake_http_client(client, _fake_request):
            with patch("app.services.base_client.asyncio.sleep", new_callable=AsyncMock):
                await client._post_json("/test", {})

        assert coordinator.is_paused() is True
        assert coordinator.pause_remaining() == pytest.approx(7.0, abs=0.5)
        assert coordinator.paused_services == frozenset({"FakeService"})

    @pytest.mark.asyncio
    async def test_no_coordinator_configured_is_a_noop(self) -> None:
        """Default construction (no coordinator) must behave exactly as
        before — signalling is purely additive."""
        client = _ConcreteClient(max_retries=2, retry_delay=0.0, max_backpressure_attempts=5)
        assert client._backpressure_coordinator is None

        async def _fake_request(method, url, **kwargs):
            return _make_response(200, {"ok": True})

        with _fake_http_client(client, _fake_request):
            response = await client._post_json("/test", {})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_bare_429_without_retry_after_does_not_signal(self) -> None:
        coordinator = BackpressureCoordinator()
        client = _ConcreteClient(
            max_retries=1, retry_delay=0.0, circuit_breaker_threshold=1,
            backpressure_coordinator=coordinator,
        )

        async def _bare_429(method, url, **kwargs):
            return _make_response(429)

        from app.services.base_client import ServiceCallError

        with _fake_http_client(client, _bare_429), pytest.raises(ServiceCallError):
            await client._post_json("/test", {})

        assert coordinator.is_paused() is False
