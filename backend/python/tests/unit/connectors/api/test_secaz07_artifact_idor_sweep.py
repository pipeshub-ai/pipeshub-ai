"""SEC-AZ-07 (C6.23): an IDOR sweep over every artifact route that names an
artifact. The routes come from the router itself, so a new one is swept
without a change here. Another org's member, a colleague with no grant, and
anyone guessing a neighbouring id all get 404, the same answer an id that
never existed gets; the owner reaches every read (the positive control), and
a reader is told no more than "only the owner may" on an owner's action."""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.artifacts_router import (
    artifacts_router,
    get_blob_store,
    get_graph_provider,
    get_sharing_service,
)
from app.services.artifact_registry.sharing import ArtifactSharingService

ORG_A = "org-a"
ORG_B = "org-b"
ARTIFACT = "art-0001"
# Sequential and look-alike guesses: the next id, the previous one, a prefix and a case change.
GUESSES = ["art-0002", "art-0000", "art-000", "ART-0001"]

USERS = {
    "u-owner": {"_key": "k-owner", "userId": "u-owner", "orgId": ORG_A},
    "u-reader": {"_key": "k-reader", "userId": "u-reader", "orgId": ORG_A},
    "u-colleague": {"_key": "k-colleague", "userId": "u-colleague", "orgId": ORG_A},
    "u-other-org": {"_key": "k-other-org", "userId": "u-other-org", "orgId": ORG_B},
}
EDGES = {("k-owner", ARTIFACT): "OWNER", ("k-reader", ARTIFACT): "READER"}
RECORD = {"_key": ARTIFACT, "id": ARTIFACT, "orgId": ORG_A, "recordName": "q3-plan.html", "version": 1}
ARTIFACT_DOC = {"_key": ARTIFACT, "name": "q3-plan.html", "artifactType": "DOCUMENT", "visibility": "VISIBLE"}


class FakeGraph:
    """The artifact store as the access policy, gallery and sharing read it."""

    async def get_user_by_user_id(self, user_id: str) -> dict | None:
        return USERS.get(user_id)

    async def get_document(self, key: str, collection: str, raise_on_error: bool = False) -> dict | None:
        if collection == CollectionNames.RECORDS.value:
            return RECORD if key == ARTIFACT else None
        if collection == CollectionNames.ARTIFACTS.value:
            return ARTIFACT_DOC if key == ARTIFACT else None
        if collection == CollectionNames.USERS.value:
            return next((u for u in USERS.values() if u["_key"] == key), None)
        return None

    async def get_edge(self, *, from_id: str, to_id: str, **_: str) -> dict | None:
        role = EDGES.get((from_id, to_id))
        return {"role": role} if role else None

    async def get_edges_to_node(self, node: str, collection: str, raise_on_error: bool = False) -> list[dict]:
        return [
            {"_from": f"users/{user_key}", "from_id": user_key, "role": role}
            for (user_key, artifact), role in EDGES.items()
            if node.endswith(f"/{artifact}")
        ]

    async def get_artifact_detail(self, user_key: str, org_id: str, artifact_id: str) -> dict | None:
        role = EDGES.get((user_key, artifact_id))
        if role is None or org_id != RECORD["orgId"]:
            return None
        return {**RECORD, "artifactDoc": ARTIFACT_DOC, "permission": {"role": role}}


def _client(user_id: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = {"userId": user_id, "orgId": USERS[user_id]["orgId"]}
        return await call_next(request)

    graph = FakeGraph()
    app.include_router(artifacts_router)
    app.dependency_overrides[get_graph_provider] = lambda: graph
    app.dependency_overrides[get_sharing_service] = lambda: ArtifactSharingService(graph, None)
    app.dependency_overrides[get_blob_store] = lambda: AsyncMock()
    return TestClient(app, raise_server_exceptions=False)


def _artifact_routes() -> list[tuple[str, str]]:
    routes = [
        (method, route.path)
        for route in artifacts_router.routes
        if isinstance(route, APIRoute) and "{artifact_id}" in route.path
        for method in sorted(route.methods)
    ]
    assert len(routes) >= 5, routes
    return routes


ROUTES = _artifact_routes()
# A body every route that takes one validates, so the refusal is the route's own.
BODY = {"userIds": ["u-reader"]}


def _send(client: TestClient, method: str, path: str, artifact_id: str) -> Response:
    url = path.replace("{artifact_id}", artifact_id)
    return client.request(method, url, json=BODY if method in {"POST", "DELETE"} else None)


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("caller", ["u-other-org", "u-colleague"])
def test_sec_az_07_a_caller_without_a_grant_gets_not_found(method: str, path: str, caller: str) -> None:
    response = _send(_client(caller), method, path, ARTIFACT)
    assert response.status_code == 404, (method, path, caller, response.text)
    assert "q3-plan" not in response.text


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("guess", GUESSES)
@pytest.mark.parametrize("caller", ["u-owner", "u-other-org"])
def test_sec_az_07_a_guessed_neighbouring_id_is_not_found(method: str, path: str, guess: str, caller: str) -> None:
    response = _send(_client(caller), method, path, guess)
    assert response.status_code == 404, (method, path, guess, caller, response.text)


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[0] == "GET"])
def test_sec_az_07_the_owner_reaches_every_read(method: str, path: str) -> None:
    response = _send(_client("u-owner"), method, path, ARTIFACT)
    assert response.status_code == 200, (method, path, response.text)


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_sec_az_07_a_reader_may_read_and_is_refused_only_the_owners_actions(method: str, path: str) -> None:
    response = _send(_client("u-reader"), method, path, ARTIFACT)
    if method == "GET" and not path.endswith("/permissions"):
        assert response.status_code == 200, (method, path, response.text)
    else:
        assert response.status_code == 403, (method, path, response.text)
