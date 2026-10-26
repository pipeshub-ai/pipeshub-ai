"""HTTP contract of `POST /api/v1/artifacts/copy`, which gives a forked chat its
own copy of the artifacts it carried over."""

from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.connectors.api.artifacts_router import artifacts_router, get_graph_provider
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import ArtifactCopy

URL = "/api/v1/artifacts/copy"


def _copy(source: str, new: str, versions: dict[int, int]) -> ArtifactCopy:
    return ArtifactCopy(
        source_artifact_id=source, source_document_id=f"doc-{source}",
        artifact_id=new, document_id=f"doc-{new}", versions=versions,
    )


@pytest.fixture
def registry() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def make_client(registry: AsyncMock) -> Callable[..., TestClient]:
    def _make(*, with_container: bool = True, token_claims: dict | None = None) -> TestClient:
        app = FastAPI()
        user = {"userId": "auth-user", "orgId": "org-1", **(token_claims or {})}

        @app.middleware("http")
        async def _authenticated(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            request.state.user = user
            return await call_next(request)

        app.include_router(artifacts_router)
        app.dependency_overrides[get_graph_provider] = lambda: AsyncMock()
        if with_container:
            app.container = MagicMock()
        return TestClient(app, raise_server_exceptions=False)

    return _make


@pytest.fixture
def patched_registry(registry: AsyncMock) -> Iterator[None]:
    with (
        patch("app.connectors.api.artifacts_router.ArtifactRegistryService", return_value=registry),
        patch("app.connectors.api.artifacts_router.BlobStorage"),
    ):
        yield


@pytest.fixture
def client(make_client: Callable[..., TestClient], patched_registry: None) -> TestClient:
    return make_client()


def test_copies_every_requested_artifact_for_the_caller(client: TestClient, registry: AsyncMock) -> None:
    registry.copy_to_conversation = AsyncMock(
        side_effect=[_copy("a1", "n1", {1: 1, 2: 2}), _copy("a2", "n2", {1: 1})],
    )

    response = client.post(URL, json={
        "conversationId": "fork-1",
        "artifacts": [{"artifactId": "a1", "upToVersion": 2}, {"artifactId": "a2", "upToVersion": 1}],
    })

    assert response.status_code == 200
    assert response.json() == {
        "copies": [
            {
                "sourceArtifactId": "a1", "sourceDocumentId": "doc-a1", "artifactId": "n1",
                "documentId": "doc-n1", "versions": [{"from": 1, "to": 1}, {"from": 2, "to": 2}],
            },
            {
                "sourceArtifactId": "a2", "sourceDocumentId": "doc-a2", "artifactId": "n2",
                "documentId": "doc-n2", "versions": [{"from": 1, "to": 1}],
            },
        ],
        "failed": [],
    }
    first = registry.copy_to_conversation.await_args_list[0].kwargs
    assert (first["actor"].org_id, first["actor"].user_id) == ("org-1", "auth-user")
    assert (first["artifact_id"], first["up_to_version"], first["conversation_id"]) == ("a1", 2, "fork-1")


def test_one_bad_artifact_does_not_fail_the_batch(client: TestClient, registry: AsyncMock) -> None:
    registry.copy_to_conversation = AsyncMock(
        side_effect=[
            ArtifactNotFoundError("gone"),
            AccessDeniedError("not yours"),
            RuntimeError("blob store down"),
            _copy("a4", "n4", {1: 1}),
        ],
    )

    response = client.post(URL, json={
        "conversationId": "fork-1",
        "artifacts": [{"artifactId": f"a{i}", "upToVersion": 1} for i in range(1, 5)],
    })

    assert response.status_code == 200
    body = response.json()
    assert [c["sourceArtifactId"] for c in body["copies"]] == ["a4"]
    assert body["failed"] == [
        {"artifactId": "a1", "reason": "not_found"},
        {"artifactId": "a2", "reason": "forbidden"},
        {"artifactId": "a3", "reason": "error"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"conversationId": "", "artifacts": [{"artifactId": "a1", "upToVersion": 1}]},
        {"conversationId": "fork-1", "artifacts": []},
        {"conversationId": "fork-1", "artifacts": [{"artifactId": "a1", "upToVersion": 0}]},
        {"conversationId": "fork-1", "artifacts": [{"artifactId": "", "upToVersion": 1}]},
        {"conversationId": "fork-1", "artifacts": [{"artifactId": f"a{i}", "upToVersion": 1} for i in range(51)]},
    ],
)
def test_rejects_a_malformed_request(client: TestClient, registry: AsyncMock, payload: dict) -> None:
    assert client.post(URL, json=payload).status_code == 422
    registry.copy_to_conversation.assert_not_awaited()


def test_fails_when_artifact_storage_is_not_wired(make_client) -> None:
    response = make_client(with_container=False).post(
        URL, json={"conversationId": "fork-1", "artifacts": [{"artifactId": "a1", "upToVersion": 1}]},
    )
    assert response.status_code == 500


_ONE_ARTIFACT = {"conversationId": "fork-1", "artifacts": [{"artifactId": "a1", "upToVersion": 1}]}


@pytest.mark.parametrize("scopes", [["conversation:chat"], ["agent:execute"]])
def test_a_client_that_may_fork_may_copy(make_client, patched_registry, registry: AsyncMock, scopes: list[str]) -> None:
    registry.copy_to_conversation = AsyncMock(return_value=_copy("a1", "n1", {1: 1}))
    client = make_client(token_claims={"isOAuth": True, "oauthScopes": scopes})

    assert client.post(URL, json=_ONE_ARTIFACT).status_code == 200


@pytest.mark.parametrize("scopes", [["conversation:read"], ["connector:write", "kb:write"], []])
def test_a_client_that_may_not_fork_may_not_copy(
    make_client, patched_registry, registry: AsyncMock, scopes: list[str],
) -> None:
    client = make_client(token_claims={"isOAuth": True, "oauthScopes": scopes})

    assert client.post(URL, json=_ONE_ARTIFACT).status_code == 403
    registry.copy_to_conversation.assert_not_awaited()
