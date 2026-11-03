"""`ArtifactRegistryService.delete` / `delete_for_conversation`: only the owner
deletes, the stored file goes before the graph so a failure is always
retryable, and nothing that referenced the artifact is left behind."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType, ArtifactVisibility
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import (
    ArtifactRegistryService,
    ArtifactStorageError,
)

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OTHER_ORG = "org-2"
OWNER = Actor(org_id=ORG, user_id="user-1")
READER = Actor(org_id=ORG, user_id="user-2")
ADMIN = Actor(org_id=ORG, user_id="admin-1")
OUTSIDER = Actor(org_id=OTHER_ORG, user_id="user-3")

RECORDS = CollectionNames.RECORDS.value
ARTIFACTS = CollectionNames.ARTIFACTS.value
PERMISSION = CollectionNames.PERMISSION.value


def _make_service() -> tuple[ArtifactRegistryService, FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user("user-1", key="ukey-1")
    graph.add_user("user-2", key="ukey-2")
    graph.add_user("admin-1", key="ukey-admin")
    graph.add_user("user-3", key="ukey-3")
    blob = FakeBlobStore()
    return ArtifactRegistryService(graph, blob), graph, blob


async def _create(service: ArtifactRegistryService, name: str = "chart.png", *, conversation_id: str = "conv-1",
                  actor: Actor = OWNER, **kwargs) -> str:
    metadata = await service.register(
        actor=actor, name=name, artifact_type=kwargs.pop("artifact_type", ArtifactType.IMAGE),
        mime_type="image/png", content=name.encode(), conversation_id=conversation_id, **kwargs,
    )
    return metadata.artifact_id


def _share(graph: FakeGraphProvider, artifact_id: str, user_key: str = "ukey-2") -> None:
    graph.edges[PERMISSION].append({
        "from_id": user_key, "from_collection": CollectionNames.USERS.value,
        "to_id": artifact_id, "to_collection": RECORDS, "type": "USER", "role": "READER",
    })


def _document_id(graph: FakeGraphProvider, artifact_id: str) -> str:
    return graph.nodes[RECORDS][artifact_id]["externalRecordId"]


def _gone(graph: FakeGraphProvider, artifact_id: str) -> bool:
    return (
        artifact_id not in graph.nodes[RECORDS]
        and artifact_id not in graph.nodes[ARTIFACTS]
        and not graph.edges_touching(artifact_id)
    )


class TestWhoMayDelete:
    async def test_owner_deletes_record_metadata_edges_and_every_stored_version(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        await service.add_version(actor=OWNER, artifact_id=artifact_id, content=b"v2")
        await service.add_version(actor=OWNER, artifact_id=artifact_id, content=b"v3")
        _share(graph, artifact_id)
        document_id = _document_id(graph, artifact_id)

        await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert _gone(graph, artifact_id)
        assert blob.purge_calls == [(ORG, document_id)]
        assert document_id not in blob.documents

    async def test_reader_from_a_shared_chat_is_refused_and_nothing_changes(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        _share(graph, artifact_id)

        with pytest.raises(AccessDeniedError):
            await service.delete(actor=READER, artifact_id=artifact_id)

        assert artifact_id in graph.nodes[RECORDS]
        assert artifact_id in graph.nodes[ARTIFACTS]
        assert blob.purge_calls == []

    async def test_admin_without_an_owner_edge_is_refused(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)

        with pytest.raises(AccessDeniedError):
            await service.delete(actor=ADMIN, artifact_id=artifact_id)

        assert artifact_id in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_another_org_sees_not_found(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)

        with pytest.raises(ArtifactNotFoundError):
            await service.delete(actor=OUTSIDER, artifact_id=artifact_id)

        assert artifact_id in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_unknown_id_is_not_found(self) -> None:
        service, _, blob = _make_service()

        with pytest.raises(ArtifactNotFoundError):
            await service.delete(actor=OWNER, artifact_id="no-such-artifact")

        assert blob.purge_calls == []


class TestWhatIsRemoved:
    async def test_other_artifacts_in_the_same_chat_are_untouched(self) -> None:
        service, graph, blob = _make_service()
        doomed = await _create(service, "a.png")
        kept = await _create(service, "b.png")
        kept_document = _document_id(graph, kept)

        await service.delete(actor=OWNER, artifact_id=doomed)

        assert kept in graph.nodes[RECORDS] and kept in graph.nodes[ARTIFACTS]
        assert kept_document in blob.documents
        assert len(graph.edges_touching(kept)) == 2  # owner permission + isOfType

    async def test_deleting_the_code_keeps_its_outputs_and_clears_their_lineage(self) -> None:
        service, graph, _ = _make_service()
        code = await _create(service, "make_chart.py", artifact_type=ArtifactType.CODE)
        output = await _create(service, "chart.png")
        await service.record_derivation(output_artifact_id=output, code_artifact_id=code, code_version=1, output_version=1)

        await service.delete(actor=OWNER, artifact_id=code)

        assert output in graph.nodes[RECORDS]
        assert await service.lineage.get_lineage_for_output(output) is None

    async def test_deleting_an_output_drops_it_from_the_code_lineage(self) -> None:
        service, graph, _ = _make_service()
        code = await _create(service, "make_chart.py", artifact_type=ArtifactType.CODE)
        output = await _create(service, "chart.png")
        await service.record_derivation(output_artifact_id=output, code_artifact_id=code, code_version=1, output_version=1)

        await service.delete(actor=OWNER, artifact_id=output)

        assert code in graph.nodes[RECORDS]
        assert await service.lineage.get_outputs_for_code(code) == []

    async def test_record_without_a_stored_file_is_still_deleted(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        graph.nodes[RECORDS][artifact_id].pop("externalRecordId")

        await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert _gone(graph, artifact_id)
        assert blob.purge_calls == []

    async def test_storage_is_purged_under_the_actors_org(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)

        await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert [org for org, _ in blob.purge_calls] == [OWNER.org_id]


class TestFailureAndRetry:
    @pytest.mark.parametrize("error", [
        aiohttp.ClientError("storage said 503"),
        asyncio.TimeoutError(),
    ])
    async def test_storage_failure_leaves_the_graph_untouched_and_a_retry_finishes(self, error) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        blob.purge_error = error

        with pytest.raises(ArtifactStorageError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)
        assert artifact_id in graph.nodes[RECORDS] and artifact_id in graph.nodes[ARTIFACTS]
        assert len(graph.edges_touching(artifact_id)) == 2

        blob.purge_error = None
        await service.delete(actor=OWNER, artifact_id=artifact_id)
        assert _gone(graph, artifact_id)

    async def test_graph_failure_after_the_purge_is_retryable(self) -> None:
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        graph.failing.add("delete_nodes_and_edges")

        with pytest.raises(RuntimeError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)

        graph.failing.clear()
        await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert _gone(graph, artifact_id)
        assert len(blob.purge_calls) == 2  # the retry's purge finds nothing left, which is fine

    async def test_record_delete_failure_is_retryable_from_the_gallery(self) -> None:
        """The gallery finds an artifact through both its record and its metadata
        node, so a failure deleting the record must leave both in place."""
        service, graph, _ = _make_service()
        artifact_id = await _create(service)
        graph.failing.add("delete_nodes_and_edges")

        with pytest.raises(RuntimeError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)
        assert artifact_id in graph.nodes[RECORDS] and artifact_id in graph.nodes[ARTIFACTS]

        graph.failing.clear()
        await service.delete(actor=OWNER, artifact_id=artifact_id)
        assert _gone(graph, artifact_id)

    async def test_metadata_left_behind_by_a_failure_goes_with_the_chat(self) -> None:
        """If only the metadata node is left (no record, no edges, nothing it can
        expose), the chat cleanup still finds it by conversation and removes it."""
        service, graph, _ = _make_service()
        artifact_id = await _create(service)
        graph.failing.add("delete_nodes")

        with pytest.raises(RuntimeError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)
        assert artifact_id not in graph.nodes[RECORDS] and not graph.edges_touching(artifact_id)

        graph.failing.clear()
        await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")
        assert _gone(graph, artifact_id)

    async def test_refuses_a_record_that_is_not_an_artifact(self) -> None:
        """The owner check accepts any record the user owns (a KB upload, a
        synced file): delete must not purge and remove those."""
        service, graph, blob = _make_service()
        artifact_id = await _create(service)
        graph.nodes[RECORDS][artifact_id]["recordType"] = "FILE"

        with pytest.raises(ArtifactNotFoundError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert artifact_id in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_second_delete_is_not_found(self) -> None:
        service, _, _ = _make_service()
        artifact_id = await _create(service)
        await service.delete(actor=OWNER, artifact_id=artifact_id)

        with pytest.raises(ArtifactNotFoundError):
            await service.delete(actor=OWNER, artifact_id=artifact_id)

    async def test_concurrent_deletes_leave_a_clean_state(self) -> None:
        service, graph, _ = _make_service()
        artifact_id = await _create(service)

        results = await asyncio.gather(
            service.delete(actor=OWNER, artifact_id=artifact_id),
            service.delete(actor=OWNER, artifact_id=artifact_id),
            return_exceptions=True,
        )

        assert all(r is None or isinstance(r, ArtifactNotFoundError) for r in results)
        assert _gone(graph, artifact_id)


class TestAfterDelete:
    async def test_the_agent_can_no_longer_find_or_read_it(self) -> None:
        service, _, _ = _make_service()
        artifact_id = await _create(service, "chart.png")
        await service.delete(actor=OWNER, artifact_id=artifact_id)

        assert await service.list_for_conversation(actor=OWNER, conversation_id="conv-1") == []
        assert await service.resolve_by_logical_name(OWNER, "chart.png", conversation_id="conv-1") is None
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=OWNER, ref=artifact_id)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=OWNER, ref="chart.png", conversation_id="conv-1")
        with pytest.raises(ArtifactNotFoundError):
            await service.get_content(actor=OWNER, artifact_id=artifact_id)
        with pytest.raises(ArtifactNotFoundError):
            await service.add_version(actor=OWNER, artifact_id=artifact_id, content=b"again")

    async def test_regenerating_the_same_name_creates_a_new_artifact(self) -> None:
        service, _, _ = _make_service()
        first, _ = await service.register_output(
            actor=OWNER, name="chart.png", artifact_type=ArtifactType.IMAGE, mime_type="image/png",
            content=b"v1", conversation_id="conv-1",
        )
        await service.delete(actor=OWNER, artifact_id=first.artifact_id)

        second, version = await service.register_output(
            actor=OWNER, name="chart.png", artifact_type=ArtifactType.IMAGE, mime_type="image/png",
            content=b"v1", conversation_id="conv-1",
        )

        assert second.artifact_id != first.artifact_id
        assert version is None and second.version == 1


class TestDeleteForConversation:
    async def test_every_artifact_in_the_chat_goes_whatever_its_visibility(self) -> None:
        service, graph, blob = _make_service()
        visible = await _create(service, "a.png")
        staging = await _create(service, "b.py", visibility=ArtifactVisibility.STAGING)
        temporary = await _create(service, "c.pdf", is_temporary=True)
        tool_result = await _create(service, "d.json", artifact_type=ArtifactType.TOOL_RESULT)
        await service.add_version(actor=OWNER, artifact_id=visible, content=b"v2")
        other_chat = await _create(service, "e.png", conversation_id="conv-2")

        result = await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert (result.deleted, result.kept) == (4, 0)
        for artifact_id in (visible, staging, temporary, tool_result):
            assert _gone(graph, artifact_id)
        assert other_chat in graph.nodes[RECORDS]
        assert _document_id(graph, other_chat) in blob.documents

    async def test_only_the_callers_artifacts_are_deleted(self) -> None:
        service, graph, _ = _make_service()
        mine = await _create(service, "a.png")
        someone_elses = await _create(service, "b.png", actor=READER)

        result = await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert (result.deleted, result.kept) == (1, 1)
        assert _gone(graph, mine)
        assert someone_elses in graph.nodes[RECORDS]

    async def test_another_orgs_chat_with_the_same_id_is_untouched(self) -> None:
        service, graph, _ = _make_service()
        theirs = await _create(service, "a.png", actor=OUTSIDER)

        result = await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert (result.deleted, result.kept) == (0, 0)
        assert theirs in graph.nodes[RECORDS]

    async def test_large_chats_are_fully_deleted(self) -> None:
        service, graph, _ = _make_service()
        ids = [await _create(service, f"file-{i}.png") for i in range(230)]

        result = await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert result.deleted == 230
        assert not any(artifact_id in graph.nodes[RECORDS] for artifact_id in ids)

    async def test_is_idempotent(self) -> None:
        service, _, _ = _make_service()
        await _create(service, "a.png")
        await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert (await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")).deleted == 0

    async def test_an_artifact_saved_while_the_chat_is_being_deleted_goes_too(self) -> None:
        """The agent can still be writing to the chat while it is deleted; once
        the chat is gone nothing would ever delete what it wrote."""
        service, graph, blob = _make_service()
        await _create(service, "a.png")
        late: list[str] = []
        purge = blob.purge_document

        async def purge_and_race(org_id: str, document_id: str) -> int:
            if not late:
                late.append(await _create(service, "late.png"))
            return await purge(org_id, document_id)

        blob.purge_document = purge_and_race

        result = await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        assert result.deleted == 2
        assert _gone(graph, late[0])
        assert graph.nodes[ARTIFACTS] == {}

    async def test_an_unknown_caller_deletes_nothing_and_is_not_told_the_chat_is_empty(self) -> None:
        """Without a user record every owner check fails as "not found"; that
        must not read as "nothing left", which would let the folder purge run."""
        service, graph, blob = _make_service()
        mine = await _create(service, "a.png")
        stranger = Actor(org_id=ORG, user_id="no-such-user")

        with pytest.raises(ArtifactNotFoundError):
            await service.delete_for_conversation(actor=stranger, conversation_id="conv-1")

        assert mine in graph.nodes[RECORDS]
        assert blob.purge_calls == []

    async def test_a_failed_listing_is_an_error_not_an_empty_chat(self) -> None:
        service, graph, _ = _make_service()
        await _create(service, "a.png")
        graph.failing.add("get_documents_paginated")

        with pytest.raises(RuntimeError):
            await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

    async def test_storage_failure_stops_and_a_retry_finishes(self) -> None:
        service, graph, blob = _make_service()
        first = await _create(service, "a.png")
        second = await _create(service, "b.png")
        blob.purge_error = aiohttp.ClientError("storage down")

        with pytest.raises(ArtifactStorageError):
            await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")

        blob.purge_error = None
        await service.delete_for_conversation(actor=OWNER, conversation_id="conv-1")
        assert _gone(graph, first) and _gone(graph, second)
