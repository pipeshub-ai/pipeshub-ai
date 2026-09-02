"""Map source-API failures onto HTTP statuses for the record-streaming paths.

Streaming failures used to collapse into a generic 500, or — worse — into a
hardcoded 404, so an expired connector token looked identical to a deleted
file. These helpers translate whatever the source API said into a status the
frontend can act on.

Everything here returns a plain ``HTTPException``: the many
``except HTTPException: raise`` sites across the connectors already propagate
it correctly, and no new response fields or handlers are needed.
"""

import asyncio
import socket
import ssl

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode

# A dead connector is reported as 409, not 403/401:
#   - 401 would trip the frontend's axios interceptor, which treats any 401 as
#     *our* session expiring and logs the user out of PipesHub entirely.
#   - 409 already means "connector unusable, go fix it" elsewhere in this
#     codebase (see _get_streaming_connector for a disabled connector), and the
#     frontend toast already renders 409 as "Action Required".
_RECONNECT = (
    "The connection to {connector} has expired or been revoked. "
    "Reconnect it from Connector Settings and try again."
)
_FORBIDDEN = (
    "Access to this item was denied by {connector}. The account PipesHub uses "
    "may not have permission to read it."
)
_NOT_FOUND = "This item no longer exists in {connector}. It may have been deleted or moved."
_NOT_DOWNLOADABLE = "This item cannot be downloaded from {connector} in its current format."
_RATE_LIMITED = "Too many requests to {connector}. Please try again shortly."
_UNAVAILABLE = "Could not reach {connector}. Please try again later."
_TIMEOUT = "The request to {connector} timed out. Please try again."
_UNKNOWN = "Could not retrieve this item. Please try again."

_DEFAULT_CONNECTOR = "the source"

# Not in HttpStatusCode; a source-side 408 is a timeout like 504.
_REQUEST_TIMEOUT = 408
_MAX_HTTP_STATUS = 599


def sanitize_retry_after(value: object) -> str | None:
    """Return *value* only if it is a safe ``Retry-After``.

    The value is upstream-controlled and goes straight onto a response header:
    a CRLF would let a self-hosted source inject one, and h11 rejects the
    result at write time, turning a correct 429 into a dropped connection.
    Only delta-seconds are accepted; an HTTP-date is dropped rather than parsed.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text.isdigit() else None


def map_source_status(
    status: int | None,
    *,
    connector: str | None = None,
    retry_after: str | None = None,
) -> HTTPException:
    """Translate a source API's HTTP status into one of ours."""
    name = connector or _DEFAULT_CONNECTOR

    # Callers pass through whatever the SDK handed them. A non-numeric status
    # is no usable signal — it must not blow up the comparisons below.
    if not isinstance(status, int) or isinstance(status, bool):
        return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)

    if status == HttpStatusCode.UNAUTHORIZED.value:
        return HTTPException(HttpStatusCode.CONFLICT.value, _RECONNECT.format(connector=name))
    if status == HttpStatusCode.FORBIDDEN.value:
        return HTTPException(HttpStatusCode.FORBIDDEN.value, _FORBIDDEN.format(connector=name))
    if status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
        return HTTPException(HttpStatusCode.NOT_FOUND.value, _NOT_FOUND.format(connector=name))
    if status == HttpStatusCode.TOO_MANY_REQUESTS.value:
        # Sanitized here rather than at each call site: the value is upstream
        # text going onto a response header, and callers pass it through raw.
        safe_retry_after = sanitize_retry_after(retry_after)
        return HTTPException(
            HttpStatusCode.TOO_MANY_REQUESTS.value,
            _RATE_LIMITED.format(connector=name),
            headers={"Retry-After": safe_retry_after} if safe_retry_after else None,
        )
    if status in (_REQUEST_TIMEOUT, HttpStatusCode.GATEWAY_TIMEOUT.value):
        return HTTPException(HttpStatusCode.GATEWAY_TIMEOUT.value, _TIMEOUT.format(connector=name))
    if HttpStatusCode.INTERNAL_SERVER_ERROR.value <= status <= _MAX_HTTP_STATUS:
        return HTTPException(HttpStatusCode.BAD_GATEWAY.value, _UNAVAILABLE.format(connector=name))
    if status > _MAX_HTTP_STATUS:
        return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)
    # Below 400 the source did not report a failure at all. Slack is the live
    # case: its errors ride in a 200 body, so the status read off the exception
    # is 200 and says nothing. Treating it as a 4xx would claim the item is
    # undownloadable on no evidence.
    if status < HttpStatusCode.BAD_REQUEST.value:
        return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)

    # Remaining 4xx: the source understood us and refused. The item is not
    # retrievable as requested, which is not a malformed-request 400.
    return HTTPException(
        HttpStatusCode.UNPROCESSABLE_ENTITY.value, _NOT_DOWNLOADABLE.format(connector=name)
    )


