"""One ``http.request.end`` per request (app/api/middlewares/request_log.py; docs/logging.md, "HTTP requests (Python)").

The app is shaped like query_main and connectors_main: a function auth middleware that binds the OrgContext the way
``_bind_org_context`` does and hands a refusal over, ``install_request_logging``, and MetricsMiddleware outermost.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from app.api.middlewares import request_log
from app.api.middlewares.auth import AuthRejected
from app.api.middlewares.request_context import RequestContextMiddleware
from app.api.middlewares.request_log import (
    RequestLogMiddleware,
    add_request_log_fields,
    bind_request_log_fields,
    install_request_logging,
    mark_request_failure,
    path_log_fields,
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
    @app.get("/api/v1/stalled/{code}")
    async def stalled(code: int = 200) -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            await asyncio.Event().wait()

        return StreamingResponse(body(), status_code=code, media_type="text/event-stream")

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

    @app.get("/api/v1/marked/wrapped/{how}")
    async def marked_wrapped(request: Request, how: str) -> None:
        try:
            raise ValueError(f"upstream echoed {ECHOED}")
        except ValueError as e:
            mark_request_failure(request, e, include_error_text=False, reason="tool_failed")
            if how == "http":
                raise HTTPException(status_code=502, detail="upstream failed") from e
            if how == "implicit-http":
                raise HTTPException(status_code=502, detail="upstream failed")  # noqa: B904 - the implicit chain is the case
            if how == "implicit-crash":
                raise RuntimeError("wrapped")  # noqa: B904 - the implicit chain is the case
            raise RuntimeError("wrapped") from e

    @app.get("/api/v1/marked/bug-while-handling")
    async def bug_while_handling(request: Request) -> dict:
        try:
            raise ConnectionError("vector db down")
        except ConnectionError as e:
            mark_request_failure(request, e, error_class="unavailable", reason="degraded")
            return {"results": {}["missing"]}  # a bug while building the degraded answer

    @app.post("/api/v1/upload-then-bug")
    async def upload_then_bug(request: Request) -> dict:
        with contextlib.suppress(ClientDisconnect):
            await request.json()
        return {"keys": {}["missing"]}

    @app.get("/api/v1/marked/http/{code}/answered-ok")
    async def marked_http_answered_ok(request: Request, code: int) -> dict:
        mark_request_failure(request, HTTPException(status_code=code, detail="no"))
        return {"ok": True}

    @app.get("/api/v1/marked/no-exception/{code}")
    async def marked_no_exception(request: Request, code: int) -> Response:
        mark_request_failure(request, error_class="auth_expired", reason="oauth_denied")
        return Response(status_code=code, headers={"location": "/done"} if code == 302 else None)

    @app.get("/api/v1/marked/class/{where}")
    async def marked_class(request: Request, where: str) -> dict:
        if where == "answered-ok":
            mark_request_failure(request, ConnectionError("db down"), error_class="upstream_5xx")
            return {"ok": True}
        exc = RuntimeError("escapes")
        mark_request_failure(request, exc, error_class="unavailable")
        raise exc

    @app.get("/api/v1/cause/{kind}")
    async def cause(kind: str) -> None:
        class DomainError(Exception):
            pass

        try:
            try:
                if kind == "group":
                    raise ExceptionGroup("g", [ConnectionError(ECHOED)])
                raise httpx.ConnectError(ECHOED)
            except Exception as inner:
                raise DomainError(ECHOED) from inner
        except DomainError as e:
            raise HTTPException(status_code=502, detail="upstream failed") from e

    @app.post("/api/v1/upload")
    async def upload(request: Request) -> dict:
        return {"keys": len(await request.json())}

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


@pytest.mark.parametrize(("path", "status"), [("/api/v1/stalled", 200), ("/api/v1/stalled/503", 503)])
async def test_a_client_that_leaves_mid_stream_is_cancelled_not_success(clock: _Clock, capture, path: str,
                                                                        status: int) -> None:
    """Starlette returns normally when the client goes (review finding 3). The auth BaseHTTPMiddleware then ends its own
    stream with a last body message after the disconnect, so a cut 503 must not read as an answered one (#5681)."""
    app = _build_app(clock)

    await _drive(app, path, Recorder(), disconnect=True)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        status, "failure", "cancelled", "client_disconnect")
    assert not record.exc_text


