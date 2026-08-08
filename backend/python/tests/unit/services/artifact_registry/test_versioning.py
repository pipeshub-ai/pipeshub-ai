"""Tests for `app.services.artifact_registry.versioning.VersionManager` —
the single writer for artifact blob content + version bookkeeping."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import (
    ArtifactType,
    deserialize_artifact_versions,
    serialize_artifact_versions,
)
from app.services.artifact_registry.access import AccessPolicy
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.versioning import (
    VersionConflictError,
    VersionManager,
    VersionMappingNotFoundError,
    VersionSyncError,
    compute_content_hash,
    recover_and_persist_version_mapping,
    recover_storage_version,
    resolve_storage_version,
)

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
USER = "user-1"


def _make_manager() -> tuple[VersionManager, FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    blob = FakeBlobStore()
    manager = VersionManager(graph, blob, AccessPolicy(graph))
    return manager, graph, blob


class TestCreate:
    async def test_creates_record_artifact_and_owner_edge(self) -> None:
        manager, graph, blob = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)

        metadata = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"pdf-bytes", conversation_id="conv-1",
        )

        assert metadata.name == "report.pdf"
        assert metadata.version == 1
        assert metadata.size_bytes == len(b"pdf-bytes")
        assert metadata.content_hash == compute_content_hash(b"pdf-bytes")

        record = graph.nodes[CollectionNames.RECORDS.value][metadata.artifact_id]
        assert record["orgId"] == ORG
        artifact_doc = graph.nodes[CollectionNames.ARTIFACTS.value][metadata.artifact_id]
        assert artifact_doc["logicalName"] == "report.pdf"
        assert graph.edges[CollectionNames.PERMISSION.value][0]["to_id"] == metadata.artifact_id
        assert blob.documents[metadata.document_id]["content"] == b"pdf-bytes"

    async def test_uses_explicit_logical_name_when_given(self) -> None:
        manager, graph, _ = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)

        metadata = await manager.create(
            actor=actor, name="report_v2.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"x", conversation_id="conv-1",
            logical_name="report.pdf",
        )
        artifact_doc = graph.nodes[CollectionNames.ARTIFACTS.value][metadata.artifact_id]
        assert artifact_doc["logicalName"] == "report.pdf"

    async def test_raises_version_sync_error_when_blob_upload_returns_no_document_id(self) -> None:
        manager, _, blob = _make_manager()
        blob.save_versioned_artifact_to_storage = lambda **kwargs: _empty_upload_result()  # type: ignore[method-assign]
        with pytest.raises(VersionSyncError):
            await manager.create(
                actor=Actor(org_id=ORG, user_id=USER), name="x.txt", artifact_type=ArtifactType.OTHER,
                mime_type="text/plain", content=b"x", conversation_id="conv-1",
            )


async def _empty_upload_result() -> dict:
    return {}


class TestResolveStorageVersion:
    """`resolve_storage_version` is the one mapping decision shared by the
    registry and by `record_content/strategies.py`, which reads `versions`
    straight off a raw graph doc — i.e. in its stored, serialized form."""

    _VERSIONS = [
        {"registryVersion": 1, "storageVersion": 0},
        {"registryVersion": 2, "storageVersion": 1},
    ]

    def test_accepts_the_serialized_string_form(self) -> None:
        assert resolve_storage_version(3, serialize_artifact_versions(self._VERSIONS), 1) == 0

    def test_accepts_the_native_list_form(self) -> None:
        assert resolve_storage_version(3, self._VERSIONS, 1) == 0

    def test_current_version_needs_no_mapping(self) -> None:
        assert resolve_storage_version(2, serialize_artifact_versions(self._VERSIONS), 2) is None

    def test_unmapped_version_raises(self) -> None:
        with pytest.raises(VersionMappingNotFoundError):
            resolve_storage_version(3, serialize_artifact_versions(self._VERSIONS), 9)


class TestAddVersion:
    async def test_bumps_version_and_uploads_new_bytes(self) -> None:
        manager, graph, blob = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )

        version, metadata = await manager.add_version(
            actor=actor, artifact_id=created.artifact_id, content=b"v2-bytes-longer",
        )

        assert version.version == 2
        assert version.deduplicated is False
        assert metadata.version == 2
        assert metadata.size_bytes == len(b"v2-bytes-longer")
        assert blob.documents[created.document_id]["content"] == b"v2-bytes-longer"
        record = graph.nodes[CollectionNames.RECORDS.value][created.artifact_id]
        assert record["version"] == 2

    async def test_identical_content_deduplicates_without_bumping_version(self) -> None:
        manager, graph, blob = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"same-bytes", conversation_id="conv-1",
        )

        version, metadata = await manager.add_version(
            actor=actor, artifact_id=created.artifact_id, content=b"same-bytes",
        )

        assert version.deduplicated is True
        assert version.version == 1
        assert metadata.version == 1
        # No second version should have been uploaded to blob storage.
        assert len(blob.documents[created.document_id]["versions"]) == 1

    async def test_versions_bookkeeping_is_stored_in_a_neo4j_storable_shape(self) -> None:
        """Regression: `versions` used to reach the graph as a list of
        dicts. ArangoDB stores that natively, but Neo4j rejects it
        (`CypherTypeError: Property values can only be of primitive types
        or arrays thereof`), so on a Neo4j deployment EVERY version bump
        raised `VersionSyncError` after the blob write had already
        succeeded — the artifact updated in storage but never in the graph,
        no download card was delivered, and the bookkeeping needed to
        resolve older versions was never written."""
        manager, graph, _ = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1-bytes", conversation_id="conv-1",
        )

        await manager.add_version(
            actor=actor, artifact_id=created.artifact_id, content=b"v2-bytes-longer",
        )

        stored = graph.nodes[CollectionNames.ARTIFACTS.value][created.artifact_id]["versions"]
        assert isinstance(stored, str)
        entries = deserialize_artifact_versions(stored)
        assert [e["registryVersion"] for e in entries] == [1, 2]

    async def test_second_bump_reads_back_its_own_serialized_bookkeeping(self) -> None:
        """The read side must round-trip what the write side stored —
        otherwise bump #2 starts from an empty list and loses v1's
        storage mapping."""
        manager, graph, _ = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )

        await manager.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2")
        await manager.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v3")

        entries = deserialize_artifact_versions(
            graph.nodes[CollectionNames.ARTIFACTS.value][created.artifact_id]["versions"]
        )
        assert [e["registryVersion"] for e in entries] == [1, 2, 3]

    async def test_concurrent_writer_raises_version_conflict(self) -> None:
        manager, _, _ = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )

        with pytest.raises(VersionConflictError):
            await manager.add_version(
                actor=actor, artifact_id=created.artifact_id, content=b"v2",
                expected_version=99,
            )

    async def test_graph_update_failure_marks_pending_reconcile(self) -> None:
        """The blob write already succeeded (bytes are durable) — a
        subsequent graph-side failure must never look like success, and
        must leave a reconcile marker rather than silently losing the
        update."""
        manager, graph, _ = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )

        call_count = {"n": 0}
        real_update_node = graph.update_node

        async def _flaky_update_node(doc_id: str, collection: str, updates: dict) -> bool:
            call_count["n"] += 1
            if collection == CollectionNames.RECORDS.value and call_count["n"] == 1:
                raise RuntimeError("graph write failed")
            return await real_update_node(doc_id, collection, updates)

        graph.update_node = _flaky_update_node  # type: ignore[method-assign]

        with pytest.raises(VersionSyncError):
            await manager.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2")

        record = graph.nodes[CollectionNames.RECORDS.value][created.artifact_id]
        assert record["reason"] == "ARTIFACT_VERSION_PENDING_RECONCILE"


class TestRecoverStorageVersion:
    """Pure layer: infer a mapping ONLY when the history length proves it."""

    @pytest.mark.parametrize(("version", "expected"), [(1, 0), (2, 1), (3, 2)])
    def test_complete_history_maps_positionally(self, version, expected) -> None:
        assert recover_storage_version(3, 3, version) == expected

    @pytest.mark.parametrize("history_len", [0, 2, 4])
    def test_refuses_when_the_history_length_disproves_the_layout(self, history_len) -> None:
        assert recover_storage_version(3, history_len, 1) is None

    @pytest.mark.parametrize("version", [0, 4, -1])
    def test_refuses_a_version_outside_the_range(self, version) -> None:
        assert recover_storage_version(3, 3, version) is None


class TestRecoverAndPersistVersionMapping:
    """A bump whose graph write failed leaves every already-rendered card
    pinning an older version pointing at nothing. Recovery has to bring
    those back — with the RIGHT bytes — and repair the graph."""

    async def _artifact_with_lost_bookkeeping(self) -> tuple[VersionManager, FakeGraphProvider, FakeBlobStore, str]:
        manager, graph, blob = _make_manager()
        actor = Actor(org_id=ORG, user_id=USER)
        created = await manager.create(
            actor=actor, name="report.pdf", artifact_type=ArtifactType.OTHER,
            mime_type="application/pdf", content=b"v1", conversation_id="conv-1",
        )
        await manager.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v2")
        await manager.add_version(actor=actor, artifact_id=created.artifact_id, content=b"v3")
        graph.nodes[CollectionNames.ARTIFACTS.value][created.artifact_id]["versions"] = None
        return manager, graph, blob, created.artifact_id

    async def test_resolves_and_persists_the_reconstructed_mapping(self) -> None:
        _, graph, blob, artifact_id = await self._artifact_with_lost_bookkeeping()
        document_id = graph.nodes[CollectionNames.RECORDS.value][artifact_id]["externalRecordId"]

        storage_version = await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id=document_id, current_version=3, version=1,
        )

        assert storage_version == 0
        assert await blob.get_buffer(ORG, document_id, version=storage_version) == b"v1"
        entries = deserialize_artifact_versions(
            graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id]["versions"]
        )
        assert entries == [
            {"registryVersion": 1, "storageVersion": 0},
            {"registryVersion": 2, "storageVersion": 1},
            {"registryVersion": 3, "storageVersion": 2},
        ]

    async def test_repair_makes_the_next_read_need_no_recovery(self) -> None:
        _, graph, blob, artifact_id = await self._artifact_with_lost_bookkeeping()
        document_id = graph.nodes[CollectionNames.RECORDS.value][artifact_id]["externalRecordId"]

        await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id=document_id, current_version=3, version=1,
        )

        versions = graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id]["versions"]
        assert resolve_storage_version(3, versions, 2) == 1

    async def test_refuses_when_the_history_cannot_verify_the_layout(self) -> None:
        _, graph, blob, artifact_id = await self._artifact_with_lost_bookkeeping()
        document_id = graph.nodes[CollectionNames.RECORDS.value][artifact_id]["externalRecordId"]

        recovered = await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id=document_id, current_version=9, version=1,
        )

        assert recovered is None
        assert graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id]["versions"] is None

    async def test_serves_the_request_even_when_the_repair_write_fails(self) -> None:
        _, graph, blob, artifact_id = await self._artifact_with_lost_bookkeeping()
        document_id = graph.nodes[CollectionNames.RECORDS.value][artifact_id]["externalRecordId"]

        async def _failing_update_node(doc_id: str, collection: str, updates: dict) -> bool:
            raise RuntimeError("graph still down")

        graph.update_node = _failing_update_node  # type: ignore[method-assign]

        storage_version = await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id=document_id, current_version=3, version=1,
        )

        assert storage_version == 0

    async def test_unreadable_storage_history_is_not_fatal(self) -> None:
        _, graph, blob, artifact_id = await self._artifact_with_lost_bookkeeping()

        async def _failing_history(org_id: str, document_id: str) -> list[dict]:
            raise RuntimeError("storage unreachable")

        blob.get_document_version_history = _failing_history  # type: ignore[method-assign]

        recovered = await recover_and_persist_version_mapping(
            graph_provider=graph, blob_store=blob, artifact_id=artifact_id, org_id=ORG,
            document_id="doc-1", current_version=3, version=1,
        )

        assert recovered is None
