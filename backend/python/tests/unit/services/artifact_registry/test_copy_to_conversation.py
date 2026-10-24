"""`ArtifactRegistryService.copy_to_conversation`: what a forked chat gets.

The copy is a separate artifact, so editing it from the fork leaves the original
alone and the original's later edits never reach the fork. Runs over the real
registry with the in-memory graph and blob fakes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.services.artifact_registry import ArtifactRegistryService
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import Actor, ArtifactMetadata

from .fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
USER = "user-1"
OTHER_USER = "user-2"
ORIGINAL = "conv-original"
FORK = "conv-fork"


@pytest.fixture
def world() -> tuple[FakeGraphProvider, FakeBlobStore, ArtifactRegistryService]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    graph.add_user(OTHER_USER, key="ukey-2")
    blob = FakeBlobStore()
    return graph, blob, ArtifactRegistryService(graph, blob)


@pytest.fixture(autouse=True)
def _blob_reads(world: tuple[FakeGraphProvider, FakeBlobStore, ArtifactRegistryService]) -> Iterator[None]:
    _, blob, _ = world

    async def _fake_fetch(*, org_id, config_service, storage_document_id, version=None, **_kw) -> bytes:
        return await blob.get_buffer(org_id, storage_document_id, version)

    with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=_fake_fetch):
        yield


def _actor(user: str = USER) -> Actor:
    return Actor(org_id=ORG, user_id=user)


async def _make(service: ArtifactRegistryService, *versions: bytes, name: str = "report.md") -> ArtifactMetadata:
    from app.models.entities import ArtifactType

    meta = await service.register(
        actor=_actor(), name=name, artifact_type=ArtifactType.DOCUMENT, mime_type="text/markdown",
        content=versions[0], conversation_id=ORIGINAL, source_tool="save_artifact",
    )
    for content in versions[1:]:
        await service.add_version(actor=_actor(), artifact_id=meta.artifact_id, content=content)
    return await service.resolve(actor=_actor(), ref=meta.artifact_id)


class TestCopyToConversation:
    async def test_copy_is_a_new_artifact_in_the_fork_with_the_same_content(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1 text")

        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=1, conversation_id=FORK,
        )

        assert copy.artifact_id != source.artifact_id
        assert copy.document_id != source.document_id
        assert copy.source_artifact_id == source.artifact_id
        assert copy.source_document_id == source.document_id
        assert copy.versions == {1: 1}
        assert await service.get_content(actor=_actor(), artifact_id=copy.artifact_id) == b"v1 text"

        listed = await service.list_for_conversation(actor=_actor(), conversation_id=FORK)
        assert [a.artifact_id for a in listed] == [copy.artifact_id]
        assert (listed[0].name, listed[0].source_tool) == ("report.md", "save_artifact")
        by_name = await service.resolve(actor=_actor(), ref="report.md", conversation_id=FORK)
        assert by_name.artifact_id == copy.artifact_id

    async def test_versions_after_the_fork_point_are_left_behind(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1", b"v2", b"v3 written after the fork")

        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=2, conversation_id=FORK,
        )

        assert copy.versions == {1: 1, 2: 2}
        current = await service.resolve(actor=_actor(), ref=copy.artifact_id)
        assert current.version == 2
        assert await service.get_content(actor=_actor(), artifact_id=copy.artifact_id) == b"v2"
        assert await service.get_content(actor=_actor(), artifact_id=copy.artifact_id, version=1) == b"v1"

    async def test_up_to_version_past_the_current_one_is_clamped(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1", b"v2")

        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=9, conversation_id=FORK,
        )

        assert copy.versions == {1: 1, 2: 2}

    async def test_identical_versions_collapse_and_are_mapped_to_the_surviving_one(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"same", b"changed")
        await service.add_version(actor=_actor(), artifact_id=source.artifact_id, content=b"changed again")

        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=3, conversation_id=FORK,
        )

        assert set(copy.versions) == {1, 2, 3}
        assert copy.versions[3] == max(copy.versions.values())

    async def test_edits_after_the_fork_stay_on_their_own_side(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1 text")
        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=1, conversation_id=FORK,
        )

        await service.add_version(actor=_actor(), artifact_id=copy.artifact_id, content=b"edited in the fork")
        await service.add_version(actor=_actor(), artifact_id=source.artifact_id, content=b"edited in the original")

        assert await service.get_content(actor=_actor(), artifact_id=copy.artifact_id) == b"edited in the fork"
        assert await service.get_content(actor=_actor(), artifact_id=source.artifact_id) == b"edited in the original"
        assert (await service.resolve(actor=_actor(), ref=copy.artifact_id)).version == 2
        assert (await service.resolve(actor=_actor(), ref=source.artifact_id)).version == 2
        original_listed = await service.list_for_conversation(actor=_actor(), conversation_id=ORIGINAL)
        fork_listed = await service.list_for_conversation(actor=_actor(), conversation_id=FORK)
        assert [a.artifact_id for a in original_listed] == [source.artifact_id]
        assert [a.artifact_id for a in fork_listed] == [copy.artifact_id]

    async def test_an_artifact_the_caller_cannot_read_is_not_copied(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"private")

        with pytest.raises((ArtifactNotFoundError, AccessDeniedError)):
            await service.copy_to_conversation(
                actor=_actor(OTHER_USER), artifact_id=source.artifact_id, up_to_version=1, conversation_id=FORK,
            )
        assert await service.list_for_conversation(actor=_actor(OTHER_USER), conversation_id=FORK) == []

    async def test_a_missing_artifact_raises_not_found(self, world) -> None:
        _, _, service = world

        with pytest.raises(ArtifactNotFoundError):
            await service.copy_to_conversation(
                actor=_actor(), artifact_id="does-not-exist", up_to_version=1, conversation_id=FORK,
            )

    async def test_deleting_the_original_chat_leaves_the_forks_copy(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1 text")
        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=1, conversation_id=FORK,
        )

        result = await service.delete_for_conversation(actor=_actor(), conversation_id=ORIGINAL)

        assert (result.deleted, result.kept) == (1, 0)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=_actor(), ref=source.artifact_id)
        assert await service.get_content(actor=_actor(), artifact_id=copy.artifact_id) == b"v1 text"

    async def test_deleting_the_fork_removes_its_copy_and_not_the_original(self, world) -> None:
        _, _, service = world
        source = await _make(service, b"v1 text")
        copy = await service.copy_to_conversation(
            actor=_actor(), artifact_id=source.artifact_id, up_to_version=1, conversation_id=FORK,
        )

        result = await service.delete_for_conversation(actor=_actor(), conversation_id=FORK)

        assert (result.deleted, result.kept) == (1, 0)
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=_actor(), ref=copy.artifact_id)
        assert await service.get_content(actor=_actor(), artifact_id=source.artifact_id) == b"v1 text"
