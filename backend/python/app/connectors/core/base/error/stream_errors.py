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
        return HTTPException(
            HttpStatusCode.TOO_MANY_REQUESTS.value,
            _RATE_LIMITED.format(connector=name),
            headers={"Retry-After": retry_after} if retry_after else None,
        )
    if status == HttpStatusCode.GATEWAY_TIMEOUT.value:
        return HTTPException(HttpStatusCode.GATEWAY_TIMEOUT.value, _TIMEOUT.format(connector=name))
    if status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value:
        return HTTPException(HttpStatusCode.BAD_GATEWAY.value, _UNAVAILABLE.format(connector=name))

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


def to_stream_error(exc: BaseException, *, connector: str | None = None) -> HTTPException:
    """Best-effort mapping of an arbitrary exception onto an HTTP status."""
    if isinstance(exc, HTTPException):
        return exc

    # The OAuth layer already raises a dedicated error for "the provider
    # permanently rejected our refresh token", which is exactly reconnect.
    if _is_a(exc, "RefreshTokenInvalidError"):
        return HTTPException(
            HttpStatusCode.CONFLICT.value,
            _RECONNECT.format(connector=connector or _DEFAULT_CONNECTOR),
        )
    # ConnectorInitError's message is authored to be shown to the user.
    if _is_a(exc, "ConnectorInitError"):
        return HTTPException(HttpStatusCode.CONFLICT.value, str(exc))

    status = extract_source_status(exc)
    if status is not None:
        return map_source_status(status, connector=connector)

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or _is_timeout(exc):
        return HTTPException(
            HttpStatusCode.GATEWAY_TIMEOUT.value,
            _TIMEOUT.format(connector=connector or _DEFAULT_CONNECTOR),
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
)


def extract_source_status(exc: BaseException) -> int | None:
    """Best-effort read of the source API's HTTP status off an SDK exception."""
    for path in _STATUS_ATTR_PATHS:
        target: object = exc
        for attr in path:
            target = getattr(target, attr, None)
            if target is None:
                break
        if _is_http_status(target):
            return target  # type: ignore[return-value]

    # botocore/boto3 keep it inside a response dict.
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if _is_http_status(status):
            return status
    return None


def _is_http_status(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599


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
