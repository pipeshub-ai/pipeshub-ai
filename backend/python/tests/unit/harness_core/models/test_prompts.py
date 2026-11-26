"""P4.7 PR-1, PR-2: the prompt registry contract on the in-memory adapter, and its data loader."""

from __future__ import annotations

import pytest

from harness_core.models.errors import PromptVersionConflict, PromptVersionUnknown
from harness_core.models.prompts import InMemoryPromptRegistry
from harness_core.testing.contracts.prompts import PromptRegistryContract


class TestInMemoryPromptRegistry(PromptRegistryContract):
    @pytest.fixture
    def registry(self) -> InMemoryPromptRegistry:
        return InMemoryPromptRegistry()


async def test_pr1_a_document_loads_under_the_ports_rules() -> None:
    registry = InMemoryPromptRegistry.from_document({"prompts": [{"name": "p", "version": "1", "text": "t"}]})
    assert (await registry.get("p", "1")).text == "t"
    with pytest.raises(PromptVersionUnknown):
        _ = await registry.get("p", "9")
    with pytest.raises(PromptVersionConflict):
        InMemoryPromptRegistry.from_document(
            {"prompts": [{"name": "p", "version": "1", "text": "t"}, {"name": "p", "version": "1", "text": "u"}]}
        )
