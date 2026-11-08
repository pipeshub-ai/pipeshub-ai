"""What a Gmail or Google sign-in failure means for a sync: retry it, skip it, or tell someone.

Google reports the same few situations through different layers: the token endpoint (google-auth's
RefreshError, sometimes wrapped by our own exceptions) and the API front end (googleapiclient's HttpError).
The connectors only need the meaning, so this module reads the error chain once and names it.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from googleapiclient.errors import HttpError

_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_RETRY_AFTER = re.compile(r"Retry after (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


class GmailFailure(str, Enum):
    """Why a call failed, in the terms a sync acts on."""

    RATE_LIMITED = "rate_limited"  # wait and retry
    TRANSIENT = "transient"  # 5xx or transport: retry, then try again next sync
    NOT_FOUND = "not_found"  # the item is gone (or never was) at the source
    HISTORY_EXPIRED = "history_expired"  # only from users.history.list: re-read the mailbox
    DELEGATION_REFUSED = "delegation_refused"  # domain-wide delegation not set up for the scopes
    KEY_REJECTED = "key_rejected"  # the service account key is wrong, deleted or disabled
    USER_UNAVAILABLE = "user_unavailable"  # the impersonated user is deleted or suspended
    MAIL_DISABLED = "mail_disabled"  # Gmail is turned off, or not licensed, for this user
    MISSING_SCOPE = "missing_scope"  # the token lacks a scope the call needs
    RECONNECT = "reconnect"  # a personal connection's sign-in was revoked or expired
    OTHER = "other"


#: Failures a person has to fix; a later sync cannot recover from them by itself.
NEEDS_ATTENTION = {GmailFailure.DELEGATION_REFUSED, GmailFailure.KEY_REJECTED, GmailFailure.MISSING_SCOPE,
                   GmailFailure.RECONNECT}


def _chain(error: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    out: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        out.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return out


def _http_reason(error: HttpError) -> str:
    try:
        body = json.loads(error.content.decode("utf-8", "replace")) if error.content else {}
    except (ValueError, AttributeError):
        return ""
    details = (body.get("error") or {}).get("errors") or []
    return str(details[0].get("reason", "")) if details and isinstance(details[0], dict) else ""


def classify(error: BaseException) -> GmailFailure:
    """The meaning of a failed Gmail, Directory or Drive call."""
    for exc in _chain(error):
        text = str(exc)
        if "unauthorized_client" in text or "access_denied" in text:
            return GmailFailure.DELEGATION_REFUSED
        if "Invalid JWT Signature" in text or "invalid_client" in text or "account not found" in text.lower():
            return GmailFailure.KEY_REJECTED
        if "Invalid email or User ID" in text or "invalid_account" in text:
            return GmailFailure.USER_UNAVAILABLE
        if "Token has been expired or revoked" in text or ("invalid_grant" in text and "JWT" not in text):
            return GmailFailure.RECONNECT
        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 0) or 0)
            reason = _http_reason(exc)
            if status == 429 or (status == 403 and reason in _RATE_REASONS):
                return GmailFailure.RATE_LIMITED
            if status >= 500:
                return GmailFailure.TRANSIENT
            if status == 404:
                return GmailFailure.NOT_FOUND
            if status == 400 and "Mail service not enabled" in text:
                return GmailFailure.MAIL_DISABLED
            if status == 403 and reason == "insufficientPermissions":
                return GmailFailure.MISSING_SCOPE
            if status == 401:
                return GmailFailure.RECONNECT
            return GmailFailure.OTHER
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return GmailFailure.TRANSIENT
        name = type(exc).__name__
        if name in {"ServerNotFoundError", "RemoteDisconnected", "IncompleteRead", "ProtocolError",
                    "TransportError", "SSLError"}:
            return GmailFailure.TRANSIENT
    return GmailFailure.OTHER


def retry_after_seconds(error: BaseException, now_s: float) -> float | None:
    """Seconds until the time a rate-limit error names ("Retry after <time>"), if it names one."""
    import datetime

    for exc in _chain(error):
        match = _RETRY_AFTER.search(str(exc))
        if match:
            stamp = match.group(1).replace("Z", "+00:00")
            try:
                when = datetime.datetime.fromisoformat(stamp).timestamp()
            except ValueError:
                return None
            return max(0.0, when - now_s)
    return None