def not_downloadable(message: str, *, connector: str | None = None) -> HTTPException:
    """422 for an item the source will not serve in the requested form."""
    return HTTPException(
        HttpStatusCode.UNPROCESSABLE_ENTITY.value,
        message or _NOT_DOWNLOADABLE.format(connector=connector or _DEFAULT_CONNECTOR),
    )


def connector_not_ready(connector: str | None = None) -> HTTPException:
    """409 when the connector has no live client — it failed to initialise.

    Reported separately from 404 so the user isn't told their file was deleted
    when the connector is what's broken.
    """
    return HTTPException(
        HttpStatusCode.CONFLICT.value,
        f"The connector '{connector or _DEFAULT_CONNECTOR}' is not connected. "
        "Check its settings and try again.",
    )


def not_found_at_source(connector: str | None = None) -> HTTPException:
    """404 for an item that is genuinely absent at the source."""
    return HTTPException(
        HttpStatusCode.NOT_FOUND.value,
        _NOT_FOUND.format(connector=connector or _DEFAULT_CONNECTOR),
    )


def raise_for_stream_fetch(
    *,
    success: bool,
    has_payload: bool,
    connector: str | None = None,
    status: int | None = None,
    message: str | None = None,
) -> None:
    """Map a source fetch result onto a stream HTTPException, or return if OK.

    Rules (match the stream_errors contract):
      - success + payload → return (caller continues)
      - any 4xx/5xx status → map_source_status, whatever `success` claims
      - success + empty payload → 404 (resource genuinely absent)
      - failure with no usable status → raise RuntimeError so to_stream_error →
        generic 500 (never invent a 404 for auth/rate-limit/5xx)

    The status outranks `success` because several envelopes report both: a
    GraphQL client sets success from the body's `errors` key alone, so a 401
    whose body has no `errors` arrives as success=True with an empty payload —
    and reporting that as "deleted" is the exact lie this module exists to stop.
    """
    if success and has_payload:
        return
    if _is_error_status(status):
        raise map_source_status(status, connector=connector)  # type: ignore[arg-type]
    if success:
        raise not_found_at_source(connector)
    raise RuntimeError(message or "Failed to fetch from source")


_INTERNAL_UNAVAILABLE = "Could not retrieve this item right now. Please try again later."


def internal_service_status(status: int | None) -> HTTPException:
    """Map a status from one of PipesHub's own services onto ours.

    See :func:`to_internal_service_error` for why 401/403 are not a reconnect.
    """
    if status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
        return HTTPException(HttpStatusCode.NOT_FOUND.value, _NOT_FOUND.format(connector="storage"))
    if _is_error_status(status):
        return HTTPException(HttpStatusCode.BAD_GATEWAY.value, _INTERNAL_UNAVAILABLE)
    return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)


