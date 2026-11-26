"""The Google clients' transport, minus re-sending a write that may already have landed (QA24-O1).

Two layers re-sent a write before: ``httplib2.Http._conn_request`` sends any request again after a
``BadStatusLine`` on a kept-alive connection or an ``HTTPException`` while sending, and
``HttpRequest.execute(num_retries=n)`` sends it again after a connection error, a timeout or a 5xx. So a Gmail
send whose answer was lost could go out twice, and no Google failure was reported as possibly sent. Here a write
goes out once on a fresh connection: a failure to connect left nothing at Google and stays retryable; a failure
after the send is reported and raised as ``PossiblySentError``, which ``execute``'s retry loop does not catch; a
5xx answer is raised as the ``HttpError`` ``execute`` would raise, before its loop can repeat it. Reads keep both
layers' retries.
"""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING, Any, cast, override

import httplib2  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore

from app.services.rate_budget import note_throttled_threadsafe
from app.sources.client.http.send_ambiguity import (
    PossiblySentError,
    classify_status,
    is_server_error,
    is_unsafe,
    settle_failed_send,
)
from app.sources.client.resilience import parse_retry_after

if TYPE_CHECKING:
    from http.client import HTTPConnection

# The reasons Google gives a project's quota refusal under 403, in the v1 ``errors[].reason`` form and the newer
# ``details[].reason`` one. ``userRateLimitExceeded`` is left to googleapiclient's own retry: one user's quota (a
# Gmail mailbox's is about 5 reads a second) is not the org's, and a team sync reads many users at once. Any other
# 403 is a permission answer.
_RATE_LIMIT_REASONS = frozenset({"ratelimitexceeded", "rate_limit_exceeded"})


def _reasons(content: bytes) -> set[str]:
    try:
        error = json.loads(content).get("error")
    except (ValueError, TypeError, AttributeError):
        return set()
    if not isinstance(error, dict):
        return set()
    entries = [*(error.get("errors") or []), *(error.get("details") or [])]
    return {str(entry.get("reason", "")).lower() for entry in entries if isinstance(entry, dict)}


def google_throttled(status: int, content: bytes) -> bool:
    """Google's "slow down": a 429, or a 403 whose reason is a rate limit."""
    if status == 429:
        return True
    return status == 403 and bool(_reasons(content) & _RATE_LIMIT_REASONS)


def _retry_after(response: httplib2.Response) -> float | None:
    return parse_retry_after({"Retry-After": str(response.get("retry-after", ""))})


class SendSafeHttp(httplib2.Http):
    @override
    def request(  # type: ignore[override]
        self,
        uri: str,
        method: str = "GET",
        body: object = None,
        headers: dict[str, str] | None = None,
        redirections: int = httplib2.DEFAULT_MAX_REDIRECTS,
        connection_type: object = None,
    ) -> tuple[httplib2.Response, bytes]:
        response, content = cast(
            "tuple[httplib2.Response, bytes]",
            super().request(uri, method, body, headers, redirections, connection_type),  # pyright: ignore[reportUnknownMemberType]
        )
        status = response.status
        if google_throttled(status, content):
            # Every attempt passes here, googleapiclient's own retries included, on the worker thread the
            # call runs on; the account it spends (a sync's, an agent tool call's) backs off as a whole (G-31).
            note_throttled_threadsafe(_retry_after(response))
        if is_unsafe(method) and is_server_error(status):
            _ = settle_failed_send(classify_status(method, status), f"http_{status}", unsafe=True)
            raise HttpError(response, content, uri=uri)
        return response, content

    @override
    def _conn_request(  # type: ignore[override]
        self, conn: HTTPConnection, request_uri: str, method: str, body: object, headers: dict[str, str]
    ) -> tuple[httplib2.Response, bytes]:
        if not is_unsafe(method):
            return cast(
                "tuple[httplib2.Response, bytes]",
                super()._conn_request(conn, request_uri, method, body, headers),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
            )
        # A pooled connection the server closed while idle would fail the write after it was handed over, which
        # cannot be told from a send that landed; a fresh connection fails, if at all, before anything is sent.
        conn.close()
        try:
            conn.connect()
        except socket.gaierror as exc:
            raise httplib2.ServerNotFoundError(f"Unable to find the server at {conn.host}") from exc
        try:
            conn.request(method, request_uri, cast("bytes | None", body), headers)
            raw = conn.getresponse()
            content = raw.read()
        except Exception as exc:
            conn.close()
            _ = settle_failed_send("maybe_sent", type(exc).__name__, unsafe=True)
            raise PossiblySentError(f"{method} may have reached Google: {type(exc).__name__}") from exc
        response = httplib2.Response(raw)
        # httplib2's own decoding (gzip, deflate, its size limits), as its _conn_request applies it.
        decoded = cast("bytes", httplib2._decompressContent(response, content, self.limit_kwargs))  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage, reportUnknownMemberType]
        return response, decoded


def install_send_safe_http(client: object, *, timeout: float) -> object:
    """Puts ``SendSafeHttp`` under a ``googleapiclient`` service built with credentials (``AuthorizedHttp``) or
    with a bare ``Http``, keeping the redirect codes ``build`` chose."""
    service = cast("Any", client)
    authorized = cast("Any", getattr(service, "_http", None))
    if authorized is None:
        return client
    current = authorized.http if hasattr(authorized, "http") else authorized
    safe = SendSafeHttp(timeout=timeout)
    safe.redirect_codes = getattr(current, "redirect_codes", safe.redirect_codes)
    if current is authorized:
        service._http = safe
    else:
        authorized.http = safe
    return client


__all__ = ["SendSafeHttp", "google_throttled", "install_send_safe_http"]
