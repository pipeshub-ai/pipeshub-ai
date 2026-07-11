"""Tests for BaseServiceClient shared retry / timeout logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.services.base_client import (
    BaseServiceClient,
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