def to_internal_service_error(exc: BaseException) -> HTTPException:
    """Map a failure from one of PipesHub's own services (storage, indexing).

    Distinct from :func:`to_stream_error` because there is no connector to
    reconnect: a 401/403 here means our own service rejected our own scoped
    token, which is a deployment fault the user cannot act on. Only a genuine
    404 is theirs to see.
    """
    if isinstance(exc, HTTPException):
        return exc

    unwrapped = _unwrap_retry_error(exc)
    if unwrapped is not None:
        return to_internal_service_error(unwrapped)

    status = extract_source_status(exc)
    if status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.GONE.value):
        return HTTPException(HttpStatusCode.NOT_FOUND.value, _NOT_FOUND.format(connector="storage"))
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or _is_timeout(exc):
        return HTTPException(HttpStatusCode.GATEWAY_TIMEOUT.value, _INTERNAL_UNAVAILABLE)
    if status is not None:
        return HTTPException(HttpStatusCode.BAD_GATEWAY.value, _INTERNAL_UNAVAILABLE)
    return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)


def to_stream_error(exc: BaseException, *, connector: str | None = None) -> HTTPException:
    """Best-effort mapping of an arbitrary exception onto an HTTP status."""
    if isinstance(exc, HTTPException):
        return exc

    # tenacity re-raises the last failure wrapped unless the caller passed
    # `reraise=True` (`utils/api_call.py` does not), so every retried status —
    # 429 and 5xx included — arrives here as an opaque RetryError.
    unwrapped = _unwrap_retry_error(exc)
    if unwrapped is not None and unwrapped is not exc:
        return to_stream_error(unwrapped, connector=connector)

    # The OAuth layer already raises a dedicated error for "the provider
    # permanently rejected our refresh token", which is exactly reconnect.
    # google.auth's RefreshError is the same condition from Google's SDK.
    if _is_a(exc, "RefreshTokenInvalidError") or (
        _is_a(exc, "RefreshError") and not getattr(exc, "retryable", False)
    ):
        return HTTPException(
            HttpStatusCode.CONFLICT.value,
            _RECONNECT.format(connector=connector or _DEFAULT_CONNECTOR),
        )
    # ConnectorInitError's message is authored to be shown to the user.
    if _is_a(exc, "ConnectorInitError"):
        return HTTPException(HttpStatusCode.CONFLICT.value, str(exc))

    status = extract_source_status(exc)
    if status is not None:
        if status == HttpStatusCode.FORBIDDEN.value and _is_rate_limit_403(exc):
            status = HttpStatusCode.TOO_MANY_REQUESTS.value
        return map_source_status(status, connector=connector, retry_after=_extract_retry_after(exc))

    # Dropbox reports path failures through a tagged union on ApiError rather
    # than an HTTP status, so a deleted file has no status to read.
    if _is_a(exc, "ApiError") and "not_found" in str(exc):
        return not_found_at_source(connector)

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or _is_timeout(exc):
        return HTTPException(
            HttpStatusCode.GATEWAY_TIMEOUT.value,
            _TIMEOUT.format(connector=connector or _DEFAULT_CONNECTOR),
        )

    # DNS, TLS and refused/reset connections never produce a status, so they
    # would otherwise land in the generic 500 below. This is the dominant
    # failure for self-hosted sources (Confluence DC, GitLab, Nextcloud, MinIO).
    if _is_unreachable(exc):
        return HTTPException(
            HttpStatusCode.BAD_GATEWAY.value,
            _UNAVAILABLE.format(connector=connector or _DEFAULT_CONNECTOR),
        )

    # Unclassified: the message can carry tokens, signed URLs or internal
    # hostnames, so the caller logs it and the client gets a stable string.
    return HTTPException(HttpStatusCode.INTERNAL_SERVER_ERROR.value, _UNKNOWN)


# Where each SDK hides the HTTP status on its exception type. Reading these
# directly saves a per-connector translation layer: googleapiclient
# (resp.status), msgraph (response_status_code), box_sdk_gen
# (response_info.status_code), httpx/requests (response.status_code).
_STATUS_ATTR_PATHS: tuple[tuple[str, ...], ...] = (
    ("status_code",),
    ("status",),
    ("response_status_code",),
    ("resp", "status"),
    ("response", "status_code"),
    ("response", "status"),
    ("response_info", "status_code"),
    ("code",),
)


