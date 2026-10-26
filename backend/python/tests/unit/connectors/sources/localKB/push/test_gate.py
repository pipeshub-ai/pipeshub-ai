"""ENABLE_KB_PUSH off: the app answers exactly like an app built without the push routes."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.middlewares.auth import KNOWLEDGE_LIMIT_CLAIM
from app.connectors.sources.localKB.api import kb_router as R
from app.connectors.sources.localKB.push import gate

from .bodies import envelope, raw

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.responses import Response

JSON = {"content-type": "application/json"}
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
PATHS = [
    "/api/v1/kb/kbA/records", "/api/v1/kb/kbA/records/", "/api/v1/kb/kbA/records?folderId=f",
    "/api/v1/kb/kbA/schema", "/api/v1/kb/kbA/schema/", "/api/v1/kb/kbA/schema?version=1",
    "/api/v1/kb/kbA/no-such-route",
]
USERS = {
    "plain": {"userId": "uA", "orgId": "orgA"},
    "limited": {
        "userId": "uA", "orgId": "orgA", "isOAuth": True, "oauthScopes": ["kb:upload", "kb:write", "kb:read"],
        KNOWLEDGE_LIMIT_CLAIM: {"apps": ["otherKb"]},
    },
}


class GraphSpy:
    """Counts every call; answers what the connection check needs."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., Awaitable[object]]:
        async def call(*_a: object, **_k: object) -> object:
            self.calls.append(name)
            if name == "get_user_by_user_id":
                return {"_key": "ukA"}
            if name == "check_access":
                return types.SimpleNamespace(node_ids_in_scope=[])
            return None

        return call


def build_app(*, with_push: bool, flag: object, user: str) -> tuple[FastAPI, GraphSpy]:
    app = FastAPI()
    app.include_router(R.kb_router)
    if with_push:
        app.include_router(R.kb_push_router)
        app.add_middleware(gate.KbPushFlagMiddleware)
    graph = GraphSpy()
    config = MagicMock()

    async def get_config(key: str, default: object = None, use_cache: bool = True, raise_on_error: bool = False) -> object:
        if isinstance(flag, Exception):
            raise flag
        return {"featureFlags": {"ENABLE_KB_PUSH": flag}}

    config.get_config = get_config
    app.container = MagicMock()
    app.container.config_service.return_value = config
    app.state.graph_provider = graph
    app.dependency_overrides[R.get_kb_service] = lambda: MagicMock()
    app.dependency_overrides[R.get_kafka_service] = lambda: MagicMock()

    @app.middleware("http")
    async def inject_user(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = dict(USERS[user])
        return await call_next(request)

    return app, graph


def ask(app: FastAPI, method: str, path: str) -> tuple:
    client = TestClient(app, raise_server_exceptions=False)
    body = raw(envelope()) if method in ("POST", "PUT", "PATCH") else None
    r = client.request(method, path, content=body, headers=JSON, follow_redirects=False)
    return r.status_code, r.headers.get("allow"), r.headers.get("location"), r.text


@pytest.mark.parametrize("user", list(USERS))
@pytest.mark.parametrize("method", METHODS)
def test_flag_off_answers_like_an_app_without_the_routes(method: str, user: str) -> None:
    base, base_graph = build_app(with_push=False, flag=False, user=user)
    head, head_graph = build_app(with_push=True, flag=False, user=user)
    for path in PATHS:
        base_graph.calls.clear()
        head_graph.calls.clear()
        assert ask(head, method, path) == ask(base, method, path), (method, path)
        assert head_graph.calls == base_graph.calls, (method, path, head_graph.calls)


def test_flag_off_makes_no_graph_call_for_a_connection_limited_token() -> None:
    for method, path in (("POST", PATHS[0]), ("PUT", PATHS[3]), ("GET", PATHS[3])):
        app, graph = build_app(with_push=True, flag=False, user="limited")
        status, *_ = ask(app, method, path)
        assert status in (404, 405) and graph.calls == [], (method, path, status, graph.calls)


def test_flag_on_the_same_limited_token_reaches_the_connection_check() -> None:
    """Control: the routes exist when the flag is on, and then the connection check runs first (403)."""
    app, graph = build_app(with_push=True, flag=True, user="limited")
    status, *_ = ask(app, "POST", PATHS[0])
    assert status == 403 and "check_access" in graph.calls


def test_flag_on_without_the_gate_dependency_order_would_differ() -> None:
    """Negative control for a dependency-based gate (flag checked after the router's
    connection check) lets the limited token's request reach the graph; the route-matching gate does not."""
    app, graph = build_app(with_push=True, flag=False, user="limited")
    ask(app, "PUT", PATHS[3])
    assert graph.calls == []


@pytest.mark.parametrize("flag", [RuntimeError("config store down"), False, None, 0])
def test_an_unreadable_or_false_flag_is_off(flag: object) -> None:
    app, _ = build_app(with_push=True, flag=flag, user="plain")
    assert ask(app, "POST", PATHS[0])[0] == 405
    assert ask(app, "PUT", PATHS[3])[0] == 404


def test_an_app_without_the_middleware_has_the_routes_off() -> None:
    app, _ = build_app(with_push=True, flag=True, user="plain")
    app.user_middleware = [m for m in app.user_middleware if m.cls is not gate.KbPushFlagMiddleware]
    app.middleware_stack = None
    assert ask(app, "PUT", PATHS[3])[0] == 404


def test_the_connectors_app_installs_the_gate() -> None:
    import app.connectors_main as main

    assert any(m.cls is gate.KbPushFlagMiddleware for m in main.app.user_middleware)
    paths = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", None) or ()))) for r in main.app.routes}
    assert ("/api/v1/kb/{kb_id}/records", ("POST",)) in paths and ("/api/v1/kb/{kb_id}/schema", ("PUT",)) in paths


async def test_the_flag_defaults_to_off() -> None:
    from app.services.featureflag.config.config import CONFIG
    from app.services.featureflag.platform_settings import is_kb_push_enabled

    assert CONFIG.ENABLE_KB_PUSH == "ENABLE_KB_PUSH"
    assert await is_kb_push_enabled(None) is False
    config = MagicMock()
    config.get_config = AsyncMock(return_value=None)
    assert await is_kb_push_enabled(config) is False


def test_a_flag_reader_that_raises_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware's own fail-closed branch (read_platform_feature_flag already swallows store errors)."""
    async def boom(_config: object) -> bool:
        raise RuntimeError("flag reader broke")

    monkeypatch.setattr(gate, "is_kb_push_enabled", boom)
    app, graph = build_app(with_push=True, flag=True, user="limited")
    assert ask(app, "POST", PATHS[0])[0] == 405 and ask(app, "PUT", PATHS[3])[0] == 404 and graph.calls == []


def test_a_flag_reader_that_answers_something_other_than_true_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    async def maybe(_config: object) -> object:
        return "false"  # a stored string is not a yes

    monkeypatch.setattr(gate, "is_kb_push_enabled", maybe)
    app, _ = build_app(with_push=True, flag=True, user="plain")
    assert ask(app, "PUT", PATHS[3])[0] == 404
