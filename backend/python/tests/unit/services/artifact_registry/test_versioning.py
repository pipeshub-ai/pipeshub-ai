"""Tests for `app.services.artifact_registry.versioning.VersionManager` —
the single writer for artifact blob content + version bookkeeping."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType
from app.services.artifact_registry.access import AccessPolicy
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.versioning import (
    VersionConflictError,
    VersionManager,
    VersionSyncError,
    compute_content_hash,
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
