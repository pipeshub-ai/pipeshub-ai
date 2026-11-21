"""A file a service-account agent's run makes is stamped when it is written, and is never shared
on: the agent's admin chose who may use what the agent can read, and the person chatting owns
the file but may not grant it to anyone else. Runs over the real registry and sharing service
with the in-memory graph and blob fakes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import (
    Actor,
    artifact_owner,
    mark_service_agent_run,
)
from app.services.artifact_registry.registry import ArtifactRegistryService
from app.services.artifact_registry.sharing import (
    ArtifactSharingService,
    ShareNotAllowedError,
)

from .fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ORG = "org-1"
BOB = "bob"
ZED = "zed"
CONVERSATION = "conv-1"


class _Graph(FakeGraphProvider):
    """The registry's fake, answering the sharing service's lookups as well."""

    async def get_document(self, doc_id: str, collection: str, transaction: str | None = None, *,
                           raise_on_error: bool = False) -> dict | None:
        return await super().get_document(doc_id, collection)

    async def get_edge(self, *, raise_on_error: bool = False, transaction: str | None = None, **kwargs: str) -> dict | None:
        return await super().get_edge(**kwargs)

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False, **scope: str) -> dict | None:
        # An edition may bind the lookup to the asking org; the base fake takes that binding unchanged.
        user = await super().get_user_by_user_id(user_id, **scope)
        return {**user, "orgId": ORG} if user else None


@pytest.fixture
def blob() -> FakeBlobStore:
    return FakeBlobStore()


@pytest.fixture
def world(blob: FakeBlobStore) -> tuple[_Graph, ArtifactRegistryService]:
    graph = _Graph()
    graph.add_user(BOB, key="key-bob")
    graph.add_user(ZED, key="key-zed")
    return graph, ArtifactRegistryService(graph, blob)


async def _in_service_agent_run(work: Callable[[], Awaitable[Any]]) -> Any:  # noqa: ANN401
    """`work` as a service-account agent's run does it: in the run's own task, marked at its start."""

    async def run() -> Any:  # noqa: ANN401
        mark_service_agent_run(active=True)
        return await work()

    return await asyncio.create_task(run())


def _bob(run_id: str = "r1") -> Actor:
    return Actor(org_id=ORG, user_id=BOB, run_id=run_id)


async def _register(service: ArtifactRegistryService, actor: Actor, content: bytes = b"a,b\n1,2\n") -> str:
    made = await service.register(
        actor=actor, name="terms.csv", artifact_type=ArtifactType.DATA_FILE, mime_type="text/csv",
        content=content, conversation_id=CONVERSATION,
    )
    return made.artifact_id


def _stamped(graph: _Graph, artifact_id: str) -> bool:
    return graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id].get("serviceAgentRun") is True


class TestStampedWhenWritten:
    async def test_a_file_a_service_agent_run_makes_is_stamped(self, world: tuple[_Graph, ArtifactRegistryService]) -> None:
        graph, service = world
        artifact_id = await _in_service_agent_run(lambda: _register(service, _bob()))
        assert _stamped(graph, artifact_id)

    async def test_a_file_any_other_run_makes_is_not(self, world: tuple[_Graph, ArtifactRegistryService]) -> None:
        graph, service = world
        artifact_id = await _register(service, _bob())
        assert not _stamped(graph, artifact_id)
        assert "serviceAgentRun" not in graph.nodes[CollectionNames.ARTIFACTS.value][artifact_id]

    async def test_every_way_a_run_writes_a_new_file_is_stamped(self, world: tuple[_Graph, ArtifactRegistryService]) -> None:
        graph, service = world

        async def both() -> tuple[str, str]:
            output, _ = await service.register_output(
                actor=_bob(), name="chart.png", artifact_type=ArtifactType.IMAGE, mime_type="image/png",
                content=b"png", conversation_id=CONVERSATION,
            )
            existing = await service.register_existing(
                actor=_bob(), document_id="doc-9", name="rows.csv", artifact_type=ArtifactType.DATA_FILE,
                mime_type="text/csv", size_bytes=3, conversation_id=CONVERSATION,
            )
            return output.artifact_id, existing.artifact_id

        output_id, existing_id = await _in_service_agent_run(both)
        assert _stamped(graph, output_id) and _stamped(graph, existing_id)

    async def test_the_actor_of_a_service_agent_runs_state_stamps_what_it_writes(
        self, world: tuple[_Graph, ArtifactRegistryService],
    ) -> None:
        """A tool that builds its actor from the run's state needs no mark on its task."""
        graph, service = world
        actor = artifact_owner({
            "org_id": ORG, "user_id": "agent-creator", "artifact_owner_user_id": BOB, "is_service_account": True,
        })
        artifact_id = await _register(service, actor)
        assert actor.user_id == BOB and _stamped(graph, artifact_id)

    async def test_a_version_a_service_agent_run_adds_stamps_the_file_for_good(
        self, world: tuple[_Graph, ArtifactRegistryService],
    ) -> None:
        graph, service = world
        artifact_id = await _register(service, _bob())
        await _in_service_agent_run(lambda: service.add_version(actor=_bob("r2"), artifact_id=artifact_id, content=b"v2"))
        assert _stamped(graph, artifact_id)

        await service.add_version(actor=_bob("r3"), artifact_id=artifact_id, content=b"v3 by a later solo turn")
        assert _stamped(graph, artifact_id)

    async def test_a_copy_of_a_stamped_file_is_stamped(
        self, world: tuple[_Graph, ArtifactRegistryService], blob: FakeBlobStore,
    ) -> None:
        """A forked chat copies its files; the copy is the same agent's output."""
        graph, service = world
        artifact_id = await _in_service_agent_run(lambda: _register(service, _bob()))

        async def _read(*, org_id: str, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
            return await blob.get_buffer(org_id, storage_document_id, version)

        with patch("app.agents.actions.util.blob_staging.fetch_blob_bytes", new=_read):
            copy = await service.copy_to_conversation(
                actor=_bob(), artifact_id=artifact_id, up_to_version=1, conversation_id="conv-fork",
            )

        assert _stamped(graph, copy.artifact_id)


class TestNeverSharedOn:
    async def test_bob_cannot_grant_a_service_agent_file_to_zed(self, world: tuple[_Graph, ArtifactRegistryService]) -> None:
        graph, service = world
        artifact_id = await _in_service_agent_run(lambda: _register(service, _bob()))
        edges_before = list(graph.edges[CollectionNames.PERMISSION.value])

        with pytest.raises(ShareNotAllowedError):
            await ArtifactSharingService(graph).share(_bob(), artifact_id, [ZED])

        assert graph.edges[CollectionNames.PERMISSION.value] == edges_before

    async def test_a_file_bob_made_himself_is_still_shared(self, world: tuple[_Graph, ArtifactRegistryService]) -> None:
        graph, service = world
        artifact_id = await _register(service, _bob())

        assert await ArtifactSharingService(graph).share(_bob(), artifact_id, [ZED]) == 1
