"""An organization's Jira and someone's personal Jira are two instances with two sets of rules: the
rules of one never decide the calls of the other, and a run checks each call under the rules of the
instance whose credentials make it."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_approvals import ToolRules
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, _record_for_approvals
from app.agents.tools.factories.registry import ClientFactoryRegistry
from tests.unit.agents.agent_loop.test_tool_approvals import _context, _Store
from tests.unit.agents.agent_loop.test_toolset_tool_approvals import _Jira

ORG = ("org-jira", "Jira")
MINE = ("my-jira", "My Jira")


async def _decide(context: Any, instance: tuple[str, str], *, personal: bool) -> ToolCallContext:  # noqa: ANN401
    tools = {t.name: t for t in ToolsetBuilder(_Jira(), name="jira", description="Jira", path_prefix="/tools/jira").tools}
    for t in tools.values():
        _record_for_approvals(context, t, instance, fallback_owner="jira", personal=personal)
    ctx = ToolCallContext(tool_path=tools["jira__create_issue"].path, tool_input={"title": "Bug"})

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


async def _rules(store: _Store, instance: tuple[str, str], **rules: str) -> None:
    await approvals.save_instance_rules(store, ToolRules(tools=rules), instance_id=instance[0])  # type: ignore[arg-type]


class TestEachInstanceHasItsOwnRules:
    async def test_the_orgs_jira_rules_dont_reach_a_personal_jira(self) -> None:
        store = _Store()
        await _rules(store, ORG, create_issue="block")

        assert (await _decide(_context(store), ORG, personal=False)).decision == PreDecision.DENY
        mine = await _decide(_context(store), MINE, personal=True)
        assert mine.decision == PreDecision.ASK
        assert mine.pending_approval is not None and mine.pending_approval.details["instanceId"] == "my-jira"

    async def test_a_personal_jiras_rules_dont_reach_the_orgs_jira(self) -> None:
        store = _Store()
        await _rules(store, MINE, create_issue="allow")

        assert (await _decide(_context(store), MINE, personal=True)).decision == PreDecision.ALLOW
        assert (await _decide(_context(store), ORG, personal=False)).decision == PreDecision.ASK


class _RecordingFactory:
    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []

    async def create_client(self, config_service: Any, logger: Any, config: dict, state: dict) -> dict:  # noqa: ANN401
        self.configs.append(config)
        return {"config": config}


_CONFIGS = {
    "org-jira": {"auth": {"who": "org"}},
    "my-jira": {"auth": {"who": "me"}, "scope": "personal"},
}


async def _load(order: list[tuple[str, str]], factory: _RecordingFactory) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
        agent_toolsets=[{"name": "jira", "instanceId": i, "instanceName": n} for i, n in order],
        toolset_configs=dict(_CONFIGS),
    )
    catalogue = MagicMock()
    catalogue.get_all_toolsets.return_value = {"jira": {"class": _Jira, "isInternal": False, "description": "Jira"}}
    with patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=catalogue):
        await PipesHubToolLoader().load(context)
    return context


class TestARunWithBothJiras:
    @pytest.mark.parametrize("order", [[ORG, MINE], [MINE, ORG]])
    async def test_calls_are_checked_under_the_rules_of_the_instance_that_makes_them(
        self, order: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        factory = _RecordingFactory()
        monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"jira": factory})
        monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)

        context = await _load(order, factory)

        target = context.tool_state[approvals.TOOLSET_TOOLS_BY_PATH]["/tools/jira/create_issue"]
        (used,) = factory.configs
        assert used == _CONFIGS[target.instance_id], "credentials and rules come from one instance"
        assert target.personal is (target.instance_id == "my-jira")
