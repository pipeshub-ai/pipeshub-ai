"""One ``http.request.end`` line per HTTP request (contract: docs/logging.md, "HTTP requests (Python)")."""

import asyncio
import contextlib
import inspect
import ipaddress
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middlewares.request_context import RequestContextMiddleware
from app.utils import error_class
from app.utils.logger import create_logger
from app.utils.logging_fields import log_event
from app.utils.route_template import route_template

EVENT = "http.request.end"
# In the ASGI scope: shared by every layer, unlike a ContextVar set in a BaseHTTPMiddleware's call_next task.
_FAILURE_KEY = "pipeshub.request_failure"
# Never routine: a service token naming another org is our bug or a stolen token.
_REASON_LEVEL = {"org_mismatch": logging.WARNING}
_CRASH_BODY = {"detail": "Internal server error"}

logger = create_logger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Message]], Callable[[Message], Awaitable[None]]], Awaitable[None]]


def mark_request_failure(request: Request, exc: BaseException) -> None:
    """Hand a failure that was turned into a response here (an auth refusal, an HTTPException) to the request's line."""
    request.scope[_FAILURE_KEY] = exc


def _client_ip(scope: Scope) -> str | None:
    """The peer as uvicorn resolved it (``FORWARDED_ALLOW_IPS``), only if it is an address."""
    client = scope.get("client")
    host = client[0] if client else None
    try:
        return str(ipaddress.ip_address(host)) if isinstance(host, str) else None
    except ValueError:
        return None


def _status_class(status: int) -> str:
    return "internal" if status == 500 else error_class.classify_error(status)


def _has_chain(exc: BaseException) -> bool:
    return exc.__cause__ is not None or (exc.__context__ is not None and not exc.__suppress_context__)


def _handed_over_class(exc: BaseException, status: int) -> str:
    """A bare HTTPException is its status; one raised from a failure is that failure's class (P1b)."""
    if isinstance(exc, StarletteHTTPException) and not _has_chain(exc):
        return _status_class(status)
    return error_class.classify_error(exc)


def _reason(exc: BaseException | None) -> str | None:
    # HTTPException only: UnicodeDecodeError, URLError and others also have a ``reason``.
    return getattr(exc, "reason", None) if isinstance(exc, StarletteHTTPException) else None


class _Request:
    """What one request sent and received, and its one line."""

    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.started = perf_counter()
        self.status: int | None = None
        self.finished: float | None = None
        self.client_left = False

    def observe(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            self.finished = perf_counter()
        elif message["type"] == "http.disconnect" and self.finished is None:
            self.client_left = True

    def log(self, exc: BaseException | None = None, *, escaped_class: str | None = None, status: int | None = None) -> None:
        with contextlib.suppress(Exception):  # logging never breaks a request
            self._log(exc, escaped_class, status or self.status)

    def _log(self, exc: BaseException | None, escaped_class: str | None, status: int | None) -> None:
        fields: dict[str, object] = {
            "method": self.scope.get("method"),
            "route": route_template(self.scope),
            "status": status,
            "duration_ms": round(((self.finished or perf_counter()) - self.started) * 1000),
            "client_ip": _client_ip(self.scope),
        }
        reason = _reason(exc)
        if exc is not None and escaped_class is not None:
            _log_exception(exc, escaped_class, reason=reason, **fields)
        elif exc is not None and status is not None and status >= 500:
            _log_exception(exc, _handed_over_class(exc, status), reason=reason, **fields)
        elif self.finished is None or self.client_left:
            # Starlette stops a stream quietly when the client leaves: no exception, and either no last body message or,
            # when a BaseHTTPMiddleware below ends its own stream, a last message after the disconnect.
            log_event(logger, EVENT, outcome="failure", error_class="cancelled", reason="client_disconnect", **fields)
        elif status is not None and status >= 400:
            level = logging.WARNING if status >= 500 else _REASON_LEVEL.get(reason, logging.INFO)
            log_event(logger, EVENT, level=level, outcome="failure", error_class=_status_class(status), reason=reason,
                      **fields)
        else:
            health = str(self.scope.get("path", "")).startswith("/health")
            log_event(logger, EVENT, level=logging.DEBUG if health else logging.INFO, outcome="success", **fields)


def _log_exception(exc: BaseException, cls: str, **fields: object) -> None:
    """With its stack once; a request whose exception was already logged (lower down, or behind another request) still
    gets its own line, WARNING at most."""
    prior = error_class.logged_level(exc)
    if prior is None or prior < error_class.level_for(cls):
        error_class.log_failure(logger, EVENT, exc, error_class=cls, **fields)
        return
    log_event(logger, EVENT, level=min(error_class.level_for(cls), logging.WARNING), outcome="failure",
              error_class=cls, error_type=type(exc).__name__, **fields)


class RequestLogMiddleware:
    """Raw ASGI, just inside RequestContextMiddleware: it sees the request id, the OrgContext the auth middleware
    bound, ``scope["route"]``, every exception a route lets out, and the end of a stream."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Callable[[], Awaitable[Message]],
                       send: Callable[[Message], Awaitable[None]]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = _Request(scope)

        async def observed_receive() -> Message:
            message = await receive()
            request.observe(message)
            return message

        async def observed_send(message: Message) -> None:
            request.observe(message)
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError as exc:
            request.log(exc, escaped_class="cancelled")
            raise
        except Exception as exc:
            if request.status is not None:
                request.log(exc, escaped_class="internal")
                raise  # the response has started: only uvicorn closing the connection tells the client
            request.log(exc, escaped_class="internal", status=500)
            # Answered here, not re-raised: ServerErrorMiddleware would re-raise, and uvicorn would then close a
            # keep-alive connection the client may reuse.
            await JSONResponse(status_code=500, content=_CRASH_BODY)(scope, receive, observed_send)
            return
        request.log(scope.get(_FAILURE_KEY))


class _AlreadyLogged(logging.Filter):
    """On ``uvicorn.error``: drops "Exception in ASGI application" for an exception ``http.request.end`` logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return exc is None or error_class.logged_level(exc) is None


_ALREADY_LOGGED = _AlreadyLogged()  # one instance: addFilter is then idempotent


def install_request_logging(app: FastAPI) -> None:
    """RequestLogMiddleware inside RequestContextMiddleware, the HTTPException hand-over and the uvicorn filter.
    Call it where the service used to add RequestContextMiddleware."""
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
    previous = app.exception_handlers.get(StarletteHTTPException, http_exception_handler)

    async def hand_over(request: Request, exc: StarletteHTTPException) -> Response:
        mark_request_failure(request, exc)
        response = previous(request, exc)
        return await response if inspect.isawaitable(response) else response

    app.add_exception_handler(StarletteHTTPException, hand_over)
    logging.getLogger("uvicorn.error").addFilter(_ALREADY_LOGGED)
