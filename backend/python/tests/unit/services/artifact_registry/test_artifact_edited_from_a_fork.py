"""What happens to an artifact made in a chat when the chat is forked and the
artifact is then edited from the fork.

A fork copies the original's messages, so its old messages still point at the
same artifact id. This runs the real model-facing tools (`ArtifactManager`) over
the real `ArtifactRegistryService` with the in-memory graph and blob fakes, once
as the original conversation and once as the fork (same user and org, a
different conversation id), and records what each side can see and do.

This is the registry's behaviour when a fork keeps the original's artifact id.
Forking a chat no longer does that: it copies the artifacts first
(`test_copy_to_conversation.py`), so these cases only matter for an id the copy
could not reach.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.services.artifact_registry import ArtifactRegistryService
from app.services.artifact_registry.access import ArtifactNotFoundError
from app.services.artifact_registry.models import Actor

from .fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
USER = "user-1"
ORIGINAL = "conv-original"
FORK = "conv-fork"


def _tools(graph: FakeGraphProvider, blob: FakeBlobStore, conversation_id: str) -> ArtifactManager:
    return ArtifactManager({
        "org_id": ORG, "user_id": USER, "conversation_id": conversation_id,
        "graph_provider": graph, "blob_store": blob,
    })


def _body(result: tuple[bool, str]) -> dict:
    ok, payload = result
    assert ok is True, payload
    return json.loads(payload)


@pytest.fixture
def world() -> tuple[FakeGraphProvider, FakeBlobStore]:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    blob = FakeBlobStore()
    return graph, blob


async def _read(blob: FakeBlobStore, service: ArtifactRegistryService, artifact_id: str, version: int | None) -> bytes:
    async def _fake_fetch(*, org_id, config_service, storage_document_id, version=None, **_kw) -> bytes:
        return await blob.get_buffer(org_id, storage_document_id, version)

    with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=_fake_fetch):
        return await service.get_content(
            actor=Actor(org_id=ORG, user_id=USER), artifact_id=artifact_id, version=version,
        )


class TestArtifactEditedFromAFork:
    async def test_the_fork_cannot_list_or_find_by_name_but_can_edit_by_id(self, world) -> None:
        graph, blob = world
        original = _tools(graph, blob, ORIGINAL)
        fork = _tools(graph, blob, FORK)
        service = ArtifactRegistryService(graph, blob)
        actor = Actor(org_id=ORG, user_id=USER)

        made = _body(await original.save_artifact(name="report.md", content="v1 text", mime_type="text/markdown"))
        artifact_id = made["artifact_id"]
        assert made["version"] == 1

        # The model in the fork is asked for "the report from earlier".
        assert _body(await fork.list_artifacts())["count"] == 0
        with pytest.raises(ArtifactNotFoundError):
            await service.resolve(actor=actor, ref="report.md", conversation_id=FORK)

        # Edit by the id carried in the copied history.
        edited = _body(await fork.update_artifact(artifact_id=artifact_id, content="v2 edited in the fork"))
        assert edited["artifact_id"] == artifact_id
        assert edited["version"] == 2

    async def test_the_original_still_has_version_one_but_its_artifact_moved_to_version_two(self, world) -> None:
        graph, blob = world
        original = _tools(graph, blob, ORIGINAL)
        fork = _tools(graph, blob, FORK)
        service = ArtifactRegistryService(graph, blob)

        artifact_id = _body(
            await original.save_artifact(name="report.md", content="v1 text", mime_type="text/markdown")
        )["artifact_id"]
        _body(await fork.update_artifact(artifact_id=artifact_id, content="v2 edited in the fork"))

        # What the original chat's old card asks for (it pins version 1): still intact.
        assert await _read(blob, service, artifact_id, 1) == b"v1 text"
        # What anything asking for the current version gets.
        assert await _read(blob, service, artifact_id, None) == b"v2 edited in the fork"

        # The original conversation's own list now reports the artifact at version 2.
        listed = _body(await original.list_artifacts())
        assert [(a["artifact_id"], a["version"]) for a in listed["artifacts"]] == [(artifact_id, 2)]
        # The fork still does not list it.
        assert _body(await fork.list_artifacts())["count"] == 0

    async def test_saving_the_same_name_in_the_fork_makes_a_separate_artifact(self, world) -> None:
        graph, blob = world
        original = _tools(graph, blob, ORIGINAL)
        fork = _tools(graph, blob, FORK)
        service = ArtifactRegistryService(graph, blob)

        first = _body(await original.save_artifact(name="report.md", content="v1 text", mime_type="text/markdown"))
        again = _body(await fork.save_artifact(name="report.md", content="written in the fork", mime_type="text/markdown"))

        assert again["artifact_id"] != first["artifact_id"]
        assert again["version"] == 1
        assert await _read(blob, service, first["artifact_id"], None) == b"v1 text"
        assert [a["artifact_id"] for a in _body(await fork.list_artifacts())["artifacts"]] == [again["artifact_id"]]
        assert [a["artifact_id"] for a in _body(await original.list_artifacts())["artifacts"]] == [first["artifact_id"]]
