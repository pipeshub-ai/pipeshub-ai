"""Plain-language failures for the agent's Google Workspace tools."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED"}
_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}


@dataclass(frozen=True)
class GoogleToolWording:
    """How one toolset names itself and its objects in failure messages."""

    product: str
    toolset: str
    access: str
    not_found: str
    gone: str


def _error_reasons(error: HttpError) -> set[str]:
    details = error.error_details if isinstance(error.error_details, list) else []
    return {d["reason"] for d in details if isinstance(d, dict) and isinstance(d.get("reason"), str)}


def google_error_message(error: Exception, action: str, wording: GoogleToolWording) -> str:
    """A failure the agent can relay; never the raw exception, which carries request URLs."""
    reconnect = f"Reconnect the {wording.toolset} toolset in Settings > Toolsets and try again."
    if isinstance(error, RefreshError):
        return f"Could not {action}: the Google sign-in has expired or was revoked. {reconnect}"
    if not isinstance(error, HttpError):
        return (
            f"Could not {action} because of an unexpected error. Try again, and if it keeps "
            f"failing, reconnect the {wording.toolset} toolset in Settings > Toolsets."
        )
    status = error.resp.status
    reasons = _error_reasons(error)
    if status == HTTPStatus.TOO_MANY_REQUESTS or reasons & _RATE_LIMIT_REASONS:
        retry_after = str(error.resp.get("retry-after") or "").strip()
        wait = f"Wait {retry_after} seconds" if retry_after.isdigit() else "Wait a minute"
        return f"{wording.product} is receiving too many requests right now, so it could not {action}. {wait} and try again."
    if status == HTTPStatus.UNAUTHORIZED:
        return f"Could not {action}: Google did not accept the saved sign-in. {reconnect}"
    if status == HTTPStatus.FORBIDDEN and reasons & _SCOPE_REASONS:
        return (
            f"Could not {action}: the connected Google account has not given this app permission to do that. "
            f"Reconnect the {wording.toolset} toolset in Settings > Toolsets and allow {wording.access}."
        )
    if status == HTTPStatus.NOT_FOUND:
        return f"Could not {action}: {wording.product} could not find {wording.not_found}"
    if status == HTTPStatus.GONE:
        return f"Could not {action}: {wording.gone}"
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return f"{wording.product} is having a temporary problem and could not {action}. Try again in a moment."
    return f"{wording.product} refused to {action}: {error.reason or f'HTTP {status}'}"
