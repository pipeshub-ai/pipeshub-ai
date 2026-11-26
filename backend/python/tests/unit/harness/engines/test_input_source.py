"""E1b IN-7: ``RegistryInputSource`` passes the ``EngineInputSource`` contract over ``ArtifactRegistryService``
(the registry's own fake graph and blob): reads authorized as the run's actor, the run's conversation only."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, override

import pytest

from app.harness.engines.inputs import RegistryInputSource
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from harness_core.testing.contracts.engine_inputs import (
    MAX_INPUT_BYTES,
    SESSION,
    TENANT,
    EngineInputSourceContract,
)
from harness_core.workspace.forks import session_scope
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import EngineInputSource
    from harness_core.workspace.types import ObjectRef

USER, STRANGER = "user-in", "user-stranger"


class _RegistryInputs:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.graph = FakeGraphProvider()
        self.graph.add_user(USER, key="ukey-in")
        self.graph.add_user(STRANGER, key="ukey-stranger")
        self.blob = FakeBlobStore()
        self.registry = ArtifactRegistryService(self.graph, self.blob, max_bytes=4096)
        self.actor = Actor(org_id=TENANT, user_id=USER)
        self.objects = InMemoryBinaryObjectStore()
        self._names = itertools.count(1)
        self._source = RegistryInputSource(
            self.registry, self.actor, self.objects, object_scope=lambda b: session_scope(b.tenant, b.scope),
            max_input_bytes=MAX_INPUT_BYTES,
        )

        async def fetch(*, org_id: str, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
            return await self.blob.get_buffer(org_id, storage_document_id, version)

        # ``get_content`` reads through Node's blob route; served from the fake blob.
        monkeypatch.setattr("app.agents.actions.util.blob_staging.fetch_blob_bytes", fetch)

    @property
    def source(self) -> EngineInputSource:
        return self._source

    async def add(self, name: str, content: bytes, *, session: str = SESSION, readable: bool = True) -> str:
        owner = self.actor if readable else Actor(org_id=TENANT, user_id=STRANGER)
        metadata, _ = await self.registry.register_output(
            actor=owner, name=name, artifact_type=ArtifactType.OTHER, mime_type="text/plain", content=content,
            conversation_id=session, artifact_id=f"art-in-{next(self._names)}",
        )
        return metadata.artifact_id

    async def add_version(self, artifact_id: str, content: bytes) -> int:
        current = await self.registry.resolve(actor=self.actor, ref=artifact_id)
        version, _ = await self.registry.add_version(
            actor=self.actor, artifact_id=artifact_id, content=content, mime_type="text/plain",
            expected_version=current.version,
        )
        return version.version

    async def discard(self, artifact_id: str) -> None:
        await self.registry.discard(actor=self.actor, artifact_id=artifact_id)

    async def stored(self, ref: ObjectRef) -> bytes:
        return await self.objects.get(session_scope(TENANT, SESSION), ref)


class TestRegistryInputSource(EngineInputSourceContract):
    @pytest.fixture
    @override
    def input_world(self, monkeypatch: pytest.MonkeyPatch) -> _RegistryInputs:  # pyright: ignore[reportIncompatibleMethodOverride]
        return _RegistryInputs(monkeypatch)
