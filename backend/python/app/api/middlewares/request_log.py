"""One ``http.request.end`` line per HTTP request (contract: docs/logging.md, "HTTP requests (Python)")."""

import asyncio
import contextlib
import inspect
import ipaddress
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from app.api.middlewares.request_context import RequestContextMiddleware
from app.core.org_context import spawn_in_request
from app.utils import error_class
from app.utils.logger import create_logger
from app.utils.logging_fields import bind_log_fields, log_event
from app.utils.route_template import route_template

EVENT = "http.request.end"
# In the ASGI scope: shared by every layer, unlike a ContextVar set in a BaseHTTPMiddleware's call_next task.
_FAILURE_KEY = "pipeshub.request_failure"
_FIELDS_KEY = "pipeshub.request_log_fields"
# Never routine: a service token naming another org is our bug or a stolen token.
_REASON_LEVEL = {"org_mismatch": logging.WARNING}
_CRASH_BODY = {"detail": "Internal server error"}
# When the line saw the client leave: before the answer started, or after it started and before its last body message.
# A disconnect seen after the last body was handed over is the server's "response complete" and not recorded.
_BEFORE_ANSWER = "before_answer"
_ANSWERING = "answering"

logger = create_logger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
ASGIApp = Callable[[Scope, Receive, Callable[[Message], Awaitable[None]]], Awaitable[None]]


@dataclass(frozen=True)
class _Failure:
    exc: BaseException | None
    error_class: str | None = None
    reason: str | None = None
    include_error_text: bool = True


def mark_request_failure(
    request: Request, exc: BaseException | None = None, /, *,
    error_class: str | None = None, reason: str | None = None, include_error_text: bool = True,
) -> None:
    """Hand a failure that was turned into a response here (an auth refusal, an HTTPException, an error answered
    with a 2xx) to the request's line, which then logs it as a failure whatever the status. ``error_class`` and
    ``reason`` win over the ones read from ``exc``; ``include_error_text=False`` keeps its text off the line, as in
    ``log_failure``. The hints also apply to an exception raised ``from`` ``exc``. ``exc`` may be None for a failure
    with no exception (an OAuth callback that answers a redirect); ``error_class`` is then required."""
    if exc is None and error_class is None:
        raise TypeError("mark_request_failure needs an exception or an error_class")
    request.scope[_FAILURE_KEY] = _Failure(exc, error_class, reason, include_error_text)


def add_request_log_fields(request: Request, **fields: object) -> None:
    """Event fields for this request's line only (not bound to the route's own lines): a bounded value the route
    computed, such as ``oauth_error``. Never raw request input. ``None`` values are skipped."""
    request.scope.setdefault(_FIELDS_KEY, {}).update({k: v for k, v in fields.items() if v is not None})


def bind_request_log_fields(request: Request, **ids: object) -> None:
    """Ids for every line of this request: the route's own (``bind_log_fields``, which checks the names) and this
    request's line, through the scope (the route runs in a BaseHTTPMiddleware's child task, whose context the line
    can't see)."""
    bind_log_fields(**ids)
    add_request_log_fields(request, **ids)


def path_log_fields(**field_by_param: str) -> Callable[[Request], Awaitable[None]]:
    """A router dependency binding path parameters as log ids: ``path_log_fields(instance_id="toolset_id")``."""
    async def bind(request: Request) -> None:  # async: a sync dependency runs in a thread, which loses the binding
        bind_request_log_fields(request, **{field: request.path_params.get(param)
                                            for param, field in field_by_param.items()})

    return bind


def _marked(scope: Scope) -> _Failure | None:
    failure = scope.get(_FAILURE_KEY)
    return failure if isinstance(failure, _Failure) else None


def _on_cause_chain(exc: BaseException, target: BaseException) -> bool:
    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        if link is target:
            return True
        seen.add(id(link))
        link = link.__cause__
    return False


