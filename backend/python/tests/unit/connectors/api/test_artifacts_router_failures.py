"""A graph failure behind the gallery routes must surface as 5xx.

An empty 200 reads as "No artifacts yet" in the UI and a 404 reads as "gone",
so neither may stand in for a store or query error.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.connectors.api.artifacts_router import artifacts_router, get_graph_provider


@pytest.fixture
def graph():
    g = AsyncMock()
    g.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key", "id": "user-key"})
    return g


@pytest.fixture
def client(graph):
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next):
        request.state.user = {"userId": "auth-user", "orgId": "org-1"}
        return await call_next(request)

    app.include_router(artifacts_router)
    app.dependency_overrides[get_graph_provider] = lambda: graph
    return TestClient(app, raise_server_exceptions=False)


def test_a_failed_listing_is_a_server_error_not_an_empty_page(client, graph):
    graph.list_accessible_artifacts = AsyncMock(side_effect=RuntimeError("graph down"))
    response = client.get("/api/v1/artifacts")
    assert response.status_code == 500
    assert "graph down" not in response.text


def test_a_failed_detail_read_is_a_server_error_not_a_404(client, graph):
    graph.get_artifact_detail = AsyncMock(side_effect=RuntimeError("graph down"))
    response = client.get("/api/v1/artifacts/art-1")
    assert response.status_code == 500
    assert "graph down" not in response.text


def test_a_failed_versions_read_is_a_server_error_not_a_404(client, graph):
    graph.get_artifact_detail = AsyncMock(side_effect=RuntimeError("graph down"))
    response = client.get("/api/v1/artifacts/art-1/versions")
    assert response.status_code == 500


def test_an_artifact_the_caller_cannot_see_is_still_a_404(client, graph):
    graph.get_artifact_detail = AsyncMock(return_value=None)
    response = client.get("/api/v1/artifacts/art-1")
    assert response.status_code == 404


def test_an_empty_gallery_is_still_a_200(client, graph):
    graph.list_accessible_artifacts = AsyncMock(return_value=([], 0))
    response = client.get("/api/v1/artifacts")
    assert response.status_code == 200
    assert response.json()["items"] == []
