"""P4.7 PR-3: the prompt version Node pins is registered and resolves."""

from __future__ import annotations

import re

from app.harness.prompts import pipeshub_prompt_registry
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.models.prompts import pinned_prompt
from tests.cohesion.support import REPO_ROOT

NODE_RUN_SPEC = REPO_ROOT / "backend/nodejs/apps/src/modules/agent-harness/domain/run-spec.ts"


def _node_prompt_version() -> str:
    found = re.search(r"RUN_SPEC_PROMPT_VERSION = '([^']+)'", NODE_RUN_SPEC.read_text(encoding="utf-8"))
    assert found is not None
    return found.group(1)


async def test_pr3_the_version_node_pins_is_registered_and_resolves() -> None:
    registry = pipeshub_prompt_registry()
    version = _node_prompt_version()
    model = validate_contract(SpecModelPolicy, {"policy": [{"provider": "openai", "model": "m"}], "promptVersion": version})
    assert (await pinned_prompt(registry, model, "pipeshub-agent")).version == version
