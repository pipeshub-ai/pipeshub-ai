"""The in-memory artifact adapters pass the P3.5 contract suites."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.testing.contracts.artifacts import (
    ArtifactPublisherContract,
    ArtifactStoreContract,
    PublicationLogContract,
)

MAX_BYTES = 1024


@dataclass
class _World:
    store: InMemoryArtifactStore = field(default_factory=lambda: InMemoryArtifactStore(max_bytes=MAX_BYTES))
    max_bytes: int = MAX_BYTES


class TestInMemoryArtifactStore(ArtifactStoreContract):
    @pytest.fixture
    def store_world(self) -> _World:
        return _World()


class TestInMemoryPublicationLog(PublicationLogContract):
    @pytest.fixture
    def publication_log(self) -> InMemoryPublicationLog:
        return InMemoryPublicationLog()


class TestWorkspaceArtifactPublisherInMemory(ArtifactPublisherContract):
    """The publisher (``WorkspaceArtifactPublisher``) over the in-memory store."""

    @pytest.fixture
    def store_world(self) -> _World:
        return _World()
