"""One ``http.request.end`` per request (app/api/middlewares/request_log.py; docs/logging.md, "HTTP requests (Python)").

The app is shaped like query_main and connectors_main: a function auth middleware that binds the OrgContext the way
``_bind_org_context`` does and hands a refusal over, ``install_request_logging``, and MetricsMiddleware outermost.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middlewares import request_log
from app.api.middlewares.auth import AuthRejected
from app.api.middlewares.request_context import RequestContextMiddleware
from app.api.middlewares.request_log import (
    RequestLogMiddleware,
    install_request_logging,
    mark_request_failure,
)
from app.core.org_context import OrgContext, set_request_context
from app.telemetry.middleware import MetricsMiddleware
from app.utils import error_class
from app.utils.logger import RedactingFormatter, log_record_fields
from app.utils.request_context import get_context, set_context_org
from tests.unit.api.test_framework_behaviour import (  # noqa: F401 - autouse fixture
    Recorder,
    _drive,
    http_requests_metric,
)

ORG = "65f0a1b2c3d4e5f6a7b8c9d0"
USER = "65f0a1b2c3d4e5f6a7b8c9d1"
EVENT = "http.request.end"
LOGGER = "app.api.middlewares.request_log"

_BEARER = {"WWW-Authenticate": "Bearer"}
REFUSALS: dict[str, Callable[[], HTTPException]] = {
    "Bearer expired": lambda: AuthRejected(401, "Could not validate credentials", reason="expired", headers=_BEARER),
    "Bearer other-org": lambda: AuthRejected(403, "org_id does not match the service token", reason="org_mismatch"),
}
SHARED = RuntimeError("one failure behind several requests")
# Built at run time: a traceback prints source lines, so a literal there would show in the stack.
ECHOED = "-".join(("SENTINEL", "ARGS"))


class _Clock:
    """perf_counter for the middleware: routes move it, so durations are exact."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(request_log, "perf_counter", fake)
    return fake


@pytest.fixture(autouse=True)
def capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return caplog


def _build_app(clock: _Clock, *, before_install: Callable[[FastAPI], None] | None = None,
               with_logging: bool = True) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        refusal = REFUSALS.get(request.headers.get("authorization", ""))
        if refusal is not None:
            exc = refusal()
            mark_request_failure(request, exc)
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
        rc = get_context()
        set_request_context(OrgContext(org_id=ORG, user_id=USER, kind="user", source="jwt",
                                       request_id=rc.root_id if rc else "-"))
        set_context_org(ORG)
        return await call_next(request)

    if before_install is not None:
        before_install(app)
    if with_logging:
        install_request_logging(app)
    else:
        app.add_middleware(RequestContextMiddleware)
    app.add_middleware(MetricsMiddleware, service_name="test_service")

    @app.get("/api/v1/kb/{kb_id}")
    async def kb(kb_id: str) -> dict:
        clock.now += 0.1
        return {"kb_id": kb_id}

    @app.post("/api/v1/kb/{kb_id}")
    async def kb_post(kb_id: str) -> dict:
        return {"kb_id": kb_id}

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: int) -> dict:
        return {"item_id": item_id}

    @app.get("/api/v1/background")
    async def background() -> JSONResponse:
        def later() -> None:
            clock.now += 5

        clock.now += 0.1
        return JSONResponse({"ok": True}, background=BackgroundTask(later))

    async def raise_(exc: BaseException) -> None:
        raise exc

    @app.get("/api/v1/boom")
    async def boom() -> None:
        await raise_(RuntimeError("route exploded"))

    @app.get("/api/v1/leak/connection")
    async def leak_connection() -> None:
        await raise_(ConnectionError("upstream down"))

    @app.get("/api/v1/leak/timeout")
    async def leak_timeout() -> None:
        await raise_(TimeoutError("upstream slow"))

    @app.get("/api/v1/leak/httpx404")
    async def leak_httpx404() -> None:
        response = httpx.Response(404, request=httpx.Request("GET", "http://upstream.invalid/x"))
        response.raise_for_status()

    @app.get("/api/v1/shared")
    async def shared() -> None:
        raise SHARED

    @app.get("/api/v1/http/{code}")
    async def http_plain(code: int) -> None:
        raise HTTPException(status_code=code, detail="plain")

    @app.get("/api/v1/http503-from-connection")
    async def http503_from_connection() -> None:
        try:
            raise ConnectionError("vector db down")
        except ConnectionError as e:
            raise HTTPException(status_code=503, detail="vector db unavailable") from e

    @app.get("/api/v1/http500-from-keyerror")
    async def http500_from_keyerror() -> None:
        try:
            {}["missing"]
        except KeyError as e:
            raise HTTPException(status_code=500, detail="Failed to save credentials.") from e

    @app.get("/api/v1/logged-then-raised")
    async def logged_then_raised() -> None:
        try:
            {}["missing"]
        except KeyError as e:
            error_class.log_failure(logging.getLogger(LOGGER + ".route"), "kb.save", e)
            raise HTTPException(status_code=500, detail="Failed to save.") from e

    @app.get("/api/v1/returns500")
    async def returns500() -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": "chosen"})

    async def insufficient_scope() -> None:
        raise AuthRejected(403, "Insufficient scope. Required: kb:read", reason="insufficient_scope")

    @app.get("/api/v1/scoped", dependencies=[Depends(insufficient_scope)])
    async def scoped() -> dict:
        return {"ok": True}

    @app.get("/api/v1/stream-fail")
    async def stream_fail() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            raise RuntimeError("mid-stream")

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.get("/api/v1/stalled")
    async def stalled() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            await asyncio.Event().wait()

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.get("/api/v1/marked/conflict")
    async def marked_conflict(request: Request) -> None:
        exc = HTTPException(status_code=409, detail="exists")
        mark_request_failure(request, exc, error_class="bad_request", reason="duplicate_name")
        raise exc

    @app.get("/api/v1/marked/quiet-502")
    async def marked_quiet_502(request: Request) -> None:
        try:
            raise ValueError(f"upstream echoed {ECHOED}")
        except ValueError as e:
            exc = HTTPException(status_code=502, detail="upstream failed")
            mark_request_failure(request, exc, include_error_text=False)
            raise exc from e

    @app.get("/api/v1/marked/quiet-crash")
    async def marked_quiet_crash(request: Request) -> None:
        exc = RuntimeError(f"tool said {ECHOED}")
        mark_request_failure(request, exc, include_error_text=False)
        raise exc

    @app.get("/api/v1/answered-ok/{marked}")
    async def answered_ok(request: Request, marked: bool) -> dict:
        try:
            raise ConnectionError("vector db down")
        except ConnectionError as e:
            if marked:
                mark_request_failure(request, e, reason="degraded")
            return {"results": []}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    @app.get("/health/deps")
    async def health_deps() -> JSONResponse:
        return JSONResponse(status_code=503, content={"status": "unhealthy"})

    return app


