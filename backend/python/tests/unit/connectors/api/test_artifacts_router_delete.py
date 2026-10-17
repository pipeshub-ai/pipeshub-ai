"""DELETE /api/v1/artifacts/{id} and DELETE /api/v1/artifacts/conversations/{id}:
status codes, scopes, and that nothing is deleted on a refused request."""

from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.artifacts_router import (
    artifacts_router,
    get_blob_store,
    get_graph_provider,
)
from app.models.entities import ArtifactType, ArtifactVisibility
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

OWNER_SESSION = {"userId": "user-1", "orgId": "org-1"}
READER_SESSION = {"userId": "user-2", "orgId": "org-1"}
RECORDS = CollectionNames.RECORDS.value


class _GalleryGraph(FakeGraphProvider):
    """The fake plus the gallery's detail query, answered from the fake's own
    nodes and edges the way both real providers do."""

    async def get_artifact_detail(self, user_key: str, org_id: str, artifact_id: str) -> dict | None:
        record = self.nodes[RECORDS].get(artifact_id)
        edge = await self.get_edge(
            from_id=user_key, from_collection=CollectionNames.USERS.value, to_id=artifact_id,
            to_collection=RECORDS, collection=CollectionNames.PERMISSION.value,
        )
        if not record or record.get("orgId") != org_id or record.get("isDeleted") or not edge:
            return None
        return {**record, "artifactDoc": self.nodes[CollectionNames.ARTIFACTS.value].get(artifact_id) or {},
                "permission": {"role": edge.get("role"), "type": "USER"}}


@pytest.fixture
def graph() -> _GalleryGraph:
    g = _GalleryGraph()
    g.add_user("user-1", key="ukey-1")
    g.add_user("user-2", key="ukey-2")
    return g


@pytest.fixture
def blob() -> FakeBlobStore:
    return FakeBlobStore()


@pytest.fixture
def session() -> dict:
    return {"user": dict(OWNER_SESSION)}


@pytest.fixture
def client(graph, blob, session) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _session_user(request: Request, call_next):  # noqa: ANN202
        request.state.user = session["user"]
        return await call_next(request)

    app.include_router(artifacts_router)
    app.dependency_overrides[get_graph_provider] = lambda: graph
    app.dependency_overrides[get_blob_store] = lambda: blob
    return TestClient(app, raise_server_exceptions=False)


async def _create(graph, blob, name="chart.png", **kwargs) -> str:
    service = ArtifactRegistryService(graph, blob)
    metadata = await service.register(
        actor=Actor(org_id="org-1", user_id="user-1"), name=name,
        artifact_type=kwargs.pop("artifact_type", ArtifactType.IMAGE), mime_type="image/png",
        content=b"bytes", conversation_id=kwargs.pop("conversation_id", "conv-1"), **kwargs,
    )
    return metadata.artifact_id


def _share(graph, artifact_id) -> None:
    graph.edges[CollectionNames.PERMISSION.value].append({
        "from_id": "ukey-2", "from_collection": CollectionNames.USERS.value,
        "to_id": artifact_id, "to_collection": RECORDS, "type": "USER", "role": "READER",
    })


class TestDeleteOne:
    async def test_owner_gets_204_and_the_artifact_is_gone(self, client, graph, blob) -> None:
        artifact_id = await _create(graph, blob)

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 204
        assert response.content == b""
        assert artifact_id not in graph.nodes[RECORDS]
        assert len(blob.purge_calls) == 1

    async def test_reader_gets_403_and_nothing_is_deleted(self, client, graph, blob, session) -> None:
        artifact_id = await _create(graph, blob)
        _share(graph, artifact_id)
        session["user"] = dict(READER_SESSION)

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 403
        assert artifact_id in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_user_without_access_gets_404(self, client, graph, blob, session) -> None:
        artifact_id = await _create(graph, blob)
        session["user"] = dict(READER_SESSION)

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 404
        assert blob.purge_calls == []

    async def test_another_org_gets_404(self, client, graph, blob, session) -> None:
        artifact_id = await _create(graph, blob)
        session["user"] = {"userId": "user-1", "orgId": "org-2"}

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 404
        assert artifact_id in graph.nodes[RECORDS]

    def test_unknown_artifact_gets_404(self, client) -> None:
        assert client.delete("/api/v1/artifacts/no-such-id").status_code == 404

    @pytest.mark.parametrize("kwargs", [
        {"artifact_type": ArtifactType.TOOL_RESULT},
        {"visibility": ArtifactVisibility.STAGING},
        {"is_temporary": True},
    ])
    async def test_hidden_artifacts_cannot_be_deleted_by_id(self, client, graph, blob, kwargs) -> None:
        artifact_id = await _create(graph, blob, **kwargs)

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 404
        assert artifact_id in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_storage_failure_is_503_and_the_graph_is_untouched(self, client, graph, blob) -> None:
        artifact_id = await _create(graph, blob)
        blob.purge_error = aiohttp.ClientError("storage 503")

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 503
        assert artifact_id in graph.nodes[RECORDS]

    async def test_graph_failure_is_500_without_internals(self, client, graph, blob) -> None:
        artifact_id = await _create(graph, blob)
        graph.failing.add("delete_nodes")

        response = client.delete(f"/api/v1/artifacts/{artifact_id}")

        assert response.status_code == 500
        assert "delete_nodes" not in response.text

    async def test_second_delete_is_404(self, client, graph, blob) -> None:
        artifact_id = await _create(graph, blob)
        assert client.delete(f"/api/v1/artifacts/{artifact_id}").status_code == 204
        assert client.delete(f"/api/v1/artifacts/{artifact_id}").status_code == 404


