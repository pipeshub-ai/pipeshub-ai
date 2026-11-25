"""SS3 (AC-1..AC-4): copying artifact versions into a private fork of their conversation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType
from app.services.artifact_registry.copies import (
    ArtifactCopier,
    CopyRequest,
    copy_artifact_id,
)
from app.services.artifact_registry.erasure import ArtifactEraser
from app.services.artifact_registry.models import Actor, ArtifactProvenance
from app.services.artifact_registry.registry import ArtifactRegistryService

from .fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "org-1"
ALICE = Actor(org_id=ORG, user_id="alice")
BOB = Actor(org_id=ORG, user_id="bob")
RECORDS = CollectionNames.RECORDS.value
ARTIFACTS = CollectionNames.ARTIFACTS.value


class World:
    def __init__(self) -> None:
        self.graph = FakeGraphProvider()
        self.graph.add_user("alice", key="ukey-alice")
        self.graph.add_user("bob", key="ukey-bob")
        self.blob = FakeBlobStore()
        self.registry = ArtifactRegistryService(self.graph, self.blob)
        self.copier = ArtifactCopier(self.registry, self.graph)

    async def artifact(self, actor: Actor, conversation: str, name: str, *versions: bytes, org: str = ORG) -> str:
        made = await self.registry.register(
            actor=Actor(org_id=org, user_id=actor.user_id),
            name=name,
            artifact_type=ArtifactType.DOCUMENT,
            mime_type="text/plain",
            content=versions[0],
            conversation_id=conversation,
        )
        for content in versions[1:]:
            await self.registry.add_version(actor=actor, artifact_id=made.artifact_id, content=content)
        return made.artifact_id

    async def content(self, actor: Actor, artifact_id: str, version: int | None = None) -> bytes:
        return await self.registry.get_content(actor=actor, artifact_id=artifact_id, version=version)


@pytest.fixture
async def world() -> AsyncIterator[World]:
    w = World()

    async def fetch(*, org_id: str, config_service: object, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
        del config_service
        document = w.blob.documents.get(storage_document_id)
        if document is None or document["org_id"] != org_id:
            raise FileNotFoundError(storage_document_id)
        return await w.blob.get_buffer(org_id, storage_document_id, version)

    with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=fetch):
        yield w


async def test_ac1_a_copy_is_a_new_artifact_with_that_versions_bytes_and_provenance(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "chart.csv", b"v1", b"v2 longer")
    await world.registry.record_derivation(
        output_artifact_id=source,
        output_version=1,
        provenance=ArtifactProvenance(version=1, run_id="run-1", task_id="t", source_record_ids=["rec-2"]),
    )
    [outcome] = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1)])
    assert outcome.reason is None
    assert outcome.copy_id == copy_artifact_id(ORG, "conv-b", source, 1) != source
    assert outcome.copy_version == 1
    assert outcome.document_id != world.graph.nodes[RECORDS][source]["externalRecordId"]
    copied = world.graph.nodes[ARTIFACTS][outcome.copy_id]
    assert copied["conversationId"] == "conv-b"
    assert world.graph.nodes[RECORDS][outcome.copy_id]["recordName"] == "chart.csv"
    assert await world.content(BOB, outcome.copy_id) == b"v1"
    [provenance] = await world.registry.lineage.provenance_of(outcome.copy_id)
    assert (provenance.version, provenance.source_record_ids) == (1, ["rec-2"])
    # The forker owns the copy; nobody else was given it.
    edges = [e for e in world.graph.edges[CollectionNames.PERMISSION.value] if e["to_id"] == outcome.copy_id]
    assert [(e["from_id"], e["role"]) for e in edges] == [("ukey-bob", "OWNER")]


async def test_ac1_without_a_version_the_current_one_is_copied(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "a.txt", b"one", b"two")
    [outcome] = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, None)])
    assert await world.content(BOB, outcome.copy_id or "") == b"two"


async def test_ac2_a_second_call_answers_the_same_copy_and_writes_nothing(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "a.txt", b"one")
    first = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1)])
    documents = dict(world.blob.documents)
    records = dict(world.graph.nodes[RECORDS])
    again = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1)])
    assert again == first
    assert world.blob.documents == documents
    assert world.graph.nodes[RECORDS] == records


async def test_ac3_refusals_are_per_artifact(world: World) -> None:
    mine = await world.artifact(BOB, "conv-a", "mine.txt", b"x")
    alices = await world.artifact(ALICE, "conv-a", "alices.txt", b"secret")
    elsewhere = await world.artifact(BOB, "conv-z", "other.txt", b"y")
    foreign = await world.artifact(BOB, "conv-a", "foreign.txt", b"z", org="org-2")
    outcomes = await world.copier.copy(
        actor=BOB,
        from_conversation_id="conv-a",
        to_conversation_id="conv-b",
        refs=[
            CopyRequest(alices, 1),
            CopyRequest(elsewhere, 1),
            CopyRequest(foreign, 1),
            CopyRequest("no-such-artifact", 1),
            CopyRequest(mine, 7),
            CopyRequest(mine, 1),
        ],
    )
    assert [o.reason for o in outcomes] == ["not_permitted", "not_found", "not_found", "not_found", "version_missing", None]
    assert [o.copy_id is not None for o in outcomes] == [False] * 5 + [True]
    copies = [doc for doc in world.graph.nodes[ARTIFACTS].values() if doc.get("conversationId") == "conv-b"]
    assert len(copies) == 1


async def test_ac4_erasing_either_conversation_leaves_the_other_whole(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "a.txt", b"bytes")
    [outcome] = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1)])
    eraser = ArtifactEraser(world.graph, world.blob)
    await eraser.erase_conversation(ORG, "conv-a")
    assert source not in world.graph.nodes[RECORDS]
    assert await world.content(BOB, outcome.copy_id or "") == b"bytes"
    await eraser.erase_conversation(ORG, "conv-b")
    assert outcome.copy_id not in world.graph.nodes[RECORDS]


async def test_ac4_the_reverse_erasing_the_copy_spares_the_source(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "a.txt", b"bytes")
    await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1)])
    await ArtifactEraser(world.graph, world.blob).erase_conversation(ORG, "conv-b")
    assert await world.content(BOB, source) == b"bytes"


def _ids(outcomes: list[Any]) -> list[str | None]:
    return [o.copy_id for o in outcomes]


async def test_copy_ids_differ_per_target_and_version(world: World) -> None:
    source = await world.artifact(BOB, "conv-a", "a.txt", b"one", b"two")
    to_b = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-b", refs=[CopyRequest(source, 1), CopyRequest(source, 2)])
    to_c = await world.copier.copy(actor=BOB, from_conversation_id="conv-a", to_conversation_id="conv-c", refs=[CopyRequest(source, 1)])
    assert len({*_ids(to_b), *_ids(to_c)}) == 3
