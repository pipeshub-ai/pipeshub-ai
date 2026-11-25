"""``RegistryArtifactStore`` and ``EventPublicationLog`` pass the P3.5 contract suites,
over ``ArtifactRegistryService`` with the registry's own fake graph and blob."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.harness.artifacts.events import EventPublicationLog
from app.harness.artifacts.registry_store import RegistryArtifactStore
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from harness_core.artifacts.errors import ArtifactNotWritable
from harness_core.artifacts.types import artifact_id_for
from harness_core.interactions.ports import RunBinding
from harness_core.testing.contracts.artifacts import (
    TENANT,
    ArtifactPublisherContract,
    ArtifactStoreContract,
    PublicationLogContract,
    binding,
)
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from harness_core.journal.types import NewEvent

MAX_BYTES = 1024
USER = "user-1"


@dataclass
class RegistryWorld:
    store: RegistryArtifactStore
    registry: ArtifactRegistryService
    graph: FakeGraphProvider
    blob: FakeBlobStore
    max_bytes: int = MAX_BYTES


def registry_world() -> RegistryWorld:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    blob = FakeBlobStore()
    registry = ArtifactRegistryService(graph, blob, max_bytes=MAX_BYTES)
    return RegistryWorld(RegistryArtifactStore(registry, Actor(org_id=TENANT, user_id=USER)), registry, graph, blob)


def served(world: RegistryWorld, monkeypatch: pytest.MonkeyPatch) -> RegistryWorld:
    """`get_content` reads through Node's blob route; serve it from the world's fake blob."""

    async def fetch(*, org_id: str, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
        return await world.blob.get_buffer(org_id, storage_document_id, version)

    monkeypatch.setattr("app.agents.actions.util.blob_staging.fetch_blob_bytes", fetch)
    return world


class TestRegistryArtifactStore(ArtifactStoreContract):
    @pytest.fixture
    def store_world(self, monkeypatch: pytest.MonkeyPatch) -> RegistryWorld:
        return served(registry_world(), monkeypatch)


class TestWorkspaceArtifactPublisherOverRegistry(ArtifactPublisherContract):
    """The publisher over ``RegistryArtifactStore``: every write is an ``ArtifactRegistryService`` write."""

    @pytest.fixture
    def store_world(self, monkeypatch: pytest.MonkeyPatch) -> RegistryWorld:
        return served(registry_world(), monkeypatch)


class TestEventPublicationLog(PublicationLogContract):
    """The log as PipesHub events and back: what E0 (journal) and S1a (request) bind."""

    @pytest.fixture
    def publication_log(self) -> EventPublicationLog:
        streams: dict[str, list[NewEvent]] = {}

        async def append(b: RunBinding, event: NewEvent) -> None:
            streams.setdefault(b.run_id, []).append(event)

        async def events(b: RunBinding) -> list[NewEvent]:
            return streams.get(b.run_id, [])

        return EventPublicationLog(append, events)


async def test_a_run_of_another_org_is_refused() -> None:
    world = registry_world()
    with pytest.raises(ArtifactNotWritable):
        await world.store.find_by_name(RunBinding("org-2", "conv-1", "run-1"), "a.md")


async def test_an_artifact_of_another_conversation_is_not_editable() -> None:
    world = registry_world()
    elsewhere = await world.registry.register(
        actor=Actor(org_id=TENANT, user_id=USER), name="a.md", artifact_type=ArtifactType.DOCUMENT,
        mime_type="text/markdown", content=b"x", conversation_id="conv-other",
    )
    with pytest.raises(ArtifactNotWritable):
        await world.store.current(binding(), elsewhere.artifact_id)


async def test_engine_artifacts_are_ordinary_registry_artifacts() -> None:
    """The record, card fields and lifecycle come from the registry: no parallel store."""
    world = registry_world()
    stored, _ = await world.store.register_output(
        binding(), artifact_id=artifact_id_for("pi_x"), name="deck.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        content=b"pptx", lifecycle="draft",
    )
    record = world.graph.nodes["records"][stored.artifact_id]
    doc = world.graph.nodes["artifacts"][stored.artifact_id]
    assert (record["recordType"], doc["artifactType"], doc["lifecycleStatus"]) == ("ARTIFACT", "PRESENTATION", "DRAFT")
    assert doc["conversationId"] == "conv-1" and doc["sourceTool"] == "harness.artifact_publisher"
    assert stored.kind == "PRESENTATION" and stored.storage_ref == record["externalRecordId"]
    listed = await world.registry.list_for_conversation(actor=Actor(org_id=TENANT, user_id=USER), conversation_id="conv-1")
    assert [m.artifact_id for m in listed] == [stored.artifact_id]
