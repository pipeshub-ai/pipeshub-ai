"""One retry classification for every model transport (MGW-20).

Each transport kept its own status set and its own idea of a network error, and they drifted: Ollama and
Bedrock never retried Anthropic-style 529s, Gemini retried a dropped connection or a timeout only when the
exception text happened to say so and dropped the status code the fallback policy reads, and Bedrock let a
bare ``OSError`` escape untyped. A transport reads the status off its own SDK's exception; whether that
failure is worth retrying is decided here, once.
"""

from __future__ import annotations

import httpx

from harness_core.kernel.core.exceptions import TransportError

# 529 is Anthropic's "overloaded_error", but any provider behind a gateway may relay it.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

# OSError covers ConnectionError and TimeoutError (asyncio's too); the httpx types are what an SDK that does
# not wrap its HTTP client lets through when the connection drops or a read times out.
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


def is_retryable(status_code: int | None, *, network: bool) -> bool:
    """A failure with a status is retried for that status alone; one without is retried when it was the network."""
    return status_code in RETRYABLE_STATUS_CODES if status_code is not None else network


def transport_error(
    message: str,
    exc: BaseException,
    *,
    status_code: int | None = None,
    network: tuple[type[BaseException], ...] = (),
) -> TransportError:
    """``exc`` as a ``TransportError``. ``network`` adds an SDK's own connection and timeout types."""
    return TransportError(
        message,
        status_code=status_code,
        retryable=is_retryable(status_code, network=isinstance(exc, NETWORK_ERRORS + network)),
    )


__all__ = ["NETWORK_ERRORS", "RETRYABLE_STATUS_CODES", "is_retryable", "transport_error"]
