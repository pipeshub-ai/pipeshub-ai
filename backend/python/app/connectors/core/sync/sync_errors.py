"""Why a sync failed, in terms a person can act on.

``classify_sync_error`` turns whatever ``run_sync`` raised into a stable code
the UI words and pairs with one action (reconnect, grant access, wait, check
the address). It reads what every connector's errors already carry: an HTTP
status on the exception or its response, the exception's own class names
(``ZendeskAuthError``, ``AtlassianQuotaExhausted``, ``DriveSyncIncomplete``),
and, last, well-known phrases in the message. It walks the cause chain,
because connectors often wrap the transport error in their own.

The detail kept beside the code is for admins and is scrubbed: query strings
and anything shaped like a token are removed before it is stored.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class SyncErrorCode(str, Enum):
    AUTH = "auth"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    SOURCE_UNAVAILABLE = "source_unavailable"
    NOT_FOUND = "not_found"
    CONFIG = "config"
    INCOMPLETE = "incomplete"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


_DETAIL_LIMIT = 300
_CHAIN_LIMIT = 6

# Checked in this order: a class named for what went wrong beats a status code
# on some inner response, and a status code beats a phrase in a message.
# Each needle is one or more whole CamelCase words run together, so ``auth``
# matches ``OAuthError`` and ``ZendeskAuthError`` but not ``AuthorNotFound``.
_NAME_RULES: tuple[tuple[tuple[str, ...], SyncErrorCode], ...] = (
    (("incomplete",), SyncErrorCode.INCOMPLETE),
    (("quota", "ratelimit", "ratelimited", "throttle", "throttled", "throttling"), SyncErrorCode.RATE_LIMIT),
    (
        (
            "auth", "authentication", "unauthorized", "unauthenticated", "credential", "credentials",
            "passwordrejected", "tokeninvalid", "serviceaccountkey",
        ),
        SyncErrorCode.AUTH,
    ),
    (
        ("permission", "permissions", "accessdenied", "forbidden", "delegation", "nofilesystemaccess"),
        SyncErrorCode.PERMISSION,
    ),
    (("notfound",), SyncErrorCode.NOT_FOUND),
    (("config", "configuration", "notconfigured", "addressnotallowed", "unsafeaddress"), SyncErrorCode.CONFIG),
    (("offline", "unreachable", "unavailable"), SyncErrorCode.SOURCE_UNAVAILABLE),
    (("timeout", "connection", "connect", "network"), SyncErrorCode.NETWORK),
)
_CAMEL_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

_MESSAGE_RULES: tuple[tuple[re.Pattern[str], SyncErrorCode], ...] = (
    (re.compile(r"\brate.?limit|too many requests|\bquota|\bthrottl|\b429\b", re.I), SyncErrorCode.RATE_LIMIT),
    (
        re.compile(
            r"invalid_grant|invalid_client|unauthori[sz]ed|token (has )?expired|expired token|"
            r"re-?auth|invalid credentials|authentication failed|\b401\b",
            re.I,
        ),
        SyncErrorCode.AUTH,
    ),
    (
        re.compile(r"\bpermission|\bforbidden\b|access denied|insufficient.*scope|not authori[sz]ed to|\b403\b", re.I),
        SyncErrorCode.PERMISSION,
    ),
    (
        re.compile(
            r"timed? ?out|connection (refused|reset|aborted|error)|name or service not known|"
            r"temporary failure in name resolution|getaddrinfo|network is unreachable|\bssl\b",
            re.I,
        ),
        SyncErrorCode.NETWORK,
    ),
    (re.compile(r"service unavailable|bad gateway|internal server error|\b50[0234]\b", re.I), SyncErrorCode.SOURCE_UNAVAILABLE),
)

_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=.]{32,}")
_QUERY = re.compile(r"(https?://[^\s?#'\"]+)\?[^\s'\"]*")
_SECRET_PAIR = re.compile(
    r"(?i)\b(access_token|refresh_token|token|secret|password|api[_-]?key|authorization|bearer)\b"
    r"([\"']?\s*[:=]\s*|\s+)(?:(?:bearer|basic|token)\s+)?[^\s,;\"'&]+"
)


def classify_sync_error(exc: BaseException) -> dict[str, Any]:
    """``{"code": ..., "detail": ...}`` for a failed run; never raises."""
    try:
        chain = list(_cause_chain(exc))
        code = (
            _by_name(chain)
            or _by_status(chain)
            or _by_message(chain)
            or SyncErrorCode.UNKNOWN
        )
        return {"code": code.value, "detail": _scrubbed_detail(exc)}
    except Exception:
        return {"code": SyncErrorCode.UNKNOWN.value, "detail": None}


def _cause_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _CHAIN_LIMIT:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _by_name(chain: list[BaseException]) -> SyncErrorCode | None:
    for exc in chain:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return SyncErrorCode.NETWORK
        if isinstance(exc, PermissionError):
            return SyncErrorCode.PERMISSION
        runs = _word_runs(type(exc).__mro__[:-2])
        for needles, code in _NAME_RULES:
            if not runs.isdisjoint(needles):
                return code
    return None


def _word_runs(classes: tuple[type, ...]) -> set[str]:
    """Every run of consecutive CamelCase words in the class names, lowercased and joined."""
    runs: set[str] = set()
    for cls in classes:
        words = [w.lower() for w in _CAMEL_WORD.findall(cls.__name__)]
        for i in range(len(words)):
            for j in range(i + 1, min(i + 4, len(words)) + 1):
                runs.add("".join(words[i:j]))
    return runs


def _status_of(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None), getattr(exc, "resp", None)):
        if holder is None:
            continue
        for attr in ("status_code", "status", "response_status_code", "code"):
            value = getattr(holder, attr, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
    # botocore: ClientError.response["Error"]["Code"] is a name, and the HTTP status sits beside it.
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if isinstance(status, int):
            return status
    return None


def _by_status(chain: list[BaseException]) -> SyncErrorCode | None:
    for exc in chain:
        status = _status_of(exc)
        if status is None:
            continue
        if status == 401:
            return SyncErrorCode.AUTH
        if status == 403:
            return SyncErrorCode.PERMISSION
        if status == 404:
            return SyncErrorCode.NOT_FOUND
        if status == 429:
            return SyncErrorCode.RATE_LIMIT
        if status >= 500:
            return SyncErrorCode.SOURCE_UNAVAILABLE
    return None


def _by_message(chain: list[BaseException]) -> SyncErrorCode | None:
    for exc in chain:
        text = str(exc)
        for pattern, code in _MESSAGE_RULES:
            if pattern.search(text):
                return code
    return None


def _scrubbed_detail(exc: BaseException) -> str | None:
    text = str(exc).strip() or type(exc).__name__
    text = _QUERY.sub(r"\1?…", text)
    text = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}=…", text)
    text = _TOKENISH.sub("…", text)
    text = " ".join(text.split())
    if len(text) > _DETAIL_LIMIT:
        text = text[: _DETAIL_LIMIT - 1] + "…"
    return text or None
