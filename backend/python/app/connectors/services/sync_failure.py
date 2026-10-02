"""Classify connector sync failures for org-facing UI copy.

Full stack traces stay in logs. Redis and the API only carry a short, cleaned
reason plus a coarse code the frontend maps to remediation steps.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from typing import Any, NamedTuple

FAILURE_REASON_MAX_CHARS = 180

# OAuth / API error codes → short org-facing explanations (English; FE i18n
# can still override via failureCode).
_FRIENDLY_BY_ERROR_CODE: dict[str, str] = {
    "unauthorized_client": (
        "Google blocked this app from getting access tokens for the requested scopes."
    ),
    "invalid_grant": "The saved Google sign-in expired or was revoked.",
    "invalid_client": "The Google app credentials look invalid or were revoked.",
    "access_denied": "Access was denied while authorizing this connector.",
    "insufficientpermissions": "This account is missing permission to read the content.",
    "insufficientfilepermissions": "This account cannot access one or more Drive files.",
    "teamdrivemembershiprequired": "This account is not a member of a required Shared Drive.",
    "userratelimitexceeded": "Google rate-limited this sync.",
    "ratelimitexceeded": "Google rate-limited this sync.",
    "quotaexceeded": "A Google API quota was exceeded.",
}


class SyncFailureCode:
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    PERMISSION = "PERMISSION"
    NETWORK = "NETWORK"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ClassifiedSyncFailure(NamedTuple):
    code: str
    reason: str


def _truncate(text: str) -> str:
    text = " ".join(text.split()).strip()
    if len(text) > FAILURE_REASON_MAX_CHARS:
        return text[: FAILURE_REASON_MAX_CHARS - 1].rstrip() + "…"
    return text


def _from_mapping(data: dict[str, Any]) -> str | None:
    for key in (
        "error_description",
        "errorDescription",
        "message",
        "detail",
        "error_message",
        "description",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    err = data.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip().replace("_", " ")
    if isinstance(err, dict):
        return _from_mapping(err)
    return None


def _extract_error_code(text: str, parsed: Any | None = None) -> str | None:
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip().lower()
        if isinstance(err, dict) and isinstance(err.get("code"), str):
            return err["code"].strip().lower()
    match = re.search(
        r"\b([a-z][a-z0-9_]{2,40})\s*:\s*",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).lower()
    return None


def _parse_structured(text: str) -> Any | None:
    """Best-effort parse of Python/JSON blobs Google auth libs put in str(exc)."""
    stripped = text.strip()
    for candidate in (stripped, stripped.strip("\"'")):
        try:
            return ast.literal_eval(candidate)
        except (ValueError, SyntaxError, MemoryError):
            pass
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
    # Common shape: ('message', {'error': '...', ...})
    if stripped.startswith("(") and ", {" in stripped:
        try:
            return ast.literal_eval(stripped)
        except (ValueError, SyntaxError, MemoryError):
            return None
    return None


def _humanize_structured(parsed: Any) -> str | None:
    if isinstance(parsed, dict):
        return _from_mapping(parsed)
    if isinstance(parsed, (list, tuple)) and parsed:
        # Prefer nested mapping description; fall back to first string.
        for item in parsed:
            if isinstance(item, dict):
                msg = _from_mapping(item)
                if msg:
                    return msg
        for item in parsed:
            if isinstance(item, str) and item.strip():
                # "unauthorized_client: Client is unauthorized…"
                part = item.strip()
                if ": " in part:
                    code, _, rest = part.partition(": ")
                    if rest and code.isidentifier():
                        return rest.strip()
                return part
    return None


def _sanitize_reason(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    parsed = _parse_structured(text)
    error_code = _extract_error_code(text, parsed)
    if error_code and error_code in _FRIENDLY_BY_ERROR_CODE:
        return _FRIENDLY_BY_ERROR_CODE[error_code]

    human = _humanize_structured(parsed) if parsed is not None else None
    if human:
        # Drop duplicated "code: message" when message already repeats the idea.
        if error_code and human.lower().startswith(error_code):
            human = human.split(":", 1)[-1].strip() or human
        return _truncate(human)

    # Strip obvious Python repr noise from unparsed strings.
    cleaned = text
    if cleaned.startswith("(") and cleaned.endswith(")"):
        # Keep the first quoted segment when present.
        quoted = re.search(r"['\"]([^'\"]{8,})['\"]", cleaned)
        if quoted:
            cleaned = quoted.group(1)
    cleaned = cleaned.replace("\\n", " ")
    return _truncate(cleaned)


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "resp", None) or getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status", None) or getattr(resp, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def classify_sync_failure(exc: BaseException) -> ClassifiedSyncFailure:
    """Map an exception to a coarse failure code and a short display reason."""
    if isinstance(exc, asyncio.CancelledError):
        return ClassifiedSyncFailure(
            SyncFailureCode.CANCELLED,
            "Sync was cancelled before it finished.",
        )

    reason = _sanitize_reason(exc)
    haystack = f"{type(exc).__name__} {exc!s} {reason}".lower()
    status = _http_status(exc)

    auth_tokens = (
        "invalid_grant",
        "unauthorized_client",
        "unauthorized",
        "unauthenticated",
        "refresherror",
        "credentials",
        "token has been expired",
        "token expired",
        "authentication",
        "re-auth",
        "reauth",
        "login required",
    )
    permission_tokens = (
        "insufficientpermissions",
        "insufficientfilepermissions",
        "teamdrivemembershiprequired",
        "forbidden",
        "accessdenied",
        "permission denied",
        "not authorized to",
        "does not have permission",
    )
    rate_limit_tokens = (
        "ratelimit",
        "rate limit",
        "rate_limit",
        "quotaexceeded",
        "quota exceeded",
        "userratelimitexceeded",
        "resourcestatequota",
        "too many requests",
    )
    network_tokens = (
        "timeout",
        "timed out",
        "connectionerror",
        "clientconnectorerror",
        "temporary failure in name resolution",
        "name or service not known",
        "network is unreachable",
        "connection reset",
        "connection refused",
    )

    if status == 429 or any(token in haystack for token in rate_limit_tokens):
        return ClassifiedSyncFailure(SyncFailureCode.RATE_LIMIT, reason)

    if status == 401 or any(token in haystack for token in auth_tokens):
        return ClassifiedSyncFailure(SyncFailureCode.AUTH, reason)

    if status == 403 or any(token in haystack for token in permission_tokens):
        return ClassifiedSyncFailure(SyncFailureCode.PERMISSION, reason)

    if any(token in haystack for token in network_tokens):
        return ClassifiedSyncFailure(SyncFailureCode.NETWORK, reason)

    return ClassifiedSyncFailure(SyncFailureCode.UNKNOWN, reason)