def extract_source_status(exc: BaseException) -> int | None:
    """Best-effort read of the source API's HTTP status off an SDK exception."""
    for path in _STATUS_ATTR_PATHS:
        target: object = exc
        for attr in path:
            target = getattr(target, attr, None)
            if target is None:
                break
        if _is_error_status(target):
            return target  # type: ignore[return-value]

    # botocore/boto3 keep it inside a response dict.
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if _is_error_status(status):
            return status
    return None


def _is_http_status(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599


def _is_error_status(value: object) -> bool:
    """A status that actually reports a failure.

    Only 4xx/5xx count as a signal. Slack raises `SlackApiError` carrying a
    *200* response — the failure is in the JSON body — and reading that 200 as
    the outcome would classify every Slack error as "undownloadable".
    """
    return _is_http_status(value) and value >= HttpStatusCode.BAD_REQUEST.value  # type: ignore[operator]


def _is_a(exc: BaseException, class_name: str) -> bool:
    """Match by class name across the exception's MRO.

    Importing the defining modules would pull the connector and messaging
    stacks into what is otherwise a dependency-free helper, and an ImportError
    there would break error handling itself.
    """
    return any(base.__name__ == class_name for base in type(exc).__mro__)


def _is_timeout(exc: BaseException) -> bool:
    """Detect library timeouts without importing every HTTP client eagerly."""
    return type(exc).__name__ in {
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "PoolTimeout",
        "ServerTimeoutError",
        "ConnectionTimeoutError",
        "SocketTimeoutError",
    }


def _unwrap_retry_error(exc: BaseException) -> BaseException | None:
    """Return the failure a retry wrapper swallowed, if this is one.

    Three unrelated SDK families ship a class named ``RetryError`` and they do
    not agree on where the original goes: tenacity uses ``last_attempt``,
    google.api_core uses ``cause``, and requests' carries neither.
    """
    if not _is_a(exc, "RetryError"):
        return None
    cause = getattr(exc, "cause", None)
    if isinstance(cause, BaseException):
        return cause
    last_attempt = getattr(exc, "last_attempt", None)
    if last_attempt is None or not getattr(last_attempt, "failed", False):
        return None
    try:
        inner = last_attempt.exception()
    except Exception:
        return None
    return inner if isinstance(inner, BaseException) else None


def _extract_retry_after(exc: BaseException) -> str | None:
    """Read a Retry-After header off an SDK exception's response, if present."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    return sanitize_retry_after(value)


# Transport-level failures, matched without importing every HTTP client.
_UNREACHABLE_EXCEPTIONS = frozenset({
    "ClientConnectorError",
    "ServerDisconnectedError",
    "ClientPayloadError",
    "ConnectError",
    "ReadError",
    "RemoteProtocolError",
})


def _is_unreachable(exc: BaseException) -> bool:
    """True when the source could not be reached at all (DNS/TLS/refused/reset)."""
    if isinstance(exc, (ConnectionError, socket.gaierror, ssl.SSLError)):
        return True
    return any(base.__name__ in _UNREACHABLE_EXCEPTIONS for base in type(exc).__mro__)


# Google reports quota exhaustion as 403, not 429; only `reason` tells it apart
# from a genuine permission loss. Getting this wrong is expensive: the indexing
# consumer treats 403 as permanent, so a transient quota blip would fail records
# for good. Mirrors `google/drive/utils/folder_filter_utils.is_retryable_403`.
_RATE_LIMIT_403_REASONS = frozenset({
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "quotaExceeded",
    "dailyLimitExceeded",
    "backendError",
})


def _is_rate_limit_403(exc: BaseException) -> bool:
    details = getattr(exc, "error_details", None)
    if not isinstance(details, (list, tuple)):
        return False
    reasons = {d.get("reason") for d in details if isinstance(d, dict)}
    return bool(reasons & _RATE_LIMIT_403_REASONS)
