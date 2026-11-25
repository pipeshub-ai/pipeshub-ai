"""The in-memory content and checkpoint stores pass their port contracts (X1, C-2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.journal.memory import InMemoryCheckpointStore, InMemoryContentStore
from harness_core.testing.contracts.content import (
    CheckpointContract,
    ContentStoreContract,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestInMemoryContentStoreContract(ContentStoreContract):
    @pytest.fixture
    def store(self) -> InMemoryContentStore:
        return InMemoryContentStore()


class TestDirectoryContentStoreContract(ContentStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> InMemoryContentStore:
        return InMemoryContentStore(tmp_path / "blobs")


class TestInMemoryCheckpointContract(CheckpointContract):
    @pytest.fixture
    def checkpoints(self) -> InMemoryCheckpointStore:
        return InMemoryCheckpointStore()


class TestDirectoryCheckpointContract(CheckpointContract):
    @pytest.fixture
    def checkpoints(self, tmp_path: Path) -> InMemoryCheckpointStore:
        return InMemoryCheckpointStore(tmp_path / "checkpoints")
