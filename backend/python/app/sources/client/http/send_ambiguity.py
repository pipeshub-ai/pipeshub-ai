"""Which failed requests may still have been processed upstream ("maybe sent"), and the send-safe retry rule.

The connector clients report such a send to the tool call they run in
(``harness_core.effects.ambiguity``), so an engine or a durable run never
repeats a write whose first attempt may have landed. Classification is pure and
conservative: a failure before the request left (no connection), or a refusal
the server makes before processing anything, is ``not_sent``; a failure after it
may have reached the server, for a request that changes something, is
``maybe_sent``; anything else, including every failure of a safe method, is
``definite``.

The rule every write client applies to a failed attempt (``settle_failed_send``):
a write is sent again only when it was ``not_sent``; a ``maybe_sent`` one is
reported instead, so the kernel reconciles it or puts it to a person.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from typing import TYPE_CHECKING, Literal
from urllib.error import HTTPError, URLError

import aiohttp  # type: ignore
import httpx  # type: ignore
from urllib3 import exceptions as urllib3_exceptions
from urllib3.util.ssl_match_hostname import CertificateError

from app.sources.client.utils.sql_statement import sql_statement_writes
from harness_core.effects.ambiguity import note_ambiguous_send

if TYPE_CHECKING:
    from types import ModuleType

SendOutcome = Literal["not_sent", "maybe_sent", "definite"]

# WebDAV's PROPFIND and REPORT (RFC 4918, 3253) and SEARCH (RFC 5323) are safe too: Nextcloud sync reads with them.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PROPFIND", "REPORT", "SEARCH"})

# Every 5xx: a gateway (502, 504) or a server (500, 503) can answer so after the write was applied, so for a write
# none of them is a definite error (decided in SAFE-RETRY; QA-24 had only 502 and 504).
def is_server_error(status: int) -> bool:
    return status // 100 == 5

# A throttle: every provider a write tool calls documents it as refused before processing.
REFUSED_STATUSES = frozenset({429})


class PossiblySentError(Exception):
    """A write failed after it may have reached the server. Raised in place of the transport error where a
    library's own retry loop would otherwise send the write again (its type is outside every such loop's set)."""

_HTTPX_NOT_SENT: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    httpx.ProxyError,
)
_HTTPX_MAYBE_SENT: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)

_AIOHTTP_NOT_SENT: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectorError,
    aiohttp.InvalidURL,
)
_AIOHTTP_MAYBE_SENT: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    TimeoutError,
)


def is_unsafe(method: str) -> bool:
    return method.upper() not in SAFE_METHODS


def classify_httpx_failure(method: str, error: BaseException) -> SendOutcome:
    if isinstance(error, _HTTPX_NOT_SENT):
        return "not_sent"
    if isinstance(error, _HTTPX_MAYBE_SENT):
        return "maybe_sent" if is_unsafe(method) else "definite"
    return "definite"


def classify_status(method: str, status: int) -> SendOutcome:
    if status in REFUSED_STATUSES:
        return "not_sent"
    return "maybe_sent" if is_server_error(status) and is_unsafe(method) else "definite"


def resend_permitted(outcome: SendOutcome, *, unsafe: bool) -> bool:
    """Whether a client may send a failed request again: a write only when it provably was not processed; a read
    or an idempotent request as its library decides."""
    return outcome == "not_sent" or not unsafe


def settle_failed_send(outcome: SendOutcome, reason: str, *, unsafe: bool) -> bool:
    """Reports a failed attempt that may have been processed to the running tool call; returns
    ``resend_permitted``."""
    if outcome == "maybe_sent":
        note_ambiguous_send(reason)
    return resend_permitted(outcome, unsafe=unsafe)


def classify_aiohttp_failure(error: BaseException, *, unsafe: bool) -> SendOutcome:
    """``unsafe``: the request changes something (a GraphQL mutation is a ``POST`` that does)."""
    if isinstance(error, _AIOHTTP_NOT_SENT):
        return "not_sent"
    if isinstance(error, _AIOHTTP_MAYBE_SENT):
        return "maybe_sent" if unsafe else "definite"
    return "definite"


# urllib wraps a failure while it sends in ``URLError``; these causes mean nothing left the host.
_URLLIB_NOT_SENT: tuple[type[BaseException], ...] = (ConnectionRefusedError, socket.gaierror, ssl.SSLError)


def classify_urllib_failure(error: BaseException, *, unsafe: bool) -> SendOutcome:
    """urllib, as the Slack SDK's client uses it. An HTTP status is ``classify_status``'s. A ``URLError`` is a
    failure while sending: ``not_sent`` only when its cause shows the request never left (a reason given as text is
    urllib refusing the URL itself). Anything else after the send began (a reset, a timeout, a server that closed
    without answering) may have been processed."""
    if isinstance(error, HTTPError):
        return "definite"
    if isinstance(error, URLError):
        if isinstance(error.reason, (str, *_URLLIB_NOT_SENT)):
            return "not_sent"
        return "maybe_sent" if unsafe else "definite"
    if isinstance(error, (OSError, http.client.HTTPException)):
        return "maybe_sent" if unsafe else "definite"
    return "definite"


def classify_urllib3_failure(error: BaseException, *, unsafe: bool) -> SendOutcome:
    """An error urllib3 hands its ``Retry``. Only a failed connect (refused, DNS, a connect timeout) or a
    certificate the handshake rejected is known to precede the send; urllib3 reports every other TLS error the
    same way whether it came before or after, so those count as possibly sent."""
    cause = error.original_error if isinstance(error, urllib3_exceptions.ProxyError) else error
    if isinstance(cause, urllib3_exceptions.ConnectTimeoutError):
        return "not_sent"
    if isinstance(cause, urllib3_exceptions.SSLError) and isinstance(
        cause.args[0] if cause.args else None, (ssl.SSLCertVerificationError, CertificateError)
    ):
        return "not_sent"
    return "maybe_sent" if unsafe else "definite"


# PEP 249 names every driver module exports for an error the server reported about the statement itself.
_DBAPI_STATEMENT_ERRORS = ("DataError", "IntegrityError", "ProgrammingError", "NotSupportedError")
_DBAPI_CONNECTION_ERRORS = ("InterfaceError", "OperationalError", "InternalError")


def _driver_classes(driver: ModuleType, names: tuple[str, ...]) -> tuple[type[BaseException], ...]:
    # A driver stand-in (a test's module double) may lack a class or hold a non-type; such a name places nothing.
    found = (getattr(driver, name, None) for name in names)
    return tuple(cls for cls in found if isinstance(cls, type) and issubclass(cls, BaseException))


def classify_dbapi_failure(error: BaseException, *, driver: ModuleType, writes: bool) -> SendOutcome:
    """A PEP 249 driver's failure after the statement was handed to the connection (a failure to connect or to
    take a pooled connection is not one). The server's answer about the statement is ``definite``; a lost
    connection, a timeout, or an error the driver does not place is ``maybe_sent`` for a statement that writes."""
    statement_errors = _driver_classes(driver, _DBAPI_STATEMENT_ERRORS)
    if isinstance(error, statement_errors):
        return "definite"
    connection_errors = _driver_classes(driver, _DBAPI_CONNECTION_ERRORS)
    unplaced = type(error) in _driver_classes(driver, ("Error", "DatabaseError"))
    if unplaced or isinstance(error, (*connection_errors, OSError, TimeoutError)):
        return "maybe_sent" if writes else "definite"
    return "definite"


def note_httpx_failure(method: str, error: BaseException) -> None:
    if classify_httpx_failure(method, error) == "maybe_sent":
        note_ambiguous_send(type(error).__name__)


def note_status(method: str, status: int) -> None:
    if classify_status(method, status) == "maybe_sent":
        note_ambiguous_send(f"http_{status}")


def note_dbapi_failure(error: BaseException, *, driver: ModuleType, statement: str) -> None:
    writes = sql_statement_writes(statement)
    if classify_dbapi_failure(error, driver=driver, writes=writes) == "maybe_sent":
        note_ambiguous_send(type(error).__name__)


def note_aiohttp_failure(error: BaseException, *, unsafe: bool) -> None:
    if classify_aiohttp_failure(error, unsafe=unsafe) == "maybe_sent":
        note_ambiguous_send(type(error).__name__)


__all__ = [
    "REFUSED_STATUSES",
    "SAFE_METHODS",
    "PossiblySentError",
    "SendOutcome",
    "classify_aiohttp_failure",
    "classify_dbapi_failure",
    "classify_httpx_failure",
    "classify_status",
    "classify_urllib3_failure",
    "classify_urllib_failure",
    "is_server_error",
    "is_unsafe",
    "note_aiohttp_failure",
    "note_dbapi_failure",
    "note_httpx_failure",
    "note_status",
    "resend_permitted",
    "settle_failed_send",
]
