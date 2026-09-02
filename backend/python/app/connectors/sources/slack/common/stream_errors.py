"""Map Slack's in-body error codes onto HTTP statuses for streaming failures.

Slack reports most failures as ``ok: false`` inside a 200 response, so the
transport status usually carries no signal. ``SlackDataSource._handle_slack_error``
preserves the raw Slack code on ``SlackResponse.error``, and that code is the only
evidence available for telling an expired token apart from a deleted file. Rate
limiting is the exception: it arrives as a real HTTP 429 with a Retry-After, which
is why the transport status and header are carried on the envelope too.
"""

from typing import Any, Optional

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    map_source_status,
    not_found_at_source,
    sanitize_retry_after,
)

_SLACK_ERROR_STATUS: dict[str, int] = {
    "invalid_auth": HttpStatusCode.UNAUTHORIZED.value,
    "token_revoked": HttpStatusCode.UNAUTHORIZED.value,
    "account_inactive": HttpStatusCode.UNAUTHORIZED.value,
    "not_authed": HttpStatusCode.UNAUTHORIZED.value,
    "missing_scope": HttpStatusCode.FORBIDDEN.value,
    "not_allowed_token_type": HttpStatusCode.FORBIDDEN.value,
    "not_in_channel": HttpStatusCode.FORBIDDEN.value,
    "channel_not_found": HttpStatusCode.FORBIDDEN.value,
    "ratelimited": HttpStatusCode.TOO_MANY_REQUESTS.value,
    "rate_limited": HttpStatusCode.TOO_MANY_REQUESTS.value,
    "file_not_found": HttpStatusCode.NOT_FOUND.value,
    "file_deleted": HttpStatusCode.NOT_FOUND.value,
    "message_not_found": HttpStatusCode.NOT_FOUND.value,
    "thread_not_found": HttpStatusCode.NOT_FOUND.value,
}

_MAX_HTTP_STATUS = 599


def _transport_status(response: Any) -> Optional[int]:  # noqa: ANN401 - SlackResponse or None
    """The source's HTTP status, when it actually reports a failure.

    Slack answers most failures with 200 and an in-body code, so only a 4xx/5xx
    carries any signal — a 200 here would otherwise be read as "undownloadable".
    """
    status = getattr(response, "status_code", None) if response else None
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    return status if HttpStatusCode.BAD_REQUEST.value <= status <= _MAX_HTTP_STATUS else None


def slack_stream_error(
    response: Any,  # noqa: ANN401 - SlackResponse or None
    *,
    connector: Optional[str] = None,
) -> HTTPException:
    """Translate a failed ``SlackResponse`` into an HTTPException to raise."""
    code = getattr(response, "error", None) if response else None
    status = _SLACK_ERROR_STATUS.get(code.strip()) if isinstance(code, str) else None
    retry_after = sanitize_retry_after(getattr(response, "retry_after", None) if response else None)
    if status is None:
        # Slack rate-limits with a real HTTP 429, and a proxy in front of it can
        # fail before any Slack code exists, so the transport status is the last
        # piece of evidence. Without one, an unrecognised code is no evidence the
        # item is gone: a generic 500 beats claiming a deletion that may not have
        # happened.
        transport = _transport_status(response)
        if transport is None:
            return map_source_status(None, connector=connector)
        return map_source_status(transport, connector=connector, retry_after=retry_after)
    if status == HttpStatusCode.NOT_FOUND.value:
        return not_found_at_source(connector)
    return map_source_status(status, connector=connector, retry_after=retry_after)
