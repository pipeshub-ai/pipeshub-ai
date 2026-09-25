"""The status and preference endpoints behind each person's demo data switch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.modules.demo_data import access
from app.modules.demo_data.access import preference_key
from app.modules.demo_data.router import demo_data_router

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    access.clear_caches()


def _client(store: dict[str, Any], indexed: bool = False) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _signed_in(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # The real auth middleware sets this.
        request.state.user = {"orgId": "org", "userId": "u1"}
        return await call_next(request)

    graph = MagicMock()
    graph.get_org_apps = AsyncMock(return_value=[{"_key": "demo-1", "type": "Demo"}, {"_key": "jira-1", "type": "JIRA"}])
    graph.get_records_by_status = AsyncMock(return_value=["r"] if indexed else [])
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, use_cache=True: store.get(key))
    config.set_config = AsyncMock(side_effect=lambda key, value: store.__setitem__(key, value) or True)
    config.delete_config = AsyncMock(side_effect=lambda key: store.pop(key, None) is not None)
    app.state.graph_provider = graph
    app.container = MagicMock(config_service=MagicMock(return_value=config))
    app.include_router(demo_data_router)
    app.dependency_overrides = {}
    for route in app.routes:  # scopes are checked by the real middleware; not under test here
        for dep in getattr(route, "dependencies", []):
            app.dependency_overrides[dep.dependency] = lambda: None
    return TestClient(app)


def test_status_reports_the_default_and_the_demo() -> None:
    body = _client({}).get("/api/v1/demo-data/status").json()
    assert body == {"hasDemo": True, "include": True, "chosen": None, "realData": False, "demoConnectorIds": ["demo-1"]}


def test_once_real_data_is_in_the_default_is_off() -> None:
    body = _client({}, indexed=True).get("/api/v1/demo-data/status").json()
    assert body["include"] is False and body["realData"] is True


def test_choosing_and_going_back_to_the_default() -> None:
    store: dict[str, Any] = {}
    client = _client(store, indexed=True)

    on = client.put("/api/v1/demo-data/preference", json={"include": True}).json()
    assert on["include"] is True and on["chosen"] is True
    assert store[preference_key("org", "u1")] == {"include": True}

    default = client.put("/api/v1/demo-data/preference", json={"include": None}).json()
    assert default["chosen"] is None and default["include"] is False


def test_unknown_fields_are_refused() -> None:
    response = _client({}).put("/api/v1/demo-data/preference", json={"include": True, "everyone": True})
    assert response.status_code == 422