def _raw_scope() -> dict:
    return {"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""}


async def _disconnected() -> dict:
    return {"type": "http.disconnect"}


async def _discard(_: dict) -> None:
    pass


@pytest.mark.parametrize("status", [200, 503])
async def test_a_response_that_never_sends_its_last_body_message_is_cancelled(capture, status: int) -> None:
    """A cut stream is cancelled whatever status it started with: the client never got the whole answer."""
    async def cut_short(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})

    await RequestLogMiddleware(cut_short)(_raw_scope(), _disconnected, _discard)

    _, fields = _one_end(capture)
    assert (fields["error_class"], fields["reason"]) == ("cancelled", "client_disconnect")


async def test_control_a_disconnect_read_after_the_response_finished_is_success(capture) -> None:
    """A server answers ``http.disconnect`` only once the client left or the response is complete (``_Server``)."""
    async def complete_then_listen(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        await receive()

    server = _Server()
    await asyncio.wait_for(RequestLogMiddleware(complete_then_listen)(_raw_scope(), server.receive, server.send), 10)

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


def _record_text(record: logging.LogRecord) -> str:
    return RedactingFormatter("%(message)s").format(record) + json.dumps(log_record_fields(record), default=str)


@pytest.mark.parametrize(("how", "status", "reason"), [
    ("http", 502, "tool_failed"),
    ("implicit-http", 502, None),  # raised inside except, no `from`: only the text rule follows (review R9)
    ("crash", 500, None),  # unhandled: the mark's class and reason stay with the marked object (review R8)
    ("implicit-crash", 500, None),
])
async def test_the_marked_text_stays_off_the_line_of_an_exception_raised_from_it(clock: _Clock, capture, how, status,
                                                                                    reason) -> None:
    """``mark_request_failure(request, e, include_error_text=False, ...)`` then a raise from ``e`` (review R1, R9)."""
    app = _build_app(clock)

    response = await _get(app, f"/api/v1/marked/wrapped/{how}")

    assert response.status_code == status
    record, fields = _one_end(capture)
    assert (fields["error_class"], fields.get("reason"), fields["cause_type"]) == ("internal", reason, "ValueError")
    assert "error_message" not in fields
    assert ECHOED not in _record_text(record)
    assert all(ECHOED not in _record_text(r) for r in capture.records)


@pytest.mark.parametrize(("code", "level", "cls"), [(403, logging.WARNING, "forbidden"), (503, logging.WARNING, "unavailable")])
async def test_a_marked_bare_http_exception_answered_ok_keeps_its_own_status_class(clock: _Clock, capture, code, level,
                                                                                    cls) -> None:
    app = _build_app(clock)

    response = await _get(app, f"/api/v1/marked/http/{code}/answered-ok")

    assert response.status_code == 200
    record, fields = _one_end(capture)
    assert (record.levelno, fields["status"], fields["error_class"], fields["outcome"]) == (level, 200, cls, "failure")
    assert not record.exc_text


@pytest.mark.parametrize("code", [200, 302])
async def test_a_failure_marked_without_an_exception_is_one_plain_line(clock: _Clock, capture, code) -> None:
    app = _build_app(clock)

    response = await _get(app, f"/api/v1/marked/no-exception/{code}")

    assert response.status_code == code
    record, fields = _one_end(capture)
    assert record.levelno == logging.WARNING
    assert (fields["route"], fields["status"], fields["error_class"], fields["reason"], fields["outcome"]) == (
        "/api/v1/marked/no-exception/{code}", code, "auth_expired", "oauth_denied", "failure")
    assert not {"error_type", "error_message", "cause_type"} & fields.keys()
    assert not record.exc_text


def test_marking_needs_an_exception_or_a_class() -> None:
    with pytest.raises(TypeError):
        mark_request_failure(SimpleNamespace(scope={}), None)
    mark_request_failure(SimpleNamespace(scope={}), None, error_class="auth_expired")  # control: a class is enough


@pytest.mark.parametrize(("where", "status", "level", "cls"), [
    ("answered-ok", 200, logging.WARNING, "upstream_5xx"),
    ("escapes", 500, logging.WARNING, "unavailable"),
])
async def test_the_error_class_hint_wins_on_the_ok_and_escaped_rows(clock: _Clock, capture, where, status, level,
                                                                     cls) -> None:
    app = _build_app(clock)

    response = await _get(app, f"/api/v1/marked/class/{where}")

    assert response.status_code == status
    record, fields = _one_end(capture)
    assert (record.levelno, fields["error_class"]) == (level, cls)


@pytest.mark.parametrize(("kind", "cause_type"), [("chain", "ConnectError"), ("group", "ConnectionError")])
async def test_cause_type_is_the_innermost_cause(clock: _Clock, capture, kind, cause_type) -> None:
    app = _build_app(clock)

    await _get(app, f"/api/v1/cause/{kind}")

    _, fields = _one_end(capture)
    assert (fields["error_type"], fields["cause_type"]) == ("HTTPException", cause_type)


async def test_no_cause_no_cause_type(clock: _Clock, capture) -> None:
    app = _build_app(clock)

    await _get(app, "/api/v1/boom")

    _, fields = _one_end(capture)
    assert "cause_type" not in fields


async def test_a_client_that_leaves_during_the_upload_is_cancelled_not_a_crash(clock: _Clock, capture) -> None:
    """Starlette raises ClientDisconnect from request.json() once it reads http.disconnect (review R4)."""
    app = _build_app(clock)
    parts = [{"type": "http.request", "body": b'{"a": ', "more_body": True}, {"type": "http.disconnect"}]

    async def receive() -> dict:
        return parts.pop(0) if parts else {"type": "http.disconnect"}

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/upload", "raw_path": b"/api/v1/upload", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("10.1.2.3", 50000), "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, _discard), timeout=10)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["error_class"], fields["reason"], fields["error_type"]) == (
        "cancelled", "client_disconnect", "ClientDisconnect")
    assert not record.exc_text


