"""A SQL query's CSV export is a real artifact record, end to end.

Runs `save_query_result_csv` against the real `ArtifactRegistryService`, the
real `can_read_record` / record authorizer and a fake Node PDP (over
in-memory graph/blob fakes), and checks what the user actually depends on:
only the owner can read the CSV, the people a conversation is shared with can
read it when Node allows that turn's `runId`, and the artifact tools can list
and update it like any other artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import RecordType
from app.modules.authz.chat_content_access import can_read_record
from app.sandbox.artifact_upload import save_query_result_csv
from app.services.artifact_registry import Actor, ArtifactRegistryService
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.versioning import compute_content_hash
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.record_content import RecordAccessDeniedError, TieredRecordAuthorizer

from ..services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from app.modules.authz.node_pdp_client import ChatContentCheck

ORG = "org-1"
OWNER = "user-owner"
COLLEAGUE = "user-colleague"
STRANGER = "user-stranger"
CONVERSATION = "conv-1"
RUN = "run-1"

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
        record = await self.get_document(record_id, CollectionNames.RECORDS.value)
        if record is not None and not is_live_record(record):
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


def _setup(*, signs_urls: bool = False, pdp: _FakePdp | None = None) -> tuple[_Graph, FakeBlobStore, ArtifactRegistryService]:
    graph = _Graph()
    for user_id, key in ((OWNER, "ukey-owner"), (COLLEAGUE, "ukey-colleague"), (STRANGER, "ukey-stranger")):
        graph.add_user(user_id, key=key)
        graph.nodes[CollectionNames.USERS.value][key] = {"_key": key, "userId": user_id}
    blob = FakeBlobStore(signs_urls=signs_urls)
    return graph, blob, ArtifactRegistryService(graph, blob, pdp=pdp)


class _FakePdp:
    """Node's H5 for one conversation: members read an artifact only when its
    `runId` is a turn whose asker consented to share tool results."""

    def __init__(self, *, members: set[str], consented_runs: set[str]) -> None:
        self.members = members
        self.consented_runs = consented_runs
        self.reqs: list[ChatContentCheck] = []

    async def can_read_chat_content(self, req: ChatContentCheck) -> bool:
        self.reqs.append(req)
        return (
            req.resource_type == "chatArtifact"
            and req.conversation_id == CONVERSATION
            and req.user_id in self.members
            and req.run_id in self.consented_runs
        )


async def _export(
    graph: _Graph, blob: FakeBlobStore, *, user_id: str | None = OWNER, run_id: str | None = RUN,
) -> dict[str, Any]:
    result = await save_query_result_csv(
        blob_store=blob, graph_provider=graph, org_id=ORG, user_id=user_id,
        conversation_id=CONVERSATION, columns=COLUMNS, rows=ROWS,
        file_name="query_result_1.csv", source_tool="sql.execute_sql_query", run_id=run_id,
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
        assert artifact["runId"] == RUN
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
            await TieredRecordAuthorizer(graph).authorize(colleague, _record(graph, entry))

    async def test_user_in_another_org_cannot_read_it(self) -> None:
        graph, blob, registry = _setup()
        entry = await _export(graph, blob)

        # Reported as missing, so another org cannot even confirm it exists.
        with pytest.raises(ArtifactNotFoundError):
            await registry.get_download_url(actor=Actor(org_id="org-2", user_id=OWNER), artifact_id=entry["recordId"])


def _record(graph: _Graph, entry: dict[str, Any]) -> dict[str, Any]:
    return graph.nodes[CollectionNames.RECORDS.value][entry["recordId"]]


class TestSharingTheConversation:
    async def test_collaborator_reads_when_node_allows_the_turn(self) -> None:
        pdp = _FakePdp(members={COLLEAGUE}, consented_runs={RUN})
        graph, blob, registry = _setup(pdp=pdp)
        entry = await _export(graph, blob)
        record_id = entry["recordId"]
        colleague = Actor(org_id=ORG, user_id=COLLEAGUE)

        # No permission edge is written for the collaborator: the PDP is the only grant.
        assert _permission_edges(graph, record_id) == {"ukey-owner": "OWNER"}
        await registry.get_download_url(actor=colleague, artifact_id=record_id)
        await TieredRecordAuthorizer(graph, pdp).authorize(colleague, _record(graph, entry))
        assert await can_read_record(graph, pdp, user_id=COLLEAGUE, org_id=ORG, record=record_id)

        assert {(r.user_id, r.record_id, r.run_id, r.owner_user_id) for r in pdp.reqs} == {
            (COLLEAGUE, record_id, RUN, OWNER),
        }

    async def test_stranger_is_denied(self) -> None:
        pdp = _FakePdp(members={COLLEAGUE}, consented_runs={RUN})
        graph, blob, registry = _setup(pdp=pdp)
        entry = await _export(graph, blob)
        stranger = Actor(org_id=ORG, user_id=STRANGER)

        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=stranger, artifact_id=entry["recordId"])
        with pytest.raises(RecordAccessDeniedError):
            await TieredRecordAuthorizer(graph, pdp).authorize(stranger, _record(graph, entry))
        assert not await can_read_record(graph, pdp, user_id=STRANGER, org_id=ORG, record=entry["recordId"])

    async def test_turn_without_consent_stays_owner_only(self) -> None:
        pdp = _FakePdp(members={COLLEAGUE}, consented_runs=set())
        graph, blob, registry = _setup(pdp=pdp)
        entry = await _export(graph, blob)

        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=Actor(org_id=ORG, user_id=COLLEAGUE), artifact_id=entry["recordId"])

    async def test_export_without_a_runid_is_owner_only(self) -> None:
        pdp = _FakePdp(members={COLLEAGUE}, consented_runs={RUN})
        graph, blob, registry = _setup(pdp=pdp)
        entry = await _export(graph, blob, run_id=None)

        assert graph.nodes[CollectionNames.ARTIFACTS.value][entry["recordId"]].get("runId") is None
        with pytest.raises(AccessDeniedError):
            await registry.get_download_url(actor=Actor(org_id=ORG, user_id=COLLEAGUE), artifact_id=entry["recordId"])
        await registry.get_download_url(actor=Actor(org_id=ORG, user_id=OWNER), artifact_id=entry["recordId"])


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

    async def test_collaborator_cannot_add_a_version(self) -> None:
        pdp = _FakePdp(members={COLLEAGUE}, consented_runs={RUN})
        graph, blob, registry = _setup(pdp=pdp)
        entry = await _export(graph, blob)

        with pytest.raises(AccessDeniedError):
            await registry.add_version(
                actor=Actor(org_id=ORG, user_id=COLLEAGUE), artifact_id=entry["recordId"], content=b"x\r\n",
            )
