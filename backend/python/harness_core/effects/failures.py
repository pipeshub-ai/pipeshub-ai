"""Why a tool call failed, as the person reads it: a ``ToolErrorCode`` beside the detail the model reads (ERR-UX).

The model gets the redacted detail so it can recover; the person gets words chosen by the code, never an
exception's or a provider's text. The code is decided by exception type and HTTP status through the cause chain,
never by message text: a message is the dependency's prose and may say anything.
"""

from __future__ import annotations

import asyncio
import math
import socket
from typing import TYPE_CHECKING

from harness_core.contracts.generated.enums import TOOL_ERROR_CODES, ToolErrorCode

if TYPE_CHECKING:
    from collections.abc import Iterator


class ToolFailure(Exception):
    """A failure whose code the raiser knows (a client that read its own protocol's error)."""

    def __init__(self, code: ToolErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code: ToolErrorCode = code


# Type names, matched anywhere in the MRO, of clients the harness reaches without importing them here.
_UNREACHABLE_TYPES = frozenset({
    "ConnectError",  # httpx
    "ConnectTimeout",  # httpx: never connected, so the service could not be reached
    "ClientConnectorError",  # aiohttp
    "ServerDisconnectedError",  # aiohttp
    "ClientOSError",  # aiohttp
    "RemoteProtocolError",  # httpx: the peer closed mid-answer
    "ReadError",  # httpx: the connection broke while reading
    "WriteError",  # httpx
    "APIConnectionError",  # openai
    "ServiceUnavailable",  # neo4j
    "SessionExpired",  # neo4j
    "MCPConnectionError",
    "BrokerUnavailable",
})
_TIMEOUT_TYPES = frozenset({"ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException", "APITimeoutError"})
_AUTH_TYPES = frozenset({"AuthenticationError", "PermissionDeniedError", "MCPOAuthError", "MCPTokenRefreshError"})
_RATE_LIMIT_TYPES = frozenset({"RateLimitError", "RateLimited", "TooManyRequests"})
_STATUS_CODES: dict[int, ToolErrorCode] = {
    401: "auth",
    403: "auth",
    404: "not_found",
    408: "timeout",
    429: "rate_limited",
    502: "unavailable",
    503: "unavailable",
    504: "timeout",
}


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(exc: BaseException) -> int | None:
    for name in ("status_code", "status", "http_status"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _one(exc: BaseException) -> ToolErrorCode | None:
    if isinstance(exc, ToolFailure):
        return exc.code
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & _UNREACHABLE_TYPES or isinstance(exc, (ConnectionError, socket.gaierror)):
        return "unavailable"
    if names & _TIMEOUT_TYPES or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if names & _RATE_LIMIT_TYPES:
        return "rate_limited"
    if names & _AUTH_TYPES:
        return "auth"
    status = _status(exc)
    return _STATUS_CODES.get(status) if status is not None else None


def tool_error_code(exc: BaseException) -> ToolErrorCode:
    """The code of a failed call: the first link of the cause chain that names one, else ``failed``."""
    for link in _chain(exc):
        code = _one(link)
        if code is not None:
            return code
    return "failed"


def _seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        try:
            seconds = float(value.strip())
        except ValueError:
            # An HTTP-date Retry-After needs a clock this module does not read; the caller backs off instead.
            return None
    else:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _header_retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    return _seconds(getter("retry-after") or getter("Retry-After"))


def retry_after_s(exc: BaseException) -> float | None:
    """How long the service asked to wait, from the first link of the cause chain that says: a client's
    ``retry_after_s`` / ``retry_after``, or the response's ``Retry-After`` seconds."""
    for link in _chain(exc):
        for name in ("retry_after_s", "retry_after"):
            seconds = _seconds(getattr(link, name, None))
            if seconds is not None:
                return seconds
        seconds = _header_retry_after(link)
        if seconds is not None:
            return seconds
    return None


def known_code(value: object) -> ToolErrorCode | None:
    """A code read back from a stored outcome; anything else is not one."""
    return value if isinstance(value, str) and value in TOOL_ERROR_CODES else None  # pyright: ignore[reportReturnType]


__all__ = ["ToolFailure", "known_code", "retry_after_s", "tool_error_code"]
