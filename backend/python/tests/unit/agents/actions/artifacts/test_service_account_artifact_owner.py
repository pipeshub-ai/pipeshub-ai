"""A service-account agent runs as its creator, but what it makes in a chat
belongs to the person chatting, as the conversation does.

Driven through the real `ArtifactRegistryService` over the in-memory graph and
blob store, so the assertions are about the permission edges the registry
writes and checks, not about which id a mock was called with.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.config.constants.arangodb import CollectionNames, OriginTypes
from app.services.artifact_registry import ArtifactRegistryService
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.models import Actor
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
CREATOR = "u-alice"
CALLER = "u-bob"


def _registry() -> tuple[ArtifactRegistryService, FakeGraphProvider]:
    graph = FakeGraphProvider()
    graph.add_user(CREATOR, key="k-alice")
    graph.add_user(CALLER, key="k-bob")
    return ArtifactRegistryService(graph, FakeBlobStore()), graph


def _manager(registry: ArtifactRegistryService, **state: object) -> ArtifactManager:
    manager = ArtifactManager({"org_id": ORG, "user_id": CREATOR, "conversation_id": "conv-1", **state})
    manager._registry = lambda: registry
    return manager


def _owners(graph: FakeGraphProvider, artifact_id: str) -> set[str]:
    return {
        edge["from_id"]
        for edge in graph.edges[CollectionNames.PERMISSION.value]
        if edge["to_id"] == artifact_id and edge["role"] == "OWNER"
    }


async def _save(manager: ArtifactManager) -> str:
    ok, payload = await manager.save_artifact(name="summary.md", content="# Summary", mime_type="text/markdown")
    assert ok, payload
    return json.loads(payload)["artifact_id"]


class TestServiceAccountChat:
    async def test_the_person_chatting_owns_what_the_agent_makes(self) -> None:
        registry, graph = _registry()

        artifact_id = await _save(_manager(registry, artifact_owner_user_id=CALLER))

        assert _owners(graph, artifact_id) == {"k-bob"}

    async def test_the_person_chatting_can_open_it_and_the_creator_cannot(self) -> None:
        registry, _ = _registry()
        artifact_id = await _save(_manager(registry, artifact_owner_user_id=CALLER))

        opened = await registry.resolve(actor=Actor(org_id=ORG, user_id=CALLER), ref=artifact_id)
        assert opened.artifact_id == artifact_id
        with pytest.raises((AccessDeniedError, ArtifactNotFoundError)):
            await registry.resolve(actor=Actor(org_id=ORG, user_id=CREATOR), ref=artifact_id)

    async def test_a_later_turn_in_the_chat_can_change_it(self) -> None:
        registry, graph = _registry()
        artifact_id = await _save(_manager(registry, artifact_owner_user_id=CALLER))

        ok, payload = await _manager(registry, artifact_owner_user_id=CALLER).update_artifact(
            artifact_id=artifact_id, content="# Summary v2", mime_type="text/markdown",
        )

        assert ok, payload
        assert json.loads(payload)["version"] == 2
        assert _owners(graph, artifact_id) == {"k-bob"}

    async def test_a_later_turn_finds_it_by_name(self) -> None:
        registry, _ = _registry()
        artifact_id = await _save(_manager(registry, artifact_owner_user_id=CALLER))

        ok, payload = await _manager(registry, artifact_owner_user_id=CALLER).list_artifacts()

        assert ok, payload
        assert artifact_id in payload


class TestEveryOtherChat:
    async def test_without_an_artifact_owner_the_run_user_owns_it(self) -> None:
        registry, graph = _registry()

        artifact_id = await _save(_manager(registry))

        assert _owners(graph, artifact_id) == {"k-alice"}

    async def test_knowledge_records_are_still_checked_as_the_run_user(self) -> None:
        """Only artifacts move to the caller: a record the agent reads is checked
        against the creator's access, which is what the agent was built with."""
        record = SimpleNamespace(
            id="rec-1", org_id=ORG, origin=OriginTypes.UPLOAD, external_record_id="blob-1",
            record_name="f.pdf", mime_type="application/pdf", weburl=None,
        )
        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(return_value=record)
        graph.check_record_access_with_details = AsyncMock(return_value=None)
        manager = ArtifactManager({
            "org_id": ORG, "user_id": CREATOR, "artifact_owner_user_id": CALLER,
            "graph_provider": graph, "blob_store": MagicMock(),
        })

        await manager.get_record_download_url("rec-1")

        graph.check_record_access_with_details.assert_awaited_once_with(CREATOR, ORG, "rec-1")
