"""Unit tests for app.telemetry.middleware — the ASGI metrics middleware."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from app.telemetry.middleware import MetricsMiddleware, _normalize_path


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


def make_middleware(service_name="query_service") -> MetricsMiddleware:
    return MetricsMiddleware(app=MagicMock(), service_name=service_name)


class TestNormalizePath:
    def test_replaces_object_ids_uuids_and_numbers(self):
        path = "/api/v1/users/507f1f77bcf86cd799439011/items/42/e2c1a1f0-1234-4abc-9def-1234567890ab"
        assert _normalize_path(path) == "/api/v1/users/:id/items/:id/:id"

    def test_keeps_plain_segments(self):
        assert _normalize_path("/api/v1/search") == "/api/v1/search"

    def test_empty_path_becomes_root(self):
        assert _normalize_path("") == "/"


class TestRouteTemplate:
    def test_prefers_matched_route_path_format(self):
        route = SimpleNamespace(path_format="/api/v1/agent/{id}", path="/raw")
        request = make_request(path="/api/v1/agent/42", route=route)

        assert MetricsMiddleware._route_template(request) == "/api/v1/agent/{id}"

    def test_falls_back_to_route_path(self):
        route = SimpleNamespace(path_format=None, path="/api/v1/agent/{id}")
        request = make_request(path="/api/v1/agent/42", route=route)

        assert MetricsMiddleware._route_template(request) == "/api/v1/agent/{id}"

    def test_normalizes_raw_path_when_no_route_matched(self):
        request = make_request(path="/api/v1/agent/12345")

        assert MetricsMiddleware._route_template(request) == "/api/v1/agent/:id"


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
    async def test_records_count_and_latency_on_success(self):
        middleware = make_middleware()
        request = make_request(
            path="/api/v1/search",
            method="POST",
            user={"orgId": "org-9", "email": "a@b.io"},
        )
        response = MagicMock(status_code=201)
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.telemetry.middleware.HTTP_REQUESTS") as requests_mock,
            patch("app.telemetry.middleware.HTTP_REQUEST_DURATION") as duration_mock,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result is response
        requests_mock.inc.assert_called_once_with(
            "query_service", "/api/v1/search", "POST", "201", "org-9", "b.io"
        )
        args, kwargs = duration_mock.observe.call_args
        assert args == ("query_service", "/api/v1/search", "POST")
        assert kwargs["value"] >= 0

    async def test_records_500_when_handler_raises(self):
        middleware = make_middleware()
        request = make_request(path="/api/v1/boom")
        call_next = AsyncMock(side_effect=RuntimeError("kaboom"))

        with (
            patch("app.telemetry.middleware.HTTP_REQUESTS") as requests_mock,
            patch("app.telemetry.middleware.HTTP_REQUEST_DURATION"),
        ):
            with pytest.raises(RuntimeError):
                await middleware.dispatch(request, call_next)

        requests_mock.inc.assert_called_once_with(
            "query_service", "/api/v1/boom", "GET", "500", "unknown", "unknown"
        )
