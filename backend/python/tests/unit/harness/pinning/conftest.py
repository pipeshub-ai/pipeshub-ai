"""Every QA-1 case runs in the assembly world with the deployment's flow rule off: a case about the flow rule
sets the org's own (an org can switch the rule on with the flag off, SEC2). Main's toolset tool rules (#4256)
are off: these cases pin the harness's own approval policy, and main's write-asks default would add asks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.agent_loop.tool_approvals import TOOLSET_ENABLED_ENV
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV
from app.utils.env_flags import HARNESS_FLOW_GUARD
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def world_skills() -> bool:
    """Whether the world's deployment runs skills; a module that needs them overrides this fixture."""
    return False


@pytest.fixture(autouse=True)
def assembly_world(monkeypatch: pytest.MonkeyPatch, world_skills: bool) -> Iterator[None]:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "false")
    monkeypatch.setenv(TOOLSET_ENABLED_ENV, "false")
    monkeypatch.delenv(FLOW_LEVEL_ENV, raising=False)
    monkeypatch.delenv(FLOW_ABOVE_ENV, raising=False)
    with w.deployment(skills=world_skills):
        yield
