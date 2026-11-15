"""Unit tests for app.telemetry.middleware — the ASGI metrics middleware."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.telemetry.middleware import MetricsMiddleware
from app.utils.route_template import route_template


def make_request(
    path="/api/v1/things",
    method="GET",
    route=None,
    user=None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "state": {},
    }
    if route is not None:
        scope["route"] = route
    request = Request(scope)
    if user is not None:
        request.state.user = user
    return request


class TestRouteTemplate:
    """MetricsMiddleware and http.request.end name a route with one helper (app.utils.route_template)."""

    def test_prefers_matched_route_path_format(self) -> None:
        route = SimpleNamespace(path_format="/api/v1/agent/{id}", path="/raw")
        request = make_request(path="/api/v1/agent/42", route=route)

        assert route_template(request.scope) == "/api/v1/agent/{id}"

    def test_falls_back_to_route_path(self) -> None:
        route = SimpleNamespace(path_format=None, path="/api/v1/agent/{id}")
        request = make_request(path="/api/v1/agent/42", route=route)

        assert route_template(request.scope) == "/api/v1/agent/{id}"

    def test_collapses_to_unmatched_when_no_route_matched(self) -> None:
        request = make_request(path="/api/v1/agent/12345")

        assert route_template(request.scope) == "unmatched"

    def test_a_request_refused_before_routing_gets_the_route_it_would_match_full_before_partial(self) -> None:
        """Starlette's router takes the first FULL match, else the first PARTIAL (a 405)."""
        app = FastAPI()

        @app.post("/api/v1/kb/{a}")
        async def by_post(a: str) -> None: ...

        @app.get("/api/v1/kb/{b}")
        async def by_get(b: str) -> None: ...

        @app.get("/api/v1/files/{rest:path}")
        async def files(rest: str) -> None: ...

        def scope(method: str, path: str) -> dict:
            return {**make_request(path=path, method=method).scope, "app": app, "root_path": ""}

        assert route_template(scope("GET", "/api/v1/kb/42")) == "/api/v1/kb/{b}"
        assert route_template(scope("POST", "/api/v1/kb/42")) == "/api/v1/kb/{a}"
        assert route_template(scope("DELETE", "/api/v1/kb/42")) == "/api/v1/kb/{a}"
        assert route_template(scope("GET", "/api/v1/files/a/b.txt")) == "/api/v1/files/{rest}"
        assert route_template(scope("GET", "/api/v1/nothing/here")) == "unmatched"

    def test_a_non_string_template_is_never_returned(self) -> None:
        request = make_request(route=SimpleNamespace(path_format=MagicMock(), path=None))

        assert route_template(request.scope) == "unmatched"


class TestIdentityExtraction:
    def test_org_from_request_state(self):
        request = make_request(user={"orgId": "org-1"})
        assert MetricsMiddleware._org(request) == "org-1"

    def test_org_snake_case_fallback(self):
        request = make_request(user={"org_id": "org-2"})
        assert MetricsMiddleware._org(request) == "org-2"

    def test_org_unknown_without_user(self):
        assert MetricsMiddleware._org(make_request()) == "unknown"
        assert MetricsMiddleware._org(make_request(user="not-a-dict")) == "unknown"

    def test_domain_from_user_email(self):
        request = make_request(user={"email": "jane@Acme.IO"})
        assert MetricsMiddleware._domain(request) == "acme.io"

    def test_domain_unknown_without_user(self):
        assert MetricsMiddleware._domain(make_request()) == "unknown"


class TestDispatch:
    @staticmethod
    async def _call(scope: dict, app) -> list[dict]:
        sent: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)

        await MetricsMiddleware(app=app, service_name="query_service")(scope, receive, send)
        return sent

    async def test_records_count_and_latency_on_success(self):
        request = make_request(
            path="/api/v1/search",
            method="POST",
            route=SimpleNamespace(path_format="/api/v1/search", path="/api/v1/search"),
            user={"orgId": "org-9", "email": "a@b.io"},
        )

        async def app(_scope, _receive, send) -> None:
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        with (
            patch("app.telemetry.middleware.HTTP_REQUESTS") as requests_mock,
            patch("app.telemetry.middleware.HTTP_REQUEST_DURATION") as duration_mock,
        ):
            sent = await self._call(request.scope, app)

        assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
        requests_mock.inc.assert_called_once_with(
            "query_service", "/api/v1/search", "POST", "201", "org-9", "b.io"
        )
        args, kwargs = duration_mock.observe.call_args
        assert args == ("query_service", "/api/v1/search", "POST")
        assert kwargs["value"] >= 0

    async def test_a_response_still_streaming_is_already_counted(self) -> None:
        """Recorded when the response starts, as before: a long stream is in every push while it runs."""
        request = make_request(
            path="/api/v1/stream", route=SimpleNamespace(path_format="/api/v1/stream", path="/api/v1/stream")
        )
        body_may_go, started = asyncio.Event(), asyncio.Event()

        async def app(_scope, _receive, send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            started.set()
            await body_may_go.wait()
            await send({"type": "http.response.body", "body": b"done"})

        with (
            patch("app.telemetry.middleware.HTTP_REQUESTS") as requests_mock,
            patch("app.telemetry.middleware.HTTP_REQUEST_DURATION") as duration_mock,
        ):
            call = asyncio.ensure_future(self._call(request.scope, app))
            await started.wait()
            requests_mock.inc.assert_called_once_with(
                "query_service", "/api/v1/stream", "GET", "200", "unknown", "unknown"
            )
            duration_mock.observe.assert_called_once()
            body_may_go.set()
            await call

        requests_mock.inc.assert_called_once()
        duration_mock.observe.assert_called_once()

    async def test_records_500_when_handler_raises(self):
        request = make_request(
            path="/api/v1/boom",
            route=SimpleNamespace(path_format="/api/v1/boom", path="/api/v1/boom"),
        )
        app = AsyncMock(side_effect=RuntimeError("kaboom"))

        with (
            patch("app.telemetry.middleware.HTTP_REQUESTS") as requests_mock,
            patch("app.telemetry.middleware.HTTP_REQUEST_DURATION"),
        ):
            with pytest.raises(RuntimeError):
                await self._call(request.scope, app)

        requests_mock.inc.assert_called_once_with(
            "query_service", "/api/v1/boom", "GET", "500", "unknown", "unknown"
        )
