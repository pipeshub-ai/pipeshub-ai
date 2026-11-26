"""P2.13 RT-1..RT-3: the query service's publication routes.

The real routes and ``KnowledgePublications``'s authority rules over the in-memory directory; what the graph says
about containers and ownership is a table. The identity is the authenticated request's (and Node's live role for
admins), so tests also send ids in the body to prove they are refused.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes import knowledge_publications as routes
from app.harness.knowledge.service import Caller, Container, KnowledgePublications
from harness_core.knowledge.memory import InMemoryPublicationDirectory

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.types import ASGIApp, Receive, Scope, Send

USERS: dict[str, dict[str, Any]] = {
    "owner": {"userId": "u-owner", "orgId": "org-1"},
    "admin": {"userId": "u-admin", "orgId": "org-1"},
    "member": {"userId": "u-member", "orgId": "org-1"},
    "stranger": {"userId": "u-x", "orgId": "org-2"},
}
CONTAINERS = {("org-1", "apps/kb-hr"), ("org-1", "recordGroups/rg-1")}
OWNERS = {("u-owner", "apps/kb-hr"), ("u-owner", "recordGroups/rg-1")}
NOW = 1_000_000


class _Publications(KnowledgePublications):
    enabled_flag = True

    def __init__(self, directory: InMemoryPublicationDirectory) -> None:
        ids = iter(f"pub-{i}" for i in range(100))
        super().__init__(graph=None, config_service=None, directory=directory, now_ms=lambda: NOW, new_id=lambda: next(ids))  # type: ignore[arg-type]

    async def enabled(self) -> bool:
        return self.enabled_flag

    async def container(self, org_id: str, qualified: str) -> Container | None:
        if (org_id, qualified) not in CONTAINERS:
            return None
        collection, key = qualified.split("/", 1)
        return Container(qualified, collection, key, key)

    async def _is_owner(self, caller: Caller, container: Container) -> bool:
        return (caller.user_id, container.id) in OWNERS


class _Auth:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            name = dict(scope["headers"]).get(b"x-test-user")
            if name is not None:
                scope.setdefault("state", {})["user"] = dict(USERS[name.decode()])
        await self.app(scope, receive, send)


@pytest.fixture
def directory() -> InMemoryPublicationDirectory:
    return InMemoryPublicationDirectory()


@pytest.fixture
def client(directory: InMemoryPublicationDirectory) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")

    async def graph_provider() -> None:
        return None

    app.container = SimpleNamespace(graph_provider=graph_provider, config_service=lambda: None)  # type: ignore[attr-defined]
    app.add_middleware(_Auth)

    async def role(request: Any, _config: Any) -> CallerRole:  # noqa: ANN401
        admin = request.state.user.get("userId") == "u-admin"
        return CallerRole(CallerRoleStatus.VALID, "admin" if admin else "member")

    with (
        patch.object(routes, "KnowledgePublications", lambda *_: _Publications(directory)),
        patch.object(routes, "fetch_caller_role", AsyncMock(side_effect=role)),
    ):
        yield TestClient(app, raise_server_exceptions=False)
    _Publications.enabled_flag = True


BODY = {"containerKind": "kb", "containerId": "kb-hr", "audience": {"userIds": ["u-alice"], "teamIds": ["t-hr"]}, "mode": "answer_only"}


def _post(client: TestClient, who: str, body: dict[str, Any]) -> Any:  # noqa: ANN401
    return client.post("/api/v1/knowledge/publications", json=body, headers={"x-test-user": who})


def test_rt1_an_owner_and_an_admin_publish(client: TestClient, directory: InMemoryPublicationDirectory) -> None:
    created = _post(client, "owner", {**BODY, "expiresAt": NOW + 60_000})
    assert created.status_code == 201, created.text
    assert created.json() == {
        "id": "pub-0", "containerKind": "kb", "containerId": "kb-hr",
        "audience": {"userIds": ["u-alice"], "teamIds": ["t-hr"], "org": False},
        "mode": "answer_only", "createdBy": "u-owner", "createdAt": NOW, "expiresAt": NOW + 60_000,
        "revokedAt": None, "revokedBy": None,
    }
    admin = _post(client, "admin", {**BODY, "containerKind": "recordGroup", "containerId": "rg-1", "audience": {"org": True}, "mode": "full"})
    assert admin.status_code == 201, admin.text
    assert admin.json()["createdBy"] == "u-admin"


def test_rt1_anyone_else_is_refused(client: TestClient, directory: InMemoryPublicationDirectory) -> None:
    refused = _post(client, "member", BODY)
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "publication_forbidden"
    # Another org's container reads as absent, whatever the caller's role.
    assert _post(client, "stranger", BODY).json()["detail"]["code"] == "publication_not_found"


@pytest.mark.parametrize("smuggled", [{"orgId": "org-2"}, {"createdBy": "u-admin"}])
def test_rt1_the_body_never_names_whose_publication_it_is(client: TestClient, smuggled: dict[str, str]) -> None:
    assert _post(client, "owner", {**BODY, **smuggled}).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [{"mode": "everything"}, {"audience": {}}, {"expiresAt": NOW - 1}, {"containerKind": "drive"}],
)
def test_rt1_an_invalid_publication_is_refused(client: TestClient, bad: dict[str, Any]) -> None:
    response = _post(client, "owner", {**BODY, **bad})
    assert response.status_code == 422, response.text


def test_rt2_list_and_revoke(client: TestClient, directory: InMemoryPublicationDirectory) -> None:
    assert _post(client, "owner", BODY).status_code == 201
    listed = client.get("/api/v1/knowledge/publications", params={"containerKind": "kb", "containerId": "kb-hr"}, headers={"x-test-user": "owner"})
    assert [p["id"] for p in listed.json()["publications"]] == ["pub-0"]
    forbidden = client.get("/api/v1/knowledge/publications", params={"containerKind": "kb", "containerId": "kb-hr"}, headers={"x-test-user": "member"})
    assert forbidden.status_code == 403
    assert client.delete("/api/v1/knowledge/publications/pub-0", headers={"x-test-user": "member"}).status_code == 403
    revoked = client.delete("/api/v1/knowledge/publications/pub-0", headers={"x-test-user": "owner"})
    assert revoked.status_code == 200
    assert (revoked.json()["revokedAt"], revoked.json()["revokedBy"]) == (NOW, "u-owner")
    kept = client.get("/api/v1/knowledge/publications", params={"containerKind": "kb", "containerId": "kb-hr"}, headers={"x-test-user": "admin"})
    assert kept.json()["publications"][0]["revokedAt"] == NOW
    assert client.delete("/api/v1/knowledge/publications/pub-9", headers={"x-test-user": "owner"}).status_code == 404


def test_rt3_flag_off_every_route_is_404(client: TestClient, directory: InMemoryPublicationDirectory) -> None:
    _Publications.enabled_flag = False
    responses = [
        _post(client, "owner", BODY),
        client.get("/api/v1/knowledge/publications", params={"containerKind": "kb", "containerId": "kb-hr"}, headers={"x-test-user": "owner"}),
        client.delete("/api/v1/knowledge/publications/pub-0", headers={"x-test-user": "owner"}),
    ]
    assert [(r.status_code, r.json()["detail"]["code"]) for r in responses] == [(404, "publications_disabled")] * 3


def test_rt_unauthenticated_is_401(client: TestClient) -> None:
    assert client.post("/api/v1/knowledge/publications", json=BODY).status_code in (401, 403)
