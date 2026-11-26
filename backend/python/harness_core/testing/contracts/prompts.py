"""Contract suite for ``PromptRegistry`` (P4.7): immutable versions and pinned reads.

Provide a ``registry`` fixture: an empty registry under test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.models.errors import PromptVersionConflict, PromptVersionUnknown
from harness_core.models.prompts import PromptVersion, pinned_prompt

if TYPE_CHECKING:
    from harness_core.models.prompts import PromptRegistry

V1 = PromptVersion("agent-system", "2026-09", "You are helpful.")
V2 = PromptVersion("agent-system", "2026-10", "You are helpful and brief.")


class PromptRegistryContract:
    @pytest.fixture
    def registry(self) -> PromptRegistry:
        raise NotImplementedError("provide a registry fixture")

    async def test_pr1_a_version_is_read_back_by_name_and_version(self, registry: PromptRegistry) -> None:
        _ = await registry.put(V1)
        _ = await registry.put(V2)
        assert await registry.get(V1.name, V1.version) == V1
        assert (await registry.get(V2.name, V2.version)).digest == V2.digest != V1.digest
        assert await registry.versions(V1.name) == ("2026-09", "2026-10")
        assert await registry.versions("other") == ()

    async def test_pr1_a_version_never_changes(self, registry: PromptRegistry) -> None:
        _ = await registry.put(V1)
        assert await registry.put(PromptVersion(V1.name, V1.version, V1.text)) == V1
        with pytest.raises(PromptVersionConflict) as refused:
            _ = await registry.put(PromptVersion(V1.name, V1.version, "Something else."))
        assert refused.value.reason_code == "prompt_version_conflict"
        assert (await registry.get(V1.name, V1.version)).text == V1.text

    async def test_pr1_an_unknown_version_is_refused(self, registry: PromptRegistry) -> None:
        _ = await registry.put(V1)
        with pytest.raises(PromptVersionUnknown) as refused:
            _ = await registry.get(V1.name, "2099-01")
        assert refused.value.reason_code == "prompt_version_unknown"

    async def test_pr2_a_run_reads_the_version_it_pinned(self, registry: PromptRegistry) -> None:
        _ = await registry.put(V1)
        _ = await registry.put(V2)
        model = validate_contract(
            SpecModelPolicy, {"policy": [{"provider": "openai", "model": "m"}], "promptVersion": V1.version}
        )
        assert await pinned_prompt(registry, model, V1.name) == V1


__all__ = ["PromptRegistryContract"]
