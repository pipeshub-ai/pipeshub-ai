"""Approvals for a personal toolset instance, one a user added for themselves: its rules are the
person's own, as an MCP server's are. Its card offers "Don't ask again", which saves the answer
as its rule once the instance is confirmed to be theirs; the model is told the user can change
it. An org instance's rules stay the admin's."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents import toolset_store
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.agents.agent_loop.tool_approvals import ToolApprovalAnswer
from app.agents.agent_loop.tool_loader import _record_for_approvals
from tests.unit.agents.agent_loop.test_tool_approvals import (
    _context,
    _Store,
    run_request,
)
from tests.unit.agents.agent_loop.test_toolset_tool_approvals import (
    _DONE,
    _FILE_IT,
    _INSTANCE,
    _Jira,
    _stopped_turn,
)

_OWN = {"_id": "inst-j", "scope": "personal", "orgId": "org-1", "createdBy": "user-1"}


def _tools(context: Any, *, personal: bool = True) -> dict[str, Any]:  # noqa: ANN401
    tools = {t.name: t for t in ToolsetBuilder(_Jira(), name="jira", description="Jira", path_prefix="/tools/jira").tools}
    for t in tools.values():
        _record_for_approvals(context, t, _INSTANCE, fallback_owner="jira", personal=personal)
    return tools


async def _ask(store: _Store) -> dict[str, Any]:
    context = _context(store)
    asked = await run_request(context, list(_tools(context).values()), _FILE_IT, _DONE)
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    return blocked["approval"]


async def _answer(store: _Store, approval_id: str, decision: str, *, owned: bool) -> None:
    answer = ToolApprovalAnswer(approval_id=approval_id, decision=decision, remember=True)  # type: ignore[arg-type]
    context = _context(store, tool_approval=answer)
    with patch.object(toolset_store, "get_personal_instance", new=AsyncMock(return_value=_OWN if owned else None)):
        await run_request(
            context, list(_tools(context).values()), _DONE, history=_stopped_turn(), query=f"Tool approval: {decision}",
        )


def _saved_rules(store: _Store) -> dict[str, Any]:
    return (store.values.get("/services/toolset-tool-rules/inst-j") or {}).get("tools", {})


@pytest.fixture(autouse=True)
def _nothing_created() -> None:
    _Jira.created = []


class TestItsCard:
    async def test_offers_dont_ask_again(self) -> None:
        assert (await _ask(_Store()))["canRemember"] is True

    async def test_an_org_instances_card_still_doesnt(self) -> None:
        store = _Store()
        context = _context(store)
        asked = await run_request(context, list(_tools(context, personal=False).values()), _FILE_IT, _DONE)
        (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
        assert blocked["approval"]["canRemember"] is False

    async def test_a_denied_tool_is_the_users_to_allow_not_an_admins(self) -> None:
        from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

        store = _Store()
        store.values["/services/toolset-tool-rules/inst-j"] = {"tools": {"delete_issue": "block"}}
        context = _context(store)
        ctx = ToolCallContext(tool_path=_tools(context)["jira__delete_issue"].path, tool_input={"key": "J-1"})
        await approvals.approval_gate(context)(ctx, AsyncMock())
        assert ctx.decision == PreDecision.DENY
        reason = ctx.decision_reason or ""
        assert "the user's tool approval rules" in reason and "admin" not in reason


class TestDontAskAgain:
    @pytest.mark.parametrize("decision,rule", [("allow", "allow"), ("deny", "block")])
    async def test_saves_the_owners_rule(self, decision: str, rule: str) -> None:
        store = _Store()
        approval = await _ask(store)
        await _answer(store, approval["approvalId"], decision, owned=True)
        assert _saved_rules(store) == {"delete_issue": rule}

    async def test_saves_nothing_unless_the_instance_is_the_persons_own(self) -> None:
        store = _Store()
        approval = await _ask(store)
        await _answer(store, approval["approvalId"], "allow", owned=False)
        assert _saved_rules(store) == {}


class TestTheLoaderKnowsWhichAreOwn:
    def test_from_the_mark_the_server_put_on_the_sign_in(self) -> None:
        context = MagicMock(toolset_configs={"inst-j": {"scope": "personal"}, "inst-o": {"isAuthenticated": True}})
        creator = ToolInstanceCreator(context)
        assert (creator.instance_is_personal("inst-j"), creator.instance_is_personal("inst-o"), creator.instance_is_personal("x")) == (
            True, False, False,
        )