async def test_a_bug_while_handling_a_marked_failure_is_internal_with_its_stack(clock: _Clock, capture) -> None:
    """The auth BaseHTTPMiddleware re-raises the KeyError ``from`` its context, the marked ConnectionError (review R8)."""
    app = _build_app(clock)

    response = await _get(app, "/api/v1/marked/bug-while-handling")

    assert response.status_code == 500
    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert (fields["error_class"], fields["error_type"]) == ("internal", "KeyError")
    assert "reason" not in fields
    assert "KeyError" in record.exc_text


async def test_a_bug_after_the_client_left_is_still_internal_with_its_stack(clock: _Clock, capture) -> None:
    """Only ClientDisconnect itself is the client leaving (review R4)."""
    app = _build_app(clock)
    parts = [{"type": "http.request", "body": b'{"a": ', "more_body": True}, {"type": "http.disconnect"}]

    async def receive() -> dict:
        return parts.pop(0) if parts else {"type": "http.disconnect"}

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/upload-then-bug", "raw_path": b"/api/v1/upload-then-bug", "query_string": b"",
        "root_path": "", "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("10.1.2.3", 50000), "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, _discard), timeout=10)

    record, fields = _one_end(capture)
    assert record.levelno == logging.ERROR
    assert (fields["error_class"], fields["error_type"]) == ("internal", "KeyError")
    assert record.exc_text


