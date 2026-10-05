"""Behaviour of the pinned FastAPI/Starlette pair that the services depend on.

These run against small apps shaped like ours (a function middleware under
``MetricsMiddleware``, streaming responses with a background task, form bodies
read by a dependency before the endpoint) so a framework upgrade that changes
any of it fails here rather than in a deployed service.
"""

import asyncio
import hashlib
import warnings
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.api.middlewares.auth import authMiddleware
from app.api.routes.parsing import router as parsing_router
from app.services.parsing.registry import ParserRegistry
from app.telemetry.middleware import MetricsMiddleware

MIB = 1024 * 1024
CHUNKS = [b"chunk-0;", b"chunk-1;", b"chunk-2;"]

_middleware_value: ContextVar[str | None] = ContextVar("middleware_value", default=None)
_dependency_value: ContextVar[str | None] = ContextVar("dependency_value", default=None)


@dataclass
class Recorder:
    events: list[str] = field(default_factory=list)
    seen: dict[str, object] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def http_requests_metric(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """MetricsMiddleware otherwise writes to the process-wide collectors and seen-routes set."""
    requests_metric = MagicMock()
    monkeypatch.setattr("app.telemetry.middleware.HTTP_REQUESTS", requests_metric)
    monkeypatch.setattr("app.telemetry.middleware.HTTP_REQUEST_DURATION", MagicMock())
    monkeypatch.setattr("app.telemetry.modules.http_metrics._seen_routes", set())
    return requests_metric


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _form_app() -> FastAPI:
    app = FastAPI()

    @app.post("/form")
    async def read_form(request: Request) -> dict:
        form = await request.form()
        uploads = {
            name: len(await value.read())
            for name, value in form.items()
            if not isinstance(value, str)
        }
        return {"fields": len(form), "uploads": uploads}

    return app


async def _post_urlencoded(body: str):
    async with _client(_form_app()) as client:
        return await client.post(
            "/form",
            content=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )


def _stacked_app(recorder: Recorder) -> FastAPI:
    """A function middleware under MetricsMiddleware, as in query_main and connectors_main."""
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        request.state.user = {"orgId": "org-1", "email": "user@example.test"}
        _middleware_value.set("from-middleware")
        return await call_next(request)

    app.add_middleware(MetricsMiddleware, service_name="test_service")

    async def bind_context() -> None:
        _dependency_value.set("from-dependency")

    def context_values() -> tuple[str | None, str | None]:
        return _middleware_value.get(), _dependency_value.get()

    async def background() -> None:
        recorder.events.append("background")
        recorder.seen["background"] = context_values()

    @app.get("/stream/{item_id}", dependencies=[Depends(bind_context)])
    async def stream(item_id: str, request: Request) -> StreamingResponse:
        recorder.seen["user"] = request.state.user

        async def body():
            recorder.seen["generator"] = context_values()
            for chunk in CHUNKS:
                yield chunk

        return StreamingResponse(body(), background=BackgroundTask(background))

    @app.get("/stalled", dependencies=[Depends(bind_context)])
    async def stalled() -> StreamingResponse:
        async def body():
            try:
                yield CHUNKS[0]
                await asyncio.Event().wait()
            finally:
                recorder.events.append("generator-closed")

        return StreamingResponse(body(), background=BackgroundTask(background))

    return app


async def _drive(app: FastAPI, path: str, recorder: Recorder, *, disconnect: bool = False) -> list[dict]:
    """Call the ASGI app directly, optionally disconnecting once the first chunk is out."""
    request_delivered = False
    first_chunk_sent = asyncio.Event()
    messages = []

    async def receive() -> dict:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if not disconnect:
            await asyncio.Event().wait()
        await first_chunk_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            recorder.events.append(f"sent:{message['body'].decode()}")
            first_chunk_sent.set()

    scope = {
        "type": "http",
        # uvicorn's HTTP spec version; below 2.4 Starlette watches receive() for a disconnect.
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return messages


async def test_urlencoded_form_accepts_1000_fields_and_rejects_1001():
    at_limit = await _post_urlencoded(urlencode({f"f{i}": "1" for i in range(1000)}))
    over_limit = await _post_urlencoded(urlencode({f"f{i}": "1" for i in range(1001)}))

    assert at_limit.status_code == 200
    assert at_limit.json()["fields"] == 1000
    assert over_limit.status_code == 400


async def test_urlencoded_field_over_1_mib_is_rejected():
    response = await _post_urlencoded("note=" + "x" * (MIB + 1))

    assert response.status_code == 400


async def test_multipart_accepts_a_5_mib_file_part():
    async with _client(_form_app()) as client:
        response = await client.post(
            "/form", files={"file": ("big.bin", b"\0" * (5 * MIB), "application/octet-stream")}
        )

    assert response.status_code == 200
    assert response.json()["uploads"] == {"file": 5 * MIB}


async def test_multipart_non_file_part_over_1_mib_is_rejected():
    async with _client(_form_app()) as client:
        response = await client.post(
            "/form",
            files={
                "file": ("small.bin", b"data", "application/octet-stream"),
                "note": (None, "x" * (MIB + 1)),
            },
        )

    assert response.status_code == 400


async def test_form_read_by_a_router_dependency_is_still_available_to_the_endpoint():
    seen_by_dependency = {}

    async def read_form_first(request: Request) -> None:
        form = await request.form()
        seen_by_dependency["org_id"] = form.get("org_id")
        seen_by_dependency["filename"] = form["file"].filename

    router = APIRouter(dependencies=[Depends(read_form_first)])

    @router.post("/upload")
    async def upload(
        file: Annotated[UploadFile, File()], org_id: Annotated[str, Form()]
    ) -> dict:
        return {
            "org_id": org_id,
            "filename": file.filename,
            "sha256": hashlib.sha256(await file.read()).hexdigest(),
        }

    app = FastAPI()
    app.include_router(router)
    payload = bytes(range(256)) * (2 * MIB // 256)

    async with _client(app) as client:
        response = await client.post(
            "/upload",
            files={"file": ("report.pdf", payload, "application/pdf")},
            data={"org_id": "org-1"},
        )

    assert response.status_code == 200
    assert seen_by_dependency == {"org_id": "org-1", "filename": "report.pdf"}
    assert response.json() == {
        "org_id": "org-1",
        "filename": "report.pdf",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


async def test_streamed_chunks_arrive_in_order_and_the_background_task_runs_once_after_them():
    recorder = Recorder()

    messages = await _drive(_stacked_app(recorder), "/stream/42", recorder)

    bodies = [m["body"] for m in messages if m["type"] == "http.response.body" and m["body"]]
    assert bodies == CHUNKS
    assert recorder.events == [*(f"sent:{chunk.decode()}" for chunk in CHUNKS), "background"]


async def test_contextvars_from_middleware_and_dependency_reach_the_stream_and_background_task():
    recorder = Recorder()

    async with _client(_stacked_app(recorder)) as client:
        response = await client.get("/stream/42")

    assert response.content == b"".join(CHUNKS)
    assert recorder.seen["generator"] == ("from-middleware", "from-dependency")
    assert recorder.seen["background"] == ("from-middleware", "from-dependency")


async def test_request_state_reaches_the_endpoint_and_metrics_record_the_route_template(
    http_requests_metric,
):
    recorder = Recorder()

    async with _client(_stacked_app(recorder)) as client:
        response = await client.get("/stream/42")

    assert response.status_code == 200
    assert recorder.seen["user"] == {"orgId": "org-1", "email": "user@example.test"}
    http_requests_metric.inc.assert_called_once_with(
        "test_service", "/stream/{item_id}", "GET", "200", "org-1", "example.test"
    )


async def test_client_disconnect_mid_stream_closes_the_generator_and_still_runs_the_background_task():
    recorder = Recorder()

    await _drive(_stacked_app(recorder), "/stalled", recorder, disconnect=True)

    assert recorder.events == [f"sent:{CHUNKS[0].decode()}", "generator-closed", "background"]


async def test_json_body_model_requires_a_json_content_type_but_request_json_does_not():
    class Item(BaseModel):
        name: str

    app = FastAPI()

    @app.post("/parsed")
    async def parsed(item: Item) -> dict:
        return {"name": item.name}

    @app.post("/raw")
    async def raw(request: Request) -> dict:
        return {"name": (await request.json())["name"]}

    body = b'{"name": "widget"}'
    json_header = {"Content-Type": "application/json"}

    async with _client(app) as client:
        parsed_without_type = await client.post("/parsed", content=body)
        parsed_with_type = await client.post("/parsed", content=body, headers=json_header)
        raw_without_type = await client.post("/raw", content=body)
        raw_with_type = await client.post("/raw", content=body, headers=json_header)

    assert "content-type" not in parsed_without_type.request.headers
    assert parsed_without_type.status_code == 422
    assert parsed_with_type.status_code == 200
    assert raw_without_type.status_code == 200
    assert raw_with_type.status_code == 200


async def _authenticated_as_indexing(request: Request) -> Request:
    # The parse route needs a service token with the parse scope (#3818).
    request.state.user = {"token_type": "scoped", "scopes": ["document:parse"], "orgId": "org-123"}
    return request


async def test_parse_route_answers_422_for_an_unknown_provider_without_deprecation_warnings():
    app = FastAPI()
    app.state.parser_registry = MagicMock(spec=ParserRegistry)
    app.include_router(parsing_router)
    app.dependency_overrides[authMiddleware] = _authenticated_as_indexing

    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated")
        async with _client(app) as client:
            response = await client.post(
                "/api/v1/parse",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
                data={"mime_type": "application/pdf", "provider": "nonexistent_provider"},
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


async def test_cors_echoes_the_origin_of_a_request_without_cookies():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    async with _client(app) as client:
        response = await client.get("/ping", headers={"Origin": "https://app.example.test"})

    assert response.headers["access-control-allow-origin"] == "https://app.example.test"
    assert "Origin" in response.headers["vary"]
