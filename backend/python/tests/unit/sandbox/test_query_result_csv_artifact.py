"""A SQL query's CSV export is a real artifact record, end to end.

Runs `save_query_result_csv` against the real `ArtifactRegistryService`, the
real conversation-share permission helpers and the real record authorizer
(over in-memory graph/blob fakes), and checks what the user actually depends
on: only the owner can read the CSV, sharing the conversation lets the
people it is shared with read it, and the artifact tools can list and
update it like any other artifact.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes.chatbot import (
    _get_artifact_record_ids_for_conversation,
    _grant_reader_permissions,
    _revoke_reader_permissions,
)
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import RecordType
from app.sandbox.artifact_upload import save_query_result_csv
from app.services.artifact_registry import Actor, ArtifactRegistryService
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.versioning import compute_content_hash
from app.services.record_content import RecordAccessDeniedError, TieredRecordAuthorizer

from ..services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OWNER = "user-owner"
COLLEAGUE = "user-colleague"
STRANGER = "user-stranger"
CONVERSATION = "conv-1"

COLUMNS = ["id", "salary"]
ROWS = [(1, 100), (2, 200)]
CSV = b"id,salary\r\n1,100\r\n2,200\r\n"


class _Graph(FakeGraphProvider):
    """The fake plus the full ACL query, which in production also covers KB/group
    paths; here only direct user permission edges exist, which is what artifacts use."""

    async def check_record_access_with_details(self, user_id: str, org_id: str, record_id: str) -> dict | None:
        user = await self.get_user_by_user_id(user_id)
        if not user:
            return None
        edge = await self.get_edge(
            from_id=user["_key"], from_collection=CollectionNames.USERS.value,
            to_id=record_id, to_collection=CollectionNames.RECORDS.value,
            collection=CollectionNames.PERMISSION.value,
        )
        return {"role": edge["role"]} if edge else None

    async def batch_delete_edges(self, edges: list[dict], collection: str) -> bool:
        doomed = {(e["from_id"], e["to_id"]) for e in edges}
        self.edges[collection] = [e for e in self.edges[collection] if (e["from_id"], e["to_id"]) not in doomed]
        return True


def _setup(*, signs_urls: bool = False) -> tuple[_Graph, FakeBlobStore, ArtifactRegistryService]:
    graph = _Graph()
    for user_id, key in ((OWNER, "ukey-owner"), (COLLEAGUE, "ukey-colleague"), (STRANGER, "ukey-stranger")):
        graph.add_user(user_id, key=key)
    blob = FakeBlobStore(signs_urls=signs_urls)
    return graph, blob, ArtifactRegistryService(graph, blob)


async def _export(graph: _Graph, blob: FakeBlobStore, *, user_id: str | None = OWNER) -> dict[str, Any]:
    result = await save_query_result_csv(
        blob_store=blob, graph_provider=graph, org_id=ORG, user_id=user_id,
        conversation_id=CONVERSATION, columns=COLUMNS, rows=ROWS,
        file_name="query_result_1.csv", source_tool="sql.execute_sql_query",
    )
    assert result is not None and result["type"] == "artifacts"
    (entry,) = result["artifacts"]
    return entry


def _permission_edges(graph: _Graph, record_id: str) -> dict[str, str]:
    return {
        e["from_id"]: e["role"]
        for e in graph.edges[CollectionNames.PERMISSION.value]
        if e["to_id"] == record_id
    }


class TestTheRecord:
    async def test_is_an_artifact_record_owned_by_the_requester(self) -> None:
        graph, blob, _ = _setup()
        entry = await _export(graph, blob)
        record_id = entry["recordId"]

        record = graph.nodes[CollectionNames.RECORDS.value][record_id]
        assert record["recordType"] == RecordType.ARTIFACT.value
        assert record["origin"] == OriginTypes.UPLOAD.value
        assert record["orgId"] == ORG
        assert record["externalRecordId"] == entry["documentId"]
        assert record["connectorName"] == Connectors.DATABASE_SANDBOX.value

        artifact = graph.nodes[CollectionNames.ARTIFACTS.value][record_id]
        assert (artifact["orgId"], artifact["conversationId"]) == (ORG, CONVERSATION)
        assert artifact["sourceTool"] == "sql.execute_sql_query"
        assert artifact["contentHash"] == compute_content_hash(CSV)

        assert _permission_edges(graph, record_id) == {"ukey-owner": "OWNER"}

    async def test_bytes_are_stored_versioned_under_the_conversation(self) -> None:
        graph, blob, _ = _setup()
        entry = await _export(graph, blob)

        stored = blob.documents[entry["documentId"]]
        assert stored["content"] == CSV
        assert (stored["content_type"], stored["file_name"]) == ("text/csv", "query_result_1.csv")
        assert entry["version"] == 1

    async def test_no_record_without_a_requesting_user(self) -> None:
        graph, blob, _ = _setup()
        blob.save_conversation_file_to_storage = _unregistered_upload(blob)
        result = await save_query_result_csv(
            blob_store=blob, graph_provider=graph, org_id=ORG, user_id=None,
            conversation_id=CONVERSATION, columns=COLUMNS, rows=ROWS,
            file_name="query_result_1.csv", source_tool="sql.execute_sql_query",
        )

        # Local storage, no owner: no record to stream and no signed URL, so no export.
        assert result is None
        assert graph.nodes[CollectionNames.RECORDS.value] == {}
        assert graph.edges[CollectionNames.PERMISSION.value] == []


def _unregistered_upload(blob: FakeBlobStore):
    async def save(*, org_id: str, conversation_id: str, file_name: str, file_bytes: bytes) -> dict[str, Any]:
        document_id = blob._new_document_id()
        blob.documents[document_id] = {"org_id": org_id, "file_name": file_name, "content": file_bytes}
        return {"documentId": document_id, "fileName": file_name}
    return save


class TestWhoCanRead:
    async def test_owner_reads_through_the_permission_checked_stream(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)

        url = await registry.get_download_url(actor=Actor(org_id=ORG, user_id=OWNER), artifact_id=entry["recordId"])

        assert url == f"https://app.example/api/v1/knowledgeBase/stream/record/{entry['recordId']}"

    async def test_owner_on_cloud_storage_gets_a_signed_url(self) -> None:
        graph, blob, registry = _setup(signs_urls=True)
        entry = await _export(graph, blob)

        url = await registry.get_download_url(actor=Actor(org_id=ORG, user_id=OWNER), artifact_id=entry["recordId"])

        assert url == f"https://blob.example/download/{entry['documentId']}"

    async def test_colleague_in_the_same_org_cannot_read_it(self) -> None:
        graph, blob, registry = _setup(signs_urls=True)
        entry = await _export(graph, blob)
        colleague = Actor(org_id=ORG, user_id=COLLEAGUE)

        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=colleague, artifact_id=entry["recordId"])
        with pytest.raises(RecordAccessDeniedError):
            await TieredRecordAuthorizer(graph).authorize(colleague, _record_view(entry))

    async def test_user_in_another_org_cannot_read_it(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)

        # Reported as missing, so another org cannot even confirm it exists.
        with pytest.raises(ArtifactNotFoundError):
            await registry.get_download_url(actor=Actor(org_id="org-2", user_id=OWNER), artifact_id=entry["recordId"])


def _record_view(entry: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=entry["recordId"], org_id=ORG)


class TestSharingTheConversation:
    async def test_share_grants_reader_and_unshare_revokes_it(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)
        record_id = entry["recordId"]
        colleague = Actor(org_id=ORG, user_id=COLLEAGUE)

        shared = await _get_artifact_record_ids_for_conversation(graph, ORG, CONVERSATION)
        assert shared == [record_id]

        assert await _grant_reader_permissions(graph, OWNER, [COLLEAGUE], shared) == 1
        assert _permission_edges(graph, record_id) == {"ukey-owner": "OWNER", "ukey-colleague": "READER"}
        await registry.get_download_url(actor=colleague, artifact_id=record_id)
        await TieredRecordAuthorizer(graph).authorize(colleague, _record_view(entry))

        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=Actor(org_id=ORG, user_id=STRANGER), artifact_id=record_id)

        assert await _revoke_reader_permissions(graph, OWNER, [COLLEAGUE], shared) == 1
        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=colleague, artifact_id=record_id)

    async def test_someone_who_does_not_own_it_cannot_share_it(self) -> None:
        graph, blob, _ = _setup()
        entry = await _export(graph, blob)

        granted = await _grant_reader_permissions(graph, STRANGER, [COLLEAGUE], [entry["recordId"]])

        assert granted == 0
        assert "ukey-colleague" not in _permission_edges(graph, entry["recordId"])


class TestArtifactTools:
    async def test_listed_with_the_conversation_artifacts(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)

        listed = await registry.list_for_conversation(actor=Actor(org_id=ORG, user_id=OWNER), conversation_id=CONVERSATION)

        assert [a.artifact_id for a in listed] == [entry["recordId"]]
        assert listed[0].mime_type == "text/csv"

    async def test_owner_can_add_a_version(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)
        revised = b"id,salary\r\n1,150\r\n"

        version, metadata = await registry.add_version(
            actor=Actor(org_id=ORG, user_id=OWNER), artifact_id=entry["recordId"], content=revised,
        )

        assert (version.version, metadata.version) == (2, 2)
        assert blob.documents[entry["documentId"]]["content"] == revised

    async def test_reader_cannot_add_a_version(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)
        await _grant_reader_permissions(graph, OWNER, [COLLEAGUE], [entry["recordId"]])

        with pytest.raises(AccessDeniedError):
            await registry.add_version(
                actor=Actor(org_id=ORG, user_id=COLLEAGUE), artifact_id=entry["recordId"], content=b"x\r\n",
            )