# --- ids and fields a route adds -----------------------------------------------------------------------------------

ROUTE_LOGGER = "app.api.routes.test_ids"


def _add_id_routes(app: FastAPI) -> None:
    """A router binding its path parameter as ``toolset_id``, the same route shape without it, and a route adding an
    event field."""
    router = APIRouter(prefix="/api/v1/ids", dependencies=[Depends(path_log_fields(instance_id="toolset_id"))])

    @router.get("/{instance_id}")
    async def with_ids(instance_id: str) -> dict:
        logging.getLogger(ROUTE_LOGGER).info("inside")
        return {}

    app.include_router(router)

    @app.get("/api/v1/no-ids/{instance_id}")
    async def no_ids(instance_id: str) -> dict:
        logging.getLogger(ROUTE_LOGGER).info("inside")
        return {}

    @app.get("/api/v1/oauth-field")
    async def oauth_field(request: Request) -> dict:
        add_request_log_fields(request, oauth_error="access_denied", unused=None)
        logging.getLogger(ROUTE_LOGGER).info("inside")
        return {}


def _inside(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [log_record_fields(r) for r in caplog.records if r.name == ROUTE_LOGGER]


async def test_a_router_binds_its_path_ids_on_the_route_lines_and_the_request_line(clock: _Clock, capture) -> None:
    capture.set_level(logging.DEBUG, logger=ROUTE_LOGGER)
    app = _build_app(clock, before_install=_add_id_routes)

    await _get(app, "/api/v1/ids/ts-1")
    _, end = _one_end(capture)
    (inside,) = _inside(capture)
    # Also guards the dependency being async: a sync one binds in a worker thread, and the route's line loses the id.
    assert end["toolset_id"] == "ts-1" and inside["toolset_id"] == "ts-1"
    assert end["route"] == "/api/v1/ids/{instance_id}"

    capture.clear()
    await _get(app, "/api/v1/no-ids/ts-1")  # control: the same shape, no dependency
    _, end = _one_end(capture)
    (inside,) = _inside(capture)
    assert "toolset_id" not in end and "toolset_id" not in inside


async def test_ids_never_leak_into_the_next_request(clock: _Clock, capture) -> None:
    app = _build_app(clock, before_install=_add_id_routes)

    for path in ("/api/v1/ids/ts-1", "/api/v1/ids/ts-2", "/api/v1/no-ids/ts-3"):
        await _get(app, path)

    assert [log_record_fields(r).get("toolset_id") for r in _ends(capture)] == ["ts-1", "ts-2", None]


async def test_unknown_ids_raise_and_event_fields_go_on_the_request_line_only(clock: _Clock, capture) -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(ValueError, match="not a bindable log field"):
        bind_request_log_fields(request, oauth_error="access_denied")
    assert request_log._FIELDS_KEY not in request.scope

    capture.set_level(logging.DEBUG, logger=ROUTE_LOGGER)
    app = _build_app(clock, before_install=_add_id_routes)
    await _get(app, "/api/v1/oauth-field")

    _, end = _one_end(capture)
    (inside,) = _inside(capture)
    assert end["oauth_error"] == "access_denied" and "unused" not in end
    assert "oauth_error" not in inside


# --- a client that leaves before a plain response (F49) ----------------------------------------------------------


class _Server:
    """uvicorn's side of one request (h11_impl.py receive/send): the request body first, then ``http.disconnect`` only
    once the client left or the response is complete. Counts ``receive`` calls."""

    def __init__(self, *body: bytes) -> None:
        self.body = [{"type": "http.request", "body": part, "more_body": i < len(body) - 1} for i, part in enumerate(body)]
        self.calls = 0
        self.second_read = asyncio.Event()
        self.gone = asyncio.Event()  # the client left, or the response is complete
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        self.calls += 1
        if self.calls == 2:
            self.second_read.set()
        if self.body:
            return self.body.pop(0)
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            self.gone.set()

    def answer(self) -> object:
        return json.loads(b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body"))


def _scope(method: str, path: str) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("10.1.2.3", 50000), "server": ("testserver", 80),
    }


@pytest.fixture
def disconnect_seen(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Set when the request's line observes ``http.disconnect``: the sync point between the client leaving and the route."""
    seen = asyncio.Event()
    observe = request_log._Request.observe

    def spy(self: request_log._Request, message: dict) -> None:
        observe(self, message)
        if message["type"] == "http.disconnect":
            seen.set()

    monkeypatch.setattr(request_log._Request, "observe", spy)
    return seen


async def _within_a_second(event: asyncio.Event) -> None:
    """Without the fix the line never sees the client leave: the route goes on after 1 s and the assertion fails."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), 1)


async def _settle() -> None:
    for _ in range(50):  # every runnable task runs; no I/O, so deterministic
        await asyncio.sleep(0)


def _indexing_app() -> FastAPI:
    """indexing_main's shape: no auth middleware between the line and the routes."""
    app = FastAPI()
    install_request_logging(app)
    app.add_middleware(MetricsMiddleware, service_name="test_service")
    return app


def _shaped_app(clock: _Clock, shape: str) -> FastAPI:
    return _build_app(clock) if shape == "query" else _indexing_app()


def _add_slow_routes(app: FastAPI, wait: Callable[[], Awaitable[None]]) -> None:
    @app.get("/api/v1/slow")
    async def slow() -> dict:
        await wait()
        return {"ok": True}

    @app.post("/api/v1/slow")
    async def slow_after_body(request: Request) -> dict:
        await request.json()
        await wait()
        return {"ok": True}


SHAPES = pytest.mark.parametrize("shape", ["query", "indexing"])
METHODS = pytest.mark.parametrize("method", ["GET", "POST"])


@SHAPES
@METHODS
async def test_a_client_that_leaves_before_a_plain_response_is_cancelled_not_success(
        clock: _Clock, capture, disconnect_seen: asyncio.Event, shape: str, method: str) -> None:
    """Nothing reads ``receive`` while the route works, and uvicorn's send drops the answer silently."""
    app = _shaped_app(clock, shape)
    _add_slow_routes(app, lambda: _within_a_second(disconnect_seen))
    server = _Server(b'{"q": 1}')
    server.gone.set()  # the client left while the route was working

    await asyncio.wait_for(app(_scope(method, "/api/v1/slow"), server.receive, server.send), timeout=10)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        200, "failure", "cancelled", "client_disconnect")
    assert not record.exc_text


@SHAPES
@METHODS
async def test_control_a_client_that_stays_for_a_plain_response_is_success(clock: _Clock, capture, shape: str,
                                                                           method: str) -> None:
    app = _shaped_app(clock, shape)
    _add_slow_routes(app, _settle)  # the read-ahead is parked in receive when the route answers
    server = _Server(b'{"q": 1}')

    await asyncio.wait_for(app(_scope(method, "/api/v1/slow"), server.receive, server.send), timeout=10)

    assert server.answer() == {"ok": True}
    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert fields["outcome"] == "success" and "reason" not in fields


@pytest.mark.parametrize(
    ("kind", "level", "status", "cls", "reason"),
    [
        ("refused", logging.WARNING, 403, "forbidden", "org_mismatch"),
        ("503", logging.WARNING, 503, "unavailable", None),
        ("marked", logging.WARNING, 200, "unavailable", None),
        ("302", logging.INFO, 302, "cancelled", "client_disconnect"),
    ],
)
async def test_after_the_client_left_only_a_success_becomes_cancelled(
        clock: _Clock, capture, disconnect_seen: asyncio.Event, kind: str, level: int, status: int, cls: str,
        reason: str | None) -> None:
    """A refusal, a 5xx or a marked failure is the better answer to "why"; org_mismatch stays WARNING."""
    app = _build_app(clock)

    @app.get("/api/v1/late/{kind}")
    async def late(request: Request, kind: str) -> JSONResponse:
        await _within_a_second(disconnect_seen)
        if kind == "refused":
            raise AuthRejected(403, "org_id does not match the service token", reason="org_mismatch")
        if kind == "marked":
            mark_request_failure(request, error_class="unavailable")
            return JSONResponse({"ok": True})
        return JSONResponse({}, status_code=int(kind))

    server = _Server(b"")
    server.gone.set()

    await asyncio.wait_for(app(_scope("GET", f"/api/v1/late/{kind}"), server.receive, server.send), timeout=10)

    assert disconnect_seen.is_set()  # the client's leaving was seen
    record, fields = _one_end(capture)
    assert record.levelno == level
    assert (fields["status"], fields["outcome"], fields["error_class"], fields.get("reason")) == (
        status, "failure", cls, reason)


@SHAPES
async def test_the_body_reaches_the_route_in_order_and_is_read_one_message_ahead(clock: _Clock, capture,
                                                                                 shape: str) -> None:
    app = _shaped_app(clock, shape)
    gate = asyncio.Event()

    @app.post("/api/v1/gated-body")
    async def gated_body(request: Request) -> dict:
        await gate.wait()
        return {"body": (await request.body()).decode()}

    server = _Server(b'{"a": ', b'"b"', b"}")
    request = asyncio.create_task(app(_scope("POST", "/api/v1/gated-body"), server.receive, server.send))
    await asyncio.wait_for(server.second_read.wait(), 1)
    await _settle()
    assert server.calls == 2  # one message in the slot, one in the read-ahead's hand: a body is not buffered
    gate.set()
    await asyncio.wait_for(request, timeout=10)

    assert server.answer() == {"body": '{"a": "b"}'}


async def test_after_a_disconnect_every_later_receive_is_a_disconnect(clock: _Clock, capture,
                                                                      disconnect_seen: asyncio.Event) -> None:
    app = _indexing_app()

    @app.get("/api/v1/listen")
    async def listen(request: Request) -> dict:
        await _within_a_second(disconnect_seen)
        types = [(await asyncio.wait_for(request.receive(), 2))["type"] for _ in range(4)]
        return {"types": types}

    server = _Server(b"")
    server.gone.set()

    await asyncio.wait_for(app(_scope("GET", "/api/v1/listen"), server.receive, server.send), timeout=10)

    assert server.answer() == {"types": ["http.request"] + ["http.disconnect"] * 3}


class _ReceiveBroke(Exception):
    pass


@pytest.mark.parametrize("error", [_ReceiveBroke("receive broke"), ExceptionGroup("receive broke", [OSError("reset")])])
async def test_a_receive_that_raises_reaches_the_app_every_time(capture, error: Exception) -> None:
    """Any Exception: through MetricsMiddleware's task group a receive error arrives as an ExceptionGroup."""
    errors: list[Exception] = []
    server = _Server()

    async def reads_twice(scope, receive, send) -> None:
        for _ in range(2):
            try:
                await asyncio.wait_for(receive(), 2)
            except TimeoutError:
                raise
            except Exception as exc:
                errors.append(exc)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def broken_receive() -> dict:
        raise error

    await asyncio.wait_for(RequestLogMiddleware(reads_twice)(_raw_scope(), broken_receive, server.send), 10)

    assert errors == [error, error]


@pytest.mark.parametrize("ending", ["answered", "crashed", "cancelled"])
async def test_the_read_ahead_task_ends_with_the_request(clock: _Clock, capture, ending: str) -> None:
    app = _build_app(clock)
    pumps: list[asyncio.Task] = []
    collected = asyncio.Event()

    @app.get("/api/v1/ending")
    async def ending_route() -> dict:
        pumps.extend(t for t in asyncio.all_tasks() if t.get_coro().__qualname__ == "_Inbox._pump")
        collected.set()
        if ending == "crashed":
            raise RuntimeError("route exploded")
        if ending == "cancelled":
            await asyncio.Event().wait()
        return {"ok": True}

    server = _Server(b"")  # the client stays: only the end of the request can stop the read-ahead
    request = asyncio.create_task(app(_scope("GET", "/api/v1/ending"), server.receive, server.send))
    await asyncio.wait_for(collected.wait(), 10)
    if ending == "cancelled":
        request.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(request, timeout=10)

    assert pumps, "no read-ahead task ran"
    await asyncio.wait_for(asyncio.gather(*pumps, return_exceptions=True), 10)
    assert all(p.done() for p in pumps)


@pytest.mark.parametrize(("shape", "polls"), [("indexing", [False, True]), ("query", [False, False])])
async def test_request_is_disconnected_in_a_route(clock: _Clock, capture, disconnect_seen: asyncio.Event, shape: str,
                                                  polls: list[bool]) -> None:
    """Indexing now sees the disconnect (it reads the read-ahead's slot). Behind the auth BaseHTTPMiddleware the poll is
    cancelled at its task group's exit, as on main (F9)."""
    app = _shaped_app(clock, shape)
    first_poll = asyncio.Event()
    server = _Server(b'{"q": 1}')

    @app.post("/api/v1/poll")
    async def poll(request: Request) -> dict:
        await request.json()
        seen = [await request.is_disconnected()]
        first_poll.set()
        await _within_a_second(disconnect_seen)
        seen.append(await request.is_disconnected())
        return {"polls": seen}

    request = asyncio.create_task(app(_scope("POST", "/api/v1/poll"), server.receive, server.send))
    await asyncio.wait_for(first_poll.wait(), 10)
    server.gone.set()
    await asyncio.wait_for(request, timeout=10)

    assert server.answer() == {"polls": polls}


# --- the line's decision table (docs/logging.md, "When the client leaves") -------------------------------------------


# (row, escaped, marked, status, final body handed over, client left) -> branch. One row per row of the table.
END_TABLE = [
    ("E1 escaped exception wins, whatever else", True, True, 503, False, "answering", "exception"),
    ("E2 escaped exception, client never left", True, False, None, False, None, "exception"),
    ("C1 left mid-answer, 2xx", False, False, 200, False, "answering", "cancelled"),
    ("C2 left mid-answer, 5xx, last body sent later by a BaseHTTPMiddleware", False, False, 503, True, "answering", "cancelled"),
    ("C3 left mid-answer, 4xx", False, False, 404, True, "answering", "cancelled"),
    ("C4 left mid-answer, marked failure", False, True, 502, False, "answering", "cancelled"),
    ("C5 no last body, no disconnect read", False, False, 200, False, None, "cancelled"),
    ("C6 no last body, no disconnect read, marked", False, True, 503, False, None, "cancelled"),
    ("L1 left before the answer, 2xx", False, False, 200, True, "before_answer", "cancelled"),
    ("L2 left before the answer, 3xx", False, False, 302, True, "before_answer", "cancelled"),
    ("L3 left before the answer, nothing answered", False, False, None, False, "before_answer", "cancelled"),
    ("L4 left before the answer, 4xx", False, False, 403, True, "before_answer", "status"),
    ("L5 left before the answer, 5xx", False, False, 503, True, "before_answer", "status"),
    ("L6 left before the answer, 5xx stream with no last body", False, False, 503, False, "before_answer", "status"),
    ("L7 left before the answer, marked 2xx", False, True, 200, True, "before_answer", "failure"),
    ("L8 left before the answer, marked 5xx", False, True, 502, True, "before_answer", "failure"),
    ("L9 left before the answer, marked 4xx", False, True, 409, True, "before_answer", "status"),
    ("L10 left before the answer, marked, nothing answered", False, True, None, False, "before_answer", "failure"),
    ("N1 complete, 2xx", False, False, 200, True, None, "success"),
    ("N2 complete, 3xx", False, False, 302, True, None, "success"),
    ("N3 complete, 4xx", False, False, 404, True, None, "status"),
    ("N4 complete, 5xx", False, False, 503, True, None, "status"),
    ("N5 complete, marked 2xx", False, True, 200, True, None, "failure"),
    ("N6 complete, marked 5xx", False, True, 500, True, None, "failure"),
    ("N7 complete, marked 4xx", False, True, 409, True, None, "status"),
]


@pytest.mark.parametrize(("row", "escaped", "marked", "status", "finished", "left", "branch"), END_TABLE,
                         ids=[r[0].split()[0] for r in END_TABLE])
def test_the_end_outcome_follows_the_table(row: str, escaped: bool, marked: bool, status: int | None, finished: bool,
                                           left: str | None, branch: str) -> None:
    assert request_log._end_outcome(escaped=escaped, marked=marked, status=status, finished=finished, left=left) == branch


def _add_marked_stalled_route(app: FastAPI) -> None:
    @app.get("/api/v1/marked-stalled/{code}")
    async def marked_stalled(request: Request, code: int) -> StreamingResponse:
        mark_request_failure(request, error_class="unavailable")

        async def body() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            await asyncio.Event().wait()

        return StreamingResponse(body(), status_code=code, media_type="text/event-stream")


@SHAPES
@pytest.mark.parametrize("code", [200, 502])
async def test_a_marked_failure_cut_mid_answer_is_cancelled(clock: _Clock, capture, shape: str, code: int) -> None:
    """Row C4: the client never got the answer, so the cut wins over the mark (#5681 round 2)."""
    app = _shaped_app(clock, shape)
    _add_marked_stalled_route(app)

    await _drive(app, f"/api/v1/marked-stalled/{code}", Recorder(), disconnect=True)

    record, fields = _one_end(capture)
    assert record.levelno == logging.INFO
    assert (fields["status"], fields["outcome"], fields["error_class"], fields["reason"]) == (
        code, "failure", "cancelled", "client_disconnect")


@SHAPES
@pytest.mark.parametrize(("code", "level", "cls", "reason"), [
    (503, logging.WARNING, "unavailable", None),
    (200, logging.INFO, "cancelled", "client_disconnect"),
])
async def test_a_stream_started_after_the_client_left_follows_the_late_answer_rule(
        clock: _Clock, capture, disconnect_seen: asyncio.Event, shape: str, code: int, level: int, cls: str,
        reason: str | None) -> None:
    """Rows L5/L6 and L1: whether or not a last body message follows (the auth BaseHTTPMiddleware sends one; with no
    middleware below, Starlette stops the stream without it), a 503 the route started after the client left keeps its
    own line and a 200 is cancelled (#5681 round 2)."""
    app = _shaped_app(clock, shape)

    @app.get("/api/v1/late-stream/{code}")
    async def late_stream(code: int) -> StreamingResponse:
        await _within_a_second(disconnect_seen)

        async def body() -> AsyncIterator[bytes]:
            yield b"data: one\n\n"
            await asyncio.Event().wait()

        return StreamingResponse(body(), status_code=code, media_type="text/event-stream")

    server = _Server(b"")
    server.gone.set()

    await asyncio.wait_for(app(_scope("GET", f"/api/v1/late-stream/{code}"), server.receive, server.send), timeout=10)

    assert [m["status"] for m in server.sent if m["type"] == "http.response.start"] == [code]  # the answer did start
    record, fields = _one_end(capture)
    assert record.levelno == level
    assert (fields["status"], fields["outcome"], fields["error_class"], fields.get("reason")) == (
        code, "failure", cls, reason)
