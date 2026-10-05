"""Tests for BaseServiceClient shared retry / timeout logic."""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from jose import jwt

from app.config.constants.service import TokenScopes
from app.services.base_client import (
    BaseServiceClient,
    CircuitBreaker,
    CircuitState,
    ServiceAuthRefusedError,
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


def _make_response(status: int, body: dict | None = None) -> httpx.Response:
    content = json.dumps(body or {}).encode()
    return httpx.Response(status, content=content)


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

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = _fake_request
        mock_make_client.return_value = mock_httpx

        response = await client._post_json("/test", {"key": "value"})

    assert response.status_code == 200
    assert call_count == 3


@pytest.mark.asyncio
async def test_request_retries_on_500() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = AsyncMock(
            side_effect=[_make_response(500), _make_response(200)]
        )
        mock_make_client.return_value = mock_httpx

        response = await client._post_json("/test", {})

    assert response.status_code == 200
    assert mock_httpx.request.await_count == 2


@pytest.mark.asyncio
async def test_request_raises_service_unavailable_on_exhausted_retries() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_make_client.return_value = mock_httpx

        with pytest.raises(ServiceUnavailableError):
            await client._post_json("/test", {})


@pytest.mark.asyncio
async def test_request_raises_service_call_error_on_persistent_5xx() -> None:
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = AsyncMock(return_value=_make_response(503))
        mock_make_client.return_value = mock_httpx

        with pytest.raises(ServiceCallError) as exc_info:
            await client._post_json("/test", {})

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_request_includes_error_message_from_5xx_body() -> None:
    """Final 5xx ServiceCallError must include remote error.message for UI reason."""
    client = _ConcreteClient(max_retries=2, retry_delay=0.0)
    body = {
        "success": False,
        "error": {
            "code": "PARSE_FAILED",
            "message": (
                "Failed to convert SVG to PNG: "
                "SVG conversion dependency missing: cairosvg is not installed"
            ),
        },
    }

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = AsyncMock(return_value=_make_response(500, body))
        mock_make_client.return_value = mock_httpx

        with pytest.raises(ServiceCallError) as exc_info:
            await client._post_json("/test", {})

    assert exc_info.value.status_code == 500
    assert "cairosvg is not installed" in str(exc_info.value)
    assert exc_info.value.details.get("error_message", "").find("cairosvg") >= 0


@pytest.mark.asyncio
async def test_request_does_not_retry_on_4xx() -> None:
    """Client errors (4xx) should not be retried."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)
    call_count = 0

    async def _fake_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_response(422, {"detail": "validation error"})

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = _fake_request
        mock_make_client.return_value = mock_httpx

        response = await client._post_json("/test", {})

    assert response.status_code == 422
    assert call_count == 1  # Only one attempt — 422 is not in TRANSIENT_STATUS_CODES


def test_circuit_breaker_allows_only_one_half_open_probe() -> None:
    breaker = CircuitBreaker(
        "test",
        failure_threshold=1,
        cooldown_seconds=10,
    )

    with patch("app.services.base_client.time.monotonic", return_value=0):
        breaker.record_failure()
        assert breaker.is_open is True
        assert breaker.should_attempt_probe() is False

    with patch("app.services.base_client.time.monotonic", return_value=11):
        assert breaker.is_open is False
        assert breaker.should_attempt_probe() is True
        assert breaker._state == CircuitState.HALF_OPEN
        assert breaker.is_open is True
        # Another caller must not claim a second probe slot concurrently.
        assert breaker.should_attempt_probe() is False

    breaker.record_success()
    with patch("app.services.base_client.time.monotonic", return_value=22):
        assert breaker.is_open is False


@pytest.mark.asyncio
async def test_recovery_probe_uses_health_check_not_real_request() -> None:
    """After cooldown, recovery should be probed via health_check(), not by
    capping the real workload request at cooldown_seconds (a real parse can
    legitimately take minutes)."""
    client = _ConcreteClient(
        max_retries=3,
        retry_delay=0,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown=0.01,
    )
    client.circuit_breaker.record_failure()
    await asyncio.sleep(0.02)

    with patch.object(client, "health_check", AsyncMock(return_value=True)) as mock_health:
        with patch.object(client, "_make_client") as mock_make_client:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.request = AsyncMock(return_value=_make_response(200))
            mock_make_client.return_value = mock_httpx

            response = await client._post_json("/test", {})

    mock_health.assert_awaited_once()
    assert response.status_code == 200
    assert client.circuit_breaker._state == CircuitState.CLOSED
    # The real request was not capped at cooldown_seconds via wait_for.
    mock_httpx.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_slow_probe_longer_than_cooldown_still_closes_circuit() -> None:
    """health_check() taking longer than cooldown_seconds (e.g. a slow but
    healthy service) must not be treated as a second cooldown window
    elapsing, and the probe must not be cut short — the circuit should
    still close once the slow-but-successful probe reports back."""
    client = _ConcreteClient(
        max_retries=3,
        retry_delay=0,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown=0.01,
    )
    client.circuit_breaker.record_failure()
    await asyncio.sleep(0.02)

    async def slow_health_check() -> bool:
        # Deliberately longer than cooldown_seconds (0.01s) and longer than
        # a naive "cap the probe at cooldown_seconds" implementation would
        # allow.
        await asyncio.sleep(0.05)
        return True

    with patch.object(client, "health_check", slow_health_check):
        with patch.object(client, "_make_client") as mock_make_client:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.request = AsyncMock(return_value=_make_response(200))
            mock_make_client.return_value = mock_httpx

            response = await client._post_json("/test", {})

    assert response.status_code == 200
    assert client.circuit_breaker._state == CircuitState.CLOSED
    mock_httpx.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_health_check_probe_reopens_circuit_without_real_request() -> None:
    client = _ConcreteClient(
        max_retries=3,
        retry_delay=0,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown=0.01,
    )
    client.circuit_breaker.record_failure()
    await asyncio.sleep(0.02)

    with patch.object(client, "health_check", AsyncMock(return_value=False)):
        with patch.object(client, "_make_client") as mock_make_client:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.request = AsyncMock(return_value=_make_response(200))
            mock_make_client.return_value = mock_httpx

            with pytest.raises(ServiceUnavailableError):
                await client._post_json("/test", {})

    mock_httpx.request.assert_not_awaited()
    assert client.circuit_breaker._state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_client_side_request_error_bypasses_circuit_breaker() -> None:
    """InvalidURL/UnsupportedProtocol etc. are client bugs, not service
    outages — they must not open the circuit breaker or be retried."""
    client = _ConcreteClient(max_retries=3, retry_delay=0.0)

    async def _fake_request(method, url, **kwargs):
        raise httpx.UnsupportedProtocol("no protocol")

    with patch.object(client, "_make_client") as mock_make_client:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.request = _fake_request
        mock_make_client.return_value = mock_httpx

        with pytest.raises(ServiceCallError):
            await client._post_json("/test", {})

    assert client.circuit_breaker._state == CircuitState.CLOSED
    assert client.circuit_breaker._consecutive_failures == 0


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
# Service tokens
# ---------------------------------------------------------------------------

SCOPED_SECRET = "scoped-secret-for-tests"


class _SecretsConfigService:
    async def get_config(self, key, **kwargs):
        return {"scopedJwtSecret": SCOPED_SECRET}


def _recording_httpx(response: httpx.Response) -> AsyncMock:
    mock_httpx = AsyncMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
    mock_httpx.__aexit__ = AsyncMock(return_value=False)
    mock_httpx.request = AsyncMock(return_value=response)
    return mock_httpx


def _sent_claims(mock_httpx: AsyncMock) -> dict:
    authorization = mock_httpx.request.await_args.kwargs["headers"]["Authorization"]
    assert authorization.startswith("Bearer ")
    return jwt.decode(authorization[len("Bearer "):], SCOPED_SECRET, algorithms=["HS256"])


@pytest.mark.asyncio
async def test_scoped_client_sends_bearer_with_scope_org_and_expiry() -> None:
    client = _ConcreteClient(
        config_service=_SecretsConfigService(), service_scope=TokenScopes.DOCUMENT_PARSE
    )
    mock_httpx = _recording_httpx(_make_response(200))

    with patch.object(client, "_make_client", return_value=mock_httpx):
        await client._post_json("/test", {}, org_id="org-1")

    claims = _sent_claims(mock_httpx)
    assert claims["scopes"] == ["document:parse"]
    assert claims["orgId"] == "org-1"
    assert time.time() < claims["exp"] <= time.time() + 3600


@pytest.mark.asyncio
async def test_orgless_call_omits_org_claim() -> None:
    client = _ConcreteClient(
        config_service=_SecretsConfigService(), service_scope=TokenScopes.DOCUMENT_PARSE
    )
    mock_httpx = _recording_httpx(_make_response(200))

    with patch.object(client, "_make_client", return_value=mock_httpx):
        await client._get_json("/test")

    claims = _sent_claims(mock_httpx)
    assert claims["scopes"] == ["document:parse"]
    assert "orgId" not in claims


@pytest.mark.asyncio
async def test_scoped_client_without_config_service_raises_before_any_request() -> None:
    client = _ConcreteClient(service_scope=TokenScopes.DOCUMENT_PARSE)
    mock_httpx = _recording_httpx(_make_response(200))

    with patch.object(client, "_make_client", return_value=mock_httpx):
        with pytest.raises(ServiceCallError, match="no config service"):
            await client._post_multipart("/test", files={}, data={}, org_id="org-1")

    mock_httpx.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "detail"),
    [(401, "Could not validate credentials"), (403, "This route requires a service token")],
)
async def test_refused_token_raises_auth_refused_with_detail_and_leaves_breaker_closed(
    status_code: int, detail: str
) -> None:
    client = _ConcreteClient(max_retries=3, retry_delay=0.0, circuit_breaker_threshold=1)
    mock_httpx = _recording_httpx(_make_response(status_code, {"detail": detail}))

    with patch.object(client, "_make_client", return_value=mock_httpx):
        with pytest.raises(ServiceAuthRefusedError) as exc_info:
            await client._post_json("/test", {})

    assert exc_info.value.status_code == status_code
    assert detail in str(exc_info.value)
    assert mock_httpx.request.await_count == 1
    assert not client.circuit_open
    assert client.circuit_breaker._consecutive_failures == 0