def _failure_for(scope: Scope, exc: BaseException, *, escaped: bool = False) -> _Failure:
    """``exc`` with a mark's hints: all of them when the mark is ``exc``, or (handed over, not escaped) on its ``__cause__``
    chain (``raise HTTPException(...) from e``). Else only ``include_error_text``, when the marked text would be on the line.
    An escaped exception's chain can't be trusted: BaseHTTPMiddleware re-raises with its ``__context__`` as ``__cause__``,
    so a bug raised while handling a marked failure would otherwise take that failure's class and lose its stack."""
    marked = _marked(scope)
    if marked is None or marked.exc is None:
        return _Failure(exc)
    if marked.exc is exc or (not escaped and _on_cause_chain(exc, marked.exc)):
        return replace(marked, exc=exc)
    if error_class.on_chain(exc, marked.exc):
        return _Failure(exc, include_error_text=marked.include_error_text)
    return _Failure(exc)


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


def _handed_over_class(exc: BaseException) -> str:
    """A bare HTTPException is its own status's class; one raised from a failure is that failure's class (P1b)."""
    if isinstance(exc, StarletteHTTPException) and not _has_chain(exc):
        return _status_class(exc.status_code)
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
        self.left: str | None = None

    def observe(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            self.finished = perf_counter()
        elif message["type"] == "http.disconnect" and self.finished is None:  # seen once: the read-ahead stops at the first
            self.left = _ANSWERING if self.status is not None else _BEFORE_ANSWER

    def log(self, escaped: BaseException | None = None, *, status: int | None = None) -> None:
        with contextlib.suppress(Exception):  # logging never breaks a request
            self._log(escaped, status or self.status)

    def _log(self, escaped: BaseException | None, status: int | None) -> None:
        fields: dict[str, object] = {
            "method": self.scope.get("method"),
            "route": route_template(self.scope),
            "status": status,
            "duration_ms": round(((self.finished or perf_counter()) - self.started) * 1000),
            "client_ip": _client_ip(self.scope),
            **self.scope.get(_FIELDS_KEY, {}),
        }
        failure = _failure_for(self.scope, escaped, escaped=True) if escaped is not None else _marked(self.scope)
        reason = (failure.reason or _reason(failure.exc)) if failure else None
        outcome = _end_outcome(escaped=escaped is not None, marked=failure is not None, status=status,
                               finished=self.finished is not None, left=self.left)
        if outcome == "exception":
            # Nobody handled it and the client got a 500 (or a cut stream): ours, unless the code that marked it said,
            # or it is the client leaving (Starlette raises ClientDisconnect from a body read).
            if isinstance(escaped, ClientDisconnect):
                default, reason = "cancelled", reason or "client_disconnect"
            else:
                default = "cancelled" if isinstance(escaped, asyncio.CancelledError) else "internal"
            _log_exception(failure, failure.error_class or default, reason=reason, **fields)
        elif outcome == "failure":
            # A 5xx, or a failure the route answered with a 2xx/3xx: logged as the failure it is.
            cls = failure.error_class or _handed_over_class(failure.exc)
            _log_exception(failure, cls, reason=reason, **fields)
        elif outcome == "cancelled":
            log_event(logger, EVENT, outcome="failure", error_class="cancelled", reason="client_disconnect", **fields)
        elif outcome == "status":
            level = logging.WARNING if status >= 500 else _REASON_LEVEL.get(reason, logging.INFO)
            cls = (failure.error_class if failure else None) or _status_class(status)
            log_event(logger, EVENT, level=level, outcome="failure", error_class=cls, reason=reason, **fields)
        else:
            health = str(self.scope.get("path", "")).startswith("/health")
            log_event(logger, EVENT, level=logging.DEBUG if health else logging.INFO, outcome="success", **fields)


def _end_outcome(*, escaped: bool, marked: bool, status: int | None, finished: bool, left: str | None) -> str:
    """Which line a request ends with: the table in docs/logging.md ("When the client leaves"). An escaped exception
    always wins. An answer the client left in the middle of is cancelled, and so is one whose last body message never
    came when the client was not seen leaving. An answer the route started only after the client left keeps a refusal's,
    a 5xx's or a marked failure's own line, and is cancelled otherwise."""
    if escaped:
        return "exception"
    if left == _ANSWERING or (left is None and not finished):
        return "cancelled"
    if marked and (status is None or not 400 <= status < 500):
        return "failure"
    if status is not None and status >= 400:
        return "status"
    return "cancelled" if left == _BEFORE_ANSWER else "success"


def _log_exception(failure: _Failure, cls: str, **fields: object) -> None:
    """With its stack once; a request whose exception was already logged (lower down, or behind another request) still
    gets its own line, WARNING at most."""
    exc = failure.exc
    if exc is None:  # marked without an exception: no type, text or stack to give
        log_event(logger, EVENT, level=error_class.level_for(cls), outcome="failure", error_class=cls, **fields)
        return
    # A type name only, so it stays with include_error_text=False.
    cause_type = error_class.innermost_type(exc) if _has_chain(exc) else None
    if not error_class.already_logged(exc, error_class.level_for(cls)):
        error_class.log_failure(logger, EVENT, exc, error_class=cls, include_error_text=failure.include_error_text,
                                cause_type=cause_type, **fields)
        return
    log_event(logger, EVENT, level=min(error_class.level_for(cls), logging.WARNING), outcome="failure",
              error_class=cls, error_type=type(exc).__name__, cause_type=cause_type, **fields)


class _Inbox:
    """Reads ``receive`` ahead of the app, one message at a time, so a client that leaves is seen even when nothing
    below reads again (a route still working on a plain response: uvicorn's send drops the answer silently)."""

    def __init__(self, receive: Receive, observe: Callable[[Message], None]) -> None:
        self._slot: asyncio.Queue[Message | Exception] = asyncio.Queue(maxsize=1)  # a body is not read ahead of the app
        self._pump_task = spawn_in_request(self._pump(receive, observe))

    async def _pump(self, receive: Receive, observe: Callable[[Message], None]) -> None:
        try:
            while True:
                message = await receive()
                observe(message)  # before the slot: the unread request message of a GET must not hide the disconnect
                await self._slot.put(message)
                if message["type"] == "http.disconnect":
                    return
        except Exception as exc:  # the app's next receive raises it, as if it had called receive itself
            await self._slot.put(exc)

    async def receive(self) -> Message:
        item = await self._slot.get()
        if isinstance(item, Exception) or item["type"] == "http.disconnect":
            self._slot.put_nowait(item)  # the last word: every later receive gets it too, as servers repeat it
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self._pump_task.cancel()


class RequestLogMiddleware:
    """Raw ASGI, just inside RequestContextMiddleware: it sees the request id, the OrgContext the auth middleware
    bound, ``scope["route"]``, every exception a route lets out, and the end of a stream."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Callable[[Message], Awaitable[None]]) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = _Request(scope)
        inbox = _Inbox(receive, request.observe)

        async def observed_send(message: Message) -> None:
            request.observe(message)
            await send(message)

        try:
            await self.app(scope, inbox.receive, observed_send)
        except asyncio.CancelledError as exc:
            request.log(exc)
            raise
        except Exception as exc:
            if request.status is not None:
                request.log(exc)
                raise  # the response has started: only uvicorn closing the connection tells the client
            request.log(exc, status=500)
            # Answered here, not re-raised: ServerErrorMiddleware would re-raise, and uvicorn would then close a
            # keep-alive connection the client may reuse.
            await JSONResponse(status_code=500, content=_CRASH_BODY)(scope, inbox.receive, observed_send)
            return
        finally:
            inbox.close()
        request.log()


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
        request.scope[_FAILURE_KEY] = _failure_for(request.scope, exc)  # keeps the hints of a route's own mark
        response = previous(request, exc)
        return await response if inspect.isawaitable(response) else response

    app.add_exception_handler(StarletteHTTPException, hand_over)
    logging.getLogger("uvicorn.error").addFilter(_ALREADY_LOGGED)
