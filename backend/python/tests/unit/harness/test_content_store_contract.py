"""PipesHub's content store (inline below 16 KiB, sealed by Node above) passes the port contract (X1, C-2)."""

from __future__ import annotations

import pytest

from app.harness.adapters.content_store import (
    InMemorySealedContent,
    PipesHubContentStore,
)
from harness_core.testing.contracts.content import ContentStoreContract


class TestPipesHubContentStoreContract(ContentStoreContract):
    @pytest.fixture
    def store(self) -> PipesHubContentStore:
        return PipesHubContentStore(InMemorySealedContent())
