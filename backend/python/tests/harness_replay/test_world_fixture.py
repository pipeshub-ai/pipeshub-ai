"""P4.9 JF-1: the replay world's Jira takes the fields the real tool takes for a type and a priority."""

from __future__ import annotations

from typing import Any

from app.agents.actions.jira.jira import Jira as RealJira
from harness_core.tools.decorators import TOOL_META_ATTR
from tests.harness_replay.world import Jira, ReplayWorld, activate, deactivate


def _params(cls: type[Any], name: str) -> dict[str, Any]:
    meta = getattr(getattr(cls, name), TOOL_META_ATTR)
    return {p.name: p for p in meta.parameters}


def test_jf1_create_issue_takes_the_real_tools_type_and_priority() -> None:
    fixture, real = _params(Jira, "create_issue"), _params(RealJira, "create_issue")
    for name in ("issue_type_name", "priority_name"):
        assert fixture[name].description == real[name].description
        assert fixture[name].required is False


async def test_jf1_the_commit_records_them() -> None:
    world = ReplayWorld()
    token = activate(world)
    try:
        _ = await Jira().create_issue(
            "PROJ", "Login returns 500 on SSO", issue_type_name="Bug", priority_name="Highest"
        )
    finally:
        deactivate(token)
    (effect,) = world.effects()
    assert (effect.payload["issue_type_name"], effect.payload["priority_name"]) == ("Bug", "Highest")
