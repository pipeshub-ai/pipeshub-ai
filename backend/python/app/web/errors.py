"""Typed failures for web search and fetch.

A failure is always one of these, never an empty result: the model is told what went wrong
(``error_type``) and whether trying again could help, and the fallback chain decides from the
type whether to retry the same source or move on.
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from app.services.base_client import parse_retry_after

_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500


class WebError(Exception):
    error_type: ClassVar[str] = "upstream_error"
    default_retryable: ClassVar[bool] = False
    # About the address itself, not the source that tried it: no other source may try it either.
    ends_chain: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.status = status
        self.retry_after_s = retry_after_s
        # About one page (the site refused it, rate limited it), not about the source that read it:
        # such a failure never makes the source sit out other pages.
        self.page_level = False

    @property
    def retryable(self) -> bool:
        return self.default_retryable

    def to_tool_payload(self) -> dict[str, Any]:
        """The only shape a web tool returns on failure."""
        return {
            "ok": False,
            "error_type": self.error_type,
            "error": self.message,
            "retryable": self.retryable,
        }


class Blocked(WebError):
    error_type = "blocked"


class AuthFailed(WebError):
    error_type = "auth_failed"


class QuotaExceeded(WebError):
    error_type = "quota_exceeded"


class RateLimited(WebError):
    error_type = "rate_limited"
    default_retryable = True


class TimedOut(WebError):
    error_type = "timeout"
    default_retryable = True


class Unreachable(WebError):
    error_type = "unreachable"
    default_retryable = True


class UpstreamError(WebError):
    error_type = "upstream_error"

    @property
    def retryable(self) -> bool:
        return self.status is not None and self.status >= _HTTP_SERVER_ERROR


class NotConfigured(WebError):
    error_type = "not_configured"

    def __init__(self, message: str, *, source: str | None = None, reason: str = "api_key") -> None:
        super().__init__(message, source=source)
        # What is missing, as a fixed code: callers that answer people outside the agent (the
        # settings health check) word it themselves instead of echoing the message.
        self.reason = reason


class UnknownProvider(WebError):
    error_type = "unknown_provider"


class InvalidUrl(WebError):
    error_type = "invalid_url"
    ends_chain = True


class UnsafeUrl(WebError):
    error_type = "unsafe_url"
    ends_chain = True


class TooLarge(WebError):
    error_type = "too_large"


class TooManyRedirects(WebError):
    error_type = "too_many_redirects"
    ends_chain = True


class UnsupportedContent(WebError):
    error_type = "unsupported_content_type"


class NoText(WebError):
    error_type = "no_text"


class AccessDenied(WebError):
    error_type = "access_denied"
    ends_chain = True


class BudgetExhausted(WebError):
    error_type = "budget_exhausted"


def error_for_status(status: int, headers: Mapping[str, str], *, source: str) -> WebError | None:
    """The error a search API's HTTP status means, or None for a success."""
    if status < _HTTP_BAD_REQUEST:
        return None
    if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
        return AuthFailed(f"{source} rejected the API key (HTTP {status})", source=source, status=status)
    if status == _HTTP_PAYMENT_REQUIRED:
        return QuotaExceeded(f"{source} quota or credits are used up (HTTP {status})", source=source, status=status)
    if status == _HTTP_TOO_MANY_REQUESTS:
        return RateLimited(
            f"{source} is rate limiting requests (HTTP {status})",
            source=source,
            status=status,
            retry_after_s=parse_retry_after(headers.get("retry-after") or headers.get("Retry-After")),
        )
    return UpstreamError(f"{source} returned HTTP {status}", source=source, status=status)
