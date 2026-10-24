"""Sends one tool call to the API and reads the answer.

Requests go through the same SSRF guard as MCP traffic: the admin's base URL may be on
a private network (unless the deployment says otherwise), loopback, link-local and cloud
metadata addresses never may, and a request can't leave the base URL's origin. Redirects
are not followed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import httpx

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import GuardedTransport
from app.agents.openapi.request_builder import build_request
from app.utils.url_redaction import redact_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agents.openapi.auth import AuthApplier
    from app.agents.openapi.models import CompiledOperation

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
# What the model is shown of one response. Larger answers are cut, and say so.
MAX_RESPONSE_BYTES = 256 * 1024


class ApiCallError(Exception):
    """The call didn't produce an HTTP response. The message is safe to show."""


@dataclass(frozen=True)
class ApiResponse:
    status: int
    content_type: str
    body: Any
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class OpenAPIHttpExecutor:
    """Calls the operations of one toolset instance against its base URL."""

    def __init__(
        self,
        base_url: str,
        auth: AuthApplier,
        *,
        allow_private: bool,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url
        self._auth = auth
        self._allow_private = allow_private
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        # Tests inject a mock; production always gets the guard.
        self._transport = transport

    async def call(self, op: CompiledOperation, arguments: Mapping[str, Any]) -> ApiResponse:
        response = await self._send(op, arguments, force_refresh=False)
        if response.status == 401 and self._auth.can_refresh:
            response = await self._send(op, arguments, force_refresh=True)
        return response

    async def _send(self, op: CompiledOperation, arguments: Mapping[str, Any], *, force_refresh: bool) -> ApiResponse:
        request = build_request(op, arguments, self._base_url)
        # Also when the spec lets the operation run anonymously (`{}` among its security options, as
        # Jira's search does): the API then answers as nobody, with nothing in it.
        await self._auth.apply(request, force_refresh=force_refresh)
        transport = self._transport or GuardedTransport(self._base_url, allow_private=self._allow_private)
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=self._timeout, follow_redirects=False, trust_env=False,
            ) as client:
                response = await client.send(request, stream=True)
                try:
                    raw, truncated = await self._read_capped(response)
                finally:
                    await response.aclose()
        except MCPUrlBlockedError as e:
            raise ApiCallError(
                "The toolset's base URL is not allowed: it must not point to a loopback, link-local or "
                "cloud metadata address, and requests can't leave its host."
            ) from e
        except httpx.TimeoutException as e:
            raise ApiCallError(f"The API did not respond within {self._timeout:.0f} seconds.") from e
        except httpx.HTTPError as e:
            logger.warning("API toolset call to %s failed: %s", redact_url(self._base_url), type(e).__name__)
            raise ApiCallError(f"Could not reach the API ({type(e).__name__}).") from e
        content_type = response.headers.get("content-type", "")
        return ApiResponse(response.status_code, content_type, _decode(raw, content_type, truncated=truncated), truncated)

    async def _read_capped(self, response: httpx.Response) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > self._max_bytes:
                return b"".join(chunks)[: self._max_bytes], True
        return b"".join(chunks), False


def _decode(raw: bytes, content_type: str, *, truncated: bool) -> Any:  # noqa: ANN401
    media = content_type.split(";", 1)[0].strip().lower()
    if not raw:
        return None
    if not truncated and (media == "application/json" or media.endswith("+json")):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    if media.startswith("text/") or "json" in media or "xml" in media or not media:
        return raw.decode("utf-8", errors="replace")
    return f"({len(raw)} bytes of {media or 'binary data'}, not shown)"
