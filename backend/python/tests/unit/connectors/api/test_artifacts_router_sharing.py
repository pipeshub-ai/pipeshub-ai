"""HTTP contract of the artifact sharing routes and the gallery's tab counts."""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.connectors.api.artifacts_router import (
    artifacts_router,
    get_graph_provider,
    get_sharing_service,
)
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.sharing import InvalidShareTargetError

SHARE_URL = "/api/v1/artifacts/art-1/permissions"


@pytest.fixture
def graph() -> AsyncMock:
    g = AsyncMock()
    g.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key", "id": "user-key"})
    return g


@pytest.fixture
def sharing() -> AsyncMock:
    service = AsyncMock()
    service.list_members = AsyncMock(return_value=[{"userId": "auth-user", "role": "OWNER"}])
    service.share = AsyncMock(return_value=2)
    service.unshare = AsyncMock(return_value=1)
    return service


@pytest.fixture
def client(graph: AsyncMock, sharing: AsyncMock) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = {"userId": "auth-user", "orgId": "org-1"}
        return await call_next(request)

    app.include_router(artifacts_router)
    app.dependency_overrides[get_graph_provider] = lambda: graph
    app.dependency_overrides[get_sharing_service] = lambda: sharing
    return TestClient(app, raise_server_exceptions=False)


def test_the_gallery_reports_the_count_of_every_tab(client: TestClient, graph: AsyncMock) -> None:
    graph.list_accessible_artifacts = AsyncMock(return_value=([], {"mine": 2, "shared": 3}))
    response = client.get("/api/v1/artifacts", params={"ownership": "shared"})
    assert response.status_code == 200
    assert response.json()["counts"] == {"all": 5, "mine": 2, "shared": 3}
    assert response.json()["pagination"]["totalCount"] == 3
    assert graph.list_accessible_artifacts.await_args.kwargs["ownership"] == "shared"


def test_share_passes_the_caller_and_the_users(client: TestClient, sharing: AsyncMock) -> None:
    response = client.post(SHARE_URL, json={"userIds": ["u1", "u2"]})
    assert response.status_code == 200
    assert response.json() == {"shared": 2}
    actor, artifact_id, user_ids = sharing.share.await_args.args
    assert (actor.org_id, actor.user_id, artifact_id, user_ids) == ("org-1", "auth-user", "art-1", ["u1", "u2"])


def test_unshare_takes_a_body_on_delete(client: TestClient, sharing: AsyncMock) -> None:
    response = client.request("DELETE", SHARE_URL, json={"userIds": ["u1"]})
    assert response.status_code == 200
    assert response.json() == {"unshared": 1}
    assert sharing.unshare.await_args.args[2] == ["u1"]


def test_members(client: TestClient) -> None:
    response = client.get(SHARE_URL)
    assert response.status_code == 200
    assert response.json() == {"members": [{"userId": "auth-user", "role": "OWNER"}]}


@pytest.mark.parametrize("body", [{}, {"userIds": []}, {"userIds": [f"u{i}" for i in range(101)]}])
def test_a_malformed_body_is_rejected_before_the_service(
    client: TestClient, sharing: AsyncMock, body: dict
) -> None:
    assert client.post(SHARE_URL, json=body).status_code == 422
    sharing.share.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ArtifactNotFoundError("Artifact not found: art-1"), 404),
        (AccessDeniedError("Not authorized"), 403),
        (InvalidShareTargetError("User not found: u1"), 400),
    ],
)
def test_service_errors_map_to_status_codes(
    client: TestClient, sharing: AsyncMock, error: Exception, status: int
) -> None:
    sharing.share.side_effect = error
    sharing.unshare.side_effect = error
    assert client.post(SHARE_URL, json={"userIds": ["u1"]}).status_code == status
    assert client.request("DELETE", SHARE_URL, json={"userIds": ["u1"]}).status_code == status


@pytest.mark.parametrize(("error", "status"), [(ArtifactNotFoundError("x"), 404), (AccessDeniedError("x"), 403)])
def test_member_errors_map_to_status_codes(
    client: TestClient, sharing: AsyncMock, error: Exception, status: int
) -> None:
    sharing.list_members.side_effect = error
    assert client.get(SHARE_URL).status_code == status


def test_a_graph_failure_while_sharing_is_a_server_error(client: TestClient, sharing: AsyncMock) -> None:
    sharing.share.side_effect = RuntimeError("graph down")
    response = client.post(SHARE_URL, json={"userIds": ["u1"]})
    assert response.status_code == 500
    assert "graph down" not in response.text


@pytest.mark.asyncio
async def test_the_service_notifies_through_the_connector_container() -> None:
    notifications = MagicMock()
    request = MagicMock()
    request.app = SimpleNamespace(container=SimpleNamespace(connector_notification_service=lambda: notifications))
    service = await get_sharing_service(request, AsyncMock())
    assert service._notifications is notifications


@pytest.mark.asyncio
async def test_without_a_container_the_service_shares_without_notifying() -> None:
    request = MagicMock()
    request.app = SimpleNamespace()
    service = await get_sharing_service(request, AsyncMock())
    assert service._notifications is None
