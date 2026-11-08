"""GET /api/v1/toolsets/configured driven through a real FastAPI app.

The route used to call the `/my-toolsets` route function directly, so the
`Query(...)` defaults of the parameters it did not pass reached the list builder
as `Query` objects (`page - 1` raised TypeError -> 500). These tests go through
FastAPI's request handling, the only path that resolves those defaults.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from dependency_injector import providers
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes import toolsets as routes
from app.config.constants.service import OAuthScopes
from app.containers.connector import ConnectorAppContainer
from tests.support.in_memory_config import InMemoryConfigService

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

ORG, USER = "org-1", "user-1"
PREFIX = "/api/v1/toolsets"


class _Registry:
    """The two registry calls the list builder makes."""

    def get_toolset_metadata(self, toolset_type: str) -> dict[str, Any]:
        return {"display_name": toolset_type.title(), "description": "", "icon_path": "",
                "category": "app", "supported_auth_types": ["API_TOKEN"], "tools": []}

    def list_toolsets(self) -> list[str]:
        return ["jira", "slack"]


def _instance(instance_id: str, toolset_type: str) -> dict[str, Any]:
    return {"_id": instance_id, "orgId": ORG, "toolsetType": toolset_type,
            "instanceName": f"{toolset_type} {instance_id}", "authType": "API_TOKEN"}


@pytest.fixture
def config() -> InMemoryConfigService:
    return InMemoryConfigService()


@pytest.fixture
def client(config: InMemoryConfigService) -> Iterator[TestClient]:
    container = ConnectorAppContainer()
    container.config_service.override(providers.Object(config))
    container.wire(modules=[routes])
    app = FastAPI()
    app.state.toolset_registry = _Registry()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        if "x-anonymous" not in request.headers:
            request.state.user = {"userId": USER, "orgId": ORG}
            if "x-oauth-scope" in request.headers:
                request.state.user |= {"isOAuth": True, "oauthScopes": [request.headers["x-oauth-scope"]]}
        return await call_next(request)

    app.include_router(routes.router)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        container.unwire()


def _seed(config: InMemoryConfigService, count: int, *, authenticated: Collection[str] = ()) -> None:
    instances = [_instance(f"i{n}", "jira" if n % 2 else "slack") for n in range(count)]
    config.values[routes._get_instances_path(ORG)] = instances
    for instance_id in authenticated:
        config.values[routes._get_user_auth_path(instance_id, USER)] = {"isAuthenticated": True}


def test_configured_returns_200_with_the_users_toolsets(client: TestClient, config: InMemoryConfigService) -> None:
    _seed(config, 2, authenticated={"i1"})
    resp = client.get(f"{PREFIX}/configured")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert {t["instanceId"]: t["isAuthenticated"] for t in body["toolsets"]} == {"i0": False, "i1": True}


def test_configured_matches_my_toolsets_without_query_params(client: TestClient, config: InMemoryConfigService) -> None:
    _seed(config, 3, authenticated={"i2"})
    configured = client.get(f"{PREFIX}/configured")
    mine = client.get(f"{PREFIX}/my-toolsets")
    assert configured.status_code == mine.status_code == 200, configured.text
    assert configured.json() == mine.json()


def test_configured_with_no_instances_is_an_empty_success(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/configured")
    assert resp.status_code == 200, resp.text
    assert resp.json()["toolsets"] == []
    assert resp.json()["pagination"]["total"] == 0


def test_configured_honours_page_and_limit(client: TestClient, config: InMemoryConfigService) -> None:
    _seed(config, 3)
    resp = client.get(f"{PREFIX}/configured", params={"page": 2, "limit": 1})
    assert resp.status_code == 200, resp.text
    assert [t["instanceId"] for t in resp.json()["toolsets"]] == ["i1"]
    assert resp.json()["pagination"] == {"page": 2, "limit": 1, "total": 3, "totalPages": 3, "hasNext": True, "hasPrev": True}


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"page": 0}])
def test_configured_rejects_out_of_range_paging(client: TestClient, params: dict[str, int]) -> None:
    assert client.get(f"{PREFIX}/configured", params=params).status_code == 422


def test_configured_without_a_user_is_401(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/configured", headers={"x-anonymous": "1"})
    assert resp.status_code == 401


@pytest.mark.parametrize(("scope", "status"), [("toolset:write", 403), (OAuthScopes.CONNECTOR_READ, 200)])
def test_configured_requires_connector_read_on_oauth_tokens(client: TestClient, scope: str, status: int) -> None:
    resp = client.get(f"{PREFIX}/configured", headers={"x-oauth-scope": scope})
    assert resp.status_code == status, resp.text