class TestDeleteForConversation:
    async def test_owner_deletes_every_artifact_of_the_chat(self, client, graph, blob) -> None:
        visible = await _create(graph, blob, "a.png")
        staging = await _create(graph, blob, "b.py", visibility=ArtifactVisibility.STAGING)
        other_chat = await _create(graph, blob, "c.png", conversation_id="conv-2")

        response = client.delete("/api/v1/artifacts/conversations/conv-1")

        assert response.status_code == 200
        assert response.json() == {"deleted": 2, "kept": 0}
        assert visible not in graph.nodes[RECORDS] and staging not in graph.nodes[RECORDS]
        assert other_chat in graph.nodes[RECORDS]

    async def test_files_are_kept_when_someone_else_still_owns_an_artifact_of_the_chat(self, client, graph, blob) -> None:
        theirs = await _create(graph, blob, "theirs.png")
        graph.edges[CollectionNames.PERMISSION.value] = [
            e for e in graph.edges[CollectionNames.PERMISSION.value] if e["to_id"] != theirs
        ]
        _share(graph, theirs)

        response = client.delete("/api/v1/artifacts/conversations/conv-1")

        assert response.json() == {"deleted": 0, "kept": 1}
        assert theirs in graph.nodes[RECORDS]

    async def test_storage_failure_is_503(self, client, graph, blob) -> None:
        await _create(graph, blob)
        blob.purge_error = aiohttp.ClientError("storage 503")

        assert client.delete("/api/v1/artifacts/conversations/conv-1").status_code == 503

    def test_empty_chat_is_200_with_zero(self, client, blob) -> None:
        response = client.delete("/api/v1/artifacts/conversations/conv-empty")
        assert response.status_code == 200
        assert response.json() == {"deleted": 0, "kept": 0}

    def test_a_caller_with_no_user_record_gets_404_and_nothing_is_deleted(self, client, session) -> None:
        session["user"] = {"userId": "ghost", "orgId": "org-1"}

        assert client.delete("/api/v1/artifacts/conversations/conv-1").status_code == 404


class TestTokens:
    @pytest.mark.parametrize("path", ["/api/v1/artifacts/a-1", "/api/v1/artifacts/conversations/conv-1"])
    def test_oauth_token_without_conversation_write_is_403(self, client, session, path) -> None:
        session["user"] = {**OWNER_SESSION, "isOAuth": True, "oauthScopes": ["kb:delete", "connector:delete"]}
        assert client.delete(path).status_code == 403

    def test_agent_tokens_can_clean_up_an_agent_chat(self, client, session) -> None:
        """Agent-chat delete takes agent:write, so its cleanup must too."""
        session["user"] = {**OWNER_SESSION, "isOAuth": True, "oauthScopes": ["agent:write"]}
        assert client.delete("/api/v1/artifacts/conversations/conv-1").status_code == 200

    async def test_oauth_token_with_conversation_write_is_allowed(self, client, graph, blob, session) -> None:
        artifact_id = await _create(graph, blob)
        session["user"] = {**OWNER_SESSION, "isOAuth": True, "oauthScopes": ["conversation:write"]}
        assert client.delete(f"/api/v1/artifacts/{artifact_id}").status_code == 204

    @pytest.mark.parametrize("path", ["/api/v1/artifacts/a-1", "/api/v1/artifacts/conversations/conv-1"])
    def test_service_tokens_are_refused(self, client, session, path) -> None:
        session["user"] = {**OWNER_SESSION, "token_type": "scoped", "scopes": ["conversation:write"]}
        assert client.delete(path).status_code == 403

    def test_missing_identity_is_401(self, client, session) -> None:
        session["user"] = {}
        assert client.delete("/api/v1/artifacts/a-1").status_code == 401


def test_gallery_rows_carry_the_callers_role(client, graph) -> None:
    graph.list_accessible_artifacts = AsyncMock(return_value=([
        {"id": "a-1", "version": 1, "artifactDoc": {"artifactType": "IMAGE"}, "permission": {"role": "OWNER"}},
        {"id": "a-2", "version": 1, "artifactDoc": {"artifactType": "IMAGE"}, "permission": {"role": "READER"}},
    ], {"mine": 1, "shared": 1}))

    items = client.get("/api/v1/artifacts").json()["items"]

    assert [item["role"] for item in items] == ["OWNER", "READER"]