async def _get(app: FastAPI, path: str, **kwargs: object) -> httpx.Response:
    """One request in its own task, as uvicorn runs each: the OrgContext auth binds does not leak to the next."""
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("10.1.2.3", 51000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, **kwargs)

    return await asyncio.create_task(call())


def _ends(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == EVENT]


def _one_end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    (record,) = _ends(caplog)
    return record, log_record_fields(record)


def _uvicorn_record(exc: BaseException) -> logging.LogRecord:
    """What uvicorn's run_asgi writes for an exception that left the app."""
    return logging.getLogger("uvicorn.error").makeRecord(
        "uvicorn.error", logging.ERROR, "h11_impl.py", 1, "Exception in ASGI application\n", (),
        (type(exc), exc, exc.__traceback__),
    )


def _uvicorn_keeps(exc: BaseException) -> bool:
    return bool(logging.getLogger("uvicorn.error").filter(_uvicorn_record(exc)))


# --- the line on success -------------------------------------------------------------------------------------------


async def test_a_request_writes_one_line_with_who_what_and_duration(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/kb/65f0aaaabbbbccccddddeeee", headers={"x-request-id": "rid-200"})

    assert response.status_code == 200
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert fields["method"] == "GET"
    assert fields["route"] == "/api/v1/kb/{kb_id}"
    assert fields["status"] == 200 and isinstance(fields["status"], int)
    assert fields["duration_ms"] == 100 and isinstance(fields["duration_ms"], int)
    assert fields["outcome"] == "success"
    assert fields["client_ip"] == "10.1.2.3"
    assert fields["org_id"] == ORG and fields["user_id"] == USER
    assert str(record.request_id).startswith("rid-200")
    assert "error_class" not in fields


async def test_positive_control_without_install_there_is_no_line(clock: _Clock, capture) -> None:
    app = _build_app(clock, with_logging=False)

    await _get(app, "/api/v1/kb/1")

    assert _ends(capture) == []


async def test_duration_ends_at_the_last_body_message_not_after_the_background_task(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/background")

    _, fields = _one_end(capture)
    assert fields["duration_ms"] == 100


async def test_no_raw_path_query_or_email_is_logged(clock: _Clock, capture) -> None:
    app = _build_app(clock)
    path = "/api/v1/kb/alice@example.com?email=alice@example.com&token=abc123"

    await _get(app, path)

    record, fields = _one_end(capture)
    text = RedactingFormatter("%(message)s").format(record) + json.dumps(fields, default=str)
    assert "alice" not in text and "email=" not in text and "abc123" not in text and "?" not in text
    assert fields["route"] == "/api/v1/kb/{kb_id}"


async def test_health_probes_are_debug_and_a_failed_probe_is_a_warning(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/health")
    await _get(app, "/health/deps")

    ok, failed = _ends(capture)
    assert ok.levelno == logging.DEBUG
    assert failed.levelno == logging.WARNING
    assert log_record_fields(failed)["error_class"] == "unavailable"


async def test_client_ip_is_logged_only_when_it_is_an_address(clock: _Clock, capture) -> None:
    """``_drive`` sends ``client=("testclient", …)``: not an address, so it is left out."""
    app = _build_app(clock)

    await _drive(app, "/api/v1/kb/1", Recorder())

    _, fields = _one_end(capture)
    assert fields["outcome"] == "success"
    assert "client_ip" not in fields


# --- 4xx and auth refusals -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "route", "status", "cls"),
    [
        ("/api/v1/no/such/route/65f0", "unmatched", 404, "not_found"),
        ("/api/v1/items/abc", "/api/v1/items/{item_id}", 422, "bad_request"),
        ("/api/v1/http/409", "/api/v1/http/{code}", 409, "conflict"),
    ],
)
async def test_a_4xx_is_info_with_its_class(clock: _Clock, capture, path, route, status, cls) -> None:
    app = _build_app(clock)

    response = await _get(app, path)

    assert response.status_code == status
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["route"], fields["status"], fields["error_class"], fields["outcome"]) == (route, status, cls, "failure")
    assert "reason" not in fields


async def test_a_405_names_the_route_it_partially_matched(clock: _Clock, capture) -> None:
    app = _build_app(clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.delete("/api/v1/kb/1")

    assert response.status_code == 405
    _, fields = _one_end(capture)
    assert fields["route"] == "/api/v1/kb/{kb_id}"


async def test_a_refusal_before_routing_carries_the_reason_and_the_route_it_would_have_matched(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/kb/1", headers={"authorization": "Bearer expired"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert fields["route"] == "/api/v1/kb/{kb_id}"
    assert (fields["error_class"], fields["reason"]) == ("auth_expired", "expired")
    assert "org_id" not in fields
    assert [r for r in capture.records if r.levelno >= logging.WARNING] == []


async def test_an_org_mismatch_refusal_is_a_warning(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/kb/1", headers={"authorization": "Bearer other-org"})

    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert (fields["status"], fields["error_class"], fields["reason"]) == (403, "forbidden", "org_mismatch")


async def test_a_route_dependency_refusal_is_handed_over_and_answered_as_fastapi_would(clock: _Clock, capture) -> None:
    app = _build_app(clock)
    plain = _build_app(clock, with_logging=False)

    response = await _get(app, "/api/v1/scoped")
    expected = await _get(plain, "/api/v1/scoped")

    assert (response.status_code, response.content) == (expected.status_code, expected.content) == (
        403, b'{"detail":"Insufficient scope. Required: kb:read"}')
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["error_class"], fields["reason"]) == ("forbidden", "insufficient_scope")


async def test_reason_is_read_from_http_exceptions_only(clock: _Clock, capture) -> None:
    """``UnicodeDecodeError`` also has ``.reason``; a plain ``HTTPException`` has none."""
    middleware = RequestLogMiddleware(JSONResponse({"x": 1}, status_code=400))
    scope = {"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(_: dict) -> None:
        pass

    mark_request_failure(SimpleNamespace(scope=scope), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"))
    await middleware(scope, receive, send)

    _, fields = _one_end(capture)
    assert fields["error_class"] == "bad_request"
    assert "reason" not in fields


# --- exceptions: answered once, logged once ------------------------------------------------------------------------


async def test_a_crash_is_answered_by_the_middleware_which_returns_so_the_connection_stays_open(clock: _Clock, capture) -> None:
    """Re-raising after a 500 makes uvicorn close a keep-alive socket the client may reuse (review finding 1)."""
    app = _build_app(clock)

    messages = await _drive(app, "/api/v1/boom", Recorder())  # raises if the exception leaves the app

    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert [s["status"] for s in starts] == [500]
    assert b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body") == (
        b'{"detail":"Internal server error"}')
    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert (fields["status"], fields["error_class"], fields["error_type"]) == (500, "internal", "RuntimeError")
    assert fields["org_id"] == ORG and fields["route"] == "/api/v1/boom"
    assert record.exc_text and "route exploded" in record.exc_text
    assert not _uvicorn_keeps(record.exc_info[1])


async def test_positive_control_an_unlogged_exception_still_reaches_uvicorn() -> None:
    assert _uvicorn_keeps(RuntimeError("never logged"))


async def test_a_crash_logs_nothing_else_at_warning_or_above(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert [r.getMessage() for r in capture.records if r.levelno >= logging.WARNING] == [EVENT]


@pytest.mark.parametrize(
    ("path", "error_type"),
    [
        ("/api/v1/leak/connection", "ConnectionError"),
        ("/api/v1/leak/timeout", "TimeoutError"),
        ("/api/v1/leak/httpx404", "HTTPStatusError"),
    ],
)
async def test_an_unhandled_exception_is_internal_with_its_stack_whatever_its_class(clock: _Clock, capture, path, error_type) -> None:
    """Nobody handled it and the client got a 500: our fault (Node's classifyRequestError, review finding 2)."""
    app = _build_app(clock)

    response = await _get(app, path)

    assert response.status_code == 500
    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert (fields["error_class"], fields["error_type"]) == ("internal", error_type)
    assert record.exc_text
    assert not _uvicorn_keeps(record.exc_info[1])


async def test_control_an_http_503_raised_from_a_connection_error_keeps_its_class(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/http503-from-connection")

    assert response.status_code == 503
    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert fields["error_class"] == "unavailable"
    assert not record.exc_text


async def test_an_http_500_raised_from_our_bug_is_error_with_both_stacks(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/http500-from-keyerror")

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to save credentials."}
    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert fields["error_class"] == "internal"
    assert "KeyError" in record.exc_text and "HTTPException" in record.exc_text


@pytest.mark.parametrize(
    ("code", "level", "cls"),
    [
        (500, logging.ERROR, "internal"),
        (502, logging.WARNING, "upstream_5xx"),
        (503, logging.WARNING, "unavailable"),
        (504, logging.WARNING, "timeout"),
    ],
)
async def test_a_plain_http_5xx_is_classified_by_its_status(clock: _Clock, capture, code, level, cls) -> None:
    app = _build_app(clock)

    await _get(app, f"/api/v1/http/{code}")

    record, fields = _one_end(capture)
    assert (record.levelno, fields["error_class"], fields["status"]) == (level, cls, code)


async def test_a_5xx_returned_without_an_exception_is_a_warning(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/returns500")

    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert fields["error_class"] == "internal"
    assert not record.exc_text


async def test_the_level_follows_the_status_the_client_got(clock: _Clock, capture) -> None:
    """An edition handler can answer an HTTPException(500) with a 402 (EE's budget refusal, review finding 4)."""
    async def budget(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 500:
            return JSONResponse(status_code=402, content={"detail": "budget exceeded"})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app = _build_app(clock, before_install=lambda a: a.add_exception_handler(StarletteHTTPException, budget))

    response = await _get(app, "/api/v1/http/500")

    assert response.status_code == 402
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["status"], fields["error_class"]) == (402, "bad_request")
    assert not record.exc_text


async def test_an_exception_already_logged_lower_down_is_not_logged_with_a_second_stack(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/logged-then-raised")

    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert fields["error_class"] == "internal"
    assert not record.exc_text
    assert len([r for r in capture.records if r.exc_text]) == 1


async def test_one_exception_behind_two_requests_gives_two_lines_and_one_stack(clock: _Clock, capture) -> None:
    """The second request's line must not drop to DEBUG (review finding 10)."""
    app = _build_app(clock)
    for attr in ("_pipeshub_logged", "__traceback__"):
        setattr(SHARED, attr, None)

    first = await _get(app, "/api/v1/shared")
    second = await _get(app, "/api/v1/shared")

    assert first.status_code == second.status_code == 500
    one, two = _ends(capture)
    assert (one.levelno, two.levelno) == (logging.ERROR, logging.WARNING)
    assert one.exc_text and not two.exc_text
    assert log_record_fields(two)["error_class"] == "internal"


async def test_a_stream_that_fails_midway_keeps_its_status_and_is_re_raised(clock: _Clock, capture) -> None:
    """The response has started: only uvicorn closing the connection can tell the client (as on main)."""
    app = _build_app(clock)

    with pytest.raises(RuntimeError, match="mid-stream"):
        await _drive(app, "/api/v1/stream-fail", Recorder())

    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert (fields["status"], fields["outcome"], fields["error_class"]) == (200, "failure", "internal")
    assert not _uvicorn_keeps(record.exc_info[1])


async def test_a_client_that_leaves_mid_stream_is_cancelled_not_success(clock: _Clock, capture) -> None:
    """Starlette returns normally when the client goes; the last body message is never sent (review finding 3)."""
    app = _build_app(clock)

    await _drive(app, "/api/v1/stalled", Recorder(), disconnect=True)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        200, "failure", "cancelled", "client_disconnect")
    assert not record.exc_text


def _raw_scope() -> dict:
    return {"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}


async def _disconnected() -> dict:
    return {"type": "http.disconnect"}


async def _discard(_: dict) -> None:
    pass


async def test_a_response_that_never_sends_its_last_body_message_is_cancelled(capture) -> None:
    async def cut_short(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    await RequestLogMiddleware(cut_short)(_raw_scope(), _disconnected, _discard)

    _, fields = _one_end(capture)
    assert (fields["error_class"], fields["reason"]) == ("cancelled", "client_disconnect")


async def test_control_a_disconnect_read_after_the_response_finished_is_success(capture) -> None:
    async def complete_then_listen(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        await receive()

    await RequestLogMiddleware(complete_then_listen)(_raw_scope(), _disconnected, _discard)

    _, fields = _one_end(capture)
    assert fields["outcome"] == "success" and "reason" not in fields


async def test_cancellation_is_logged_as_cancelled_and_re_raised(capture) -> None:
    async def cancelled_app(scope, receive, send) -> None:
        raise asyncio.CancelledError

    middleware = RequestLogMiddleware(cancelled_app)
    scope = {"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}

    with pytest.raises(asyncio.CancelledError):
        await middleware(scope, None, None)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert fields["error_class"] == "cancelled"


async def test_other_scopes_pass_through_without_a_line(capture) -> None:
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["type"])

    await RequestLogMiddleware(inner)({"type": "lifespan"}, None, None)

    assert seen == ["lifespan"]
    assert _ends(capture) == []


async def test_a_logging_failure_never_breaks_the_request(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("handler down")

    monkeypatch.setattr(request_log, "log_event", broken)
    app = _build_app(clock)

    response = await _get(app, "/api/v1/kb/1")

    assert response.status_code == 200
    assert response.json() == {"kb_id": "1"}


# --- installation --------------------------------------------------------------------------------------------------


def test_install_puts_the_line_inside_the_request_context_and_keeps_an_existing_handler() -> None:
    async def edition_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"edition": True})

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, edition_handler)
    install_request_logging(app)
    install_request_logging(FastAPI())

    classes = [m.cls for m in app.user_middleware]
    assert classes.index(RequestContextMiddleware) + 1 == classes.index(RequestLogMiddleware)
    assert Exception not in app.exception_handlers
    assert sum(isinstance(f, request_log._AlreadyLogged) for f in logging.getLogger("uvicorn.error").filters) == 1


async def test_install_delegates_http_exceptions_to_the_handler_registered_before(clock: _Clock) -> None:
    async def edition_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"edition": True})

    app = _build_app(clock, before_install=lambda a: a.add_exception_handler(StarletteHTTPException, edition_handler))

    response = await _get(app, "/api/v1/http/409")

    assert response.json() == {"edition": True}


# --- hints from the code that handed the failure over ---------------------------------------------------------------


async def test_hints_win_and_the_http_exception_handler_keeps_them(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/marked/conflict")

    assert response.status_code == 409
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["error_class"], fields["reason"]) == ("bad_request", "duplicate_name")


@pytest.mark.parametrize(("path", "status", "cls"), [
    ("/api/v1/marked/quiet-502", 502, "internal"),  # raised from our ValueError: ours (P1b)
    ("/api/v1/marked/quiet-crash", 500, "internal"),
])
async def test_include_error_text_false_keeps_the_text_off_the_line(clock: _Clock, capture, path, status, cls) -> None:
    app = _build_app(clock)

    response = await _get(app, path)

    assert response.status_code == status
    record, fields = _one_end(capture)
    assert fields["error_class"] == cls
    assert "error_message" not in fields
    assert "SENTINEL" not in RedactingFormatter("%(message)s").format(record)


async def test_a_marked_failure_answered_with_a_2xx_is_logged_as_a_failure(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    response = await _get(app, "/api/v1/answered-ok/true")

    assert response.status_code == 200
    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["error_type"], fields["reason"]) == (
        200, "failure", "unavailable", "ConnectionError", "degraded")


async def test_control_the_same_2xx_unmarked_is_success(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/answered-ok/false")

    record, fields = _one_end(capture)
    assert (record.levelno, fields["outcome"]) == (logging.INFO, "success")
