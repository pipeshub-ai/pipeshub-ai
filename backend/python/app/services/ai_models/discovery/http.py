"""Bounded, guarded GETs for provider list endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from app.services.ai_models.discovery.types import DiscoveryErrorCode, DiscoveryHttpError
from app.utils.aimodels import require_allowed_endpoint
from app.utils.model_egress import guarded_async_client

TIMEOUT_S = 15.0
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_MODELS = 2000


def _status_error(status: int) -> DiscoveryHttpError:
    if status in (401, 403):
        return DiscoveryHttpError(DiscoveryErrorCode.AUTH_ERROR, "The provider rejected these credentials.")
    if status == 429:
        return DiscoveryHttpError(DiscoveryErrorCode.RATE_LIMITED, "The provider rate-limited model listing.")
    return DiscoveryHttpError(
        DiscoveryErrorCode.UNREACHABLE,
        f"The provider returned HTTP {status} while listing models.",
    )


class DiscoveryHttp:
    """GET JSON. Tests pass an ``httpx.MockTransport``; production uses the egress guard."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, url: str) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_S)
        return guarded_async_client(url, timeout=TIMEOUT_S)

    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        require_allowed_endpoint(url)
        try:
            client = self._client(url)
            try:
                response = await client.get(url, headers=headers or {})
            finally:
                await client.aclose()
        except httpx.TimeoutException as exc:
            raise DiscoveryHttpError(DiscoveryErrorCode.TIMEOUT, "Model listing timed out.") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.UNREACHABLE, "The model endpoint could not be reached."
            ) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.INVALID_RESPONSE, "The model list response is too large."
            )
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.INVALID_RESPONSE, "The provider did not return JSON."
            ) from exc

    async def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
        """Status, body, and headers. Used when a strategy needs the Link header."""
        require_allowed_endpoint(url)
        try:
            client = self._client(url)
            try:
                response = await client.get(url, headers=headers or {})
            finally:
                await client.aclose()
        except httpx.TimeoutException as exc:
            raise DiscoveryHttpError(DiscoveryErrorCode.TIMEOUT, "Model listing timed out.") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.UNREACHABLE, "The model endpoint could not be reached."
            ) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.INVALID_RESPONSE, "The model list response is too large."
            )
        return response.status_code, response.content, {k.lower(): v for k, v in response.headers.items()}


    async def post_json(self, url: str, body: dict, headers: dict[str, str] | None = None) -> Any:
        require_allowed_endpoint(url)
        try:
            client = self._client(url)
            try:
                response = await client.post(url, json=body, headers=headers or {})
            finally:
                await client.aclose()
        except httpx.TimeoutException as exc:
            raise DiscoveryHttpError(DiscoveryErrorCode.TIMEOUT, "Model listing timed out.") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.UNREACHABLE, "The model endpoint could not be reached."
            ) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.INVALID_RESPONSE, "The model list response is too large."
            )
        if response.status_code >= 400:
            raise _status_error(response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.INVALID_RESPONSE, "The provider did not return JSON."
            ) from exc


def bearer(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}
