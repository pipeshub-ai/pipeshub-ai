"""How a request's approval answer and the gate's inputs reach the agent: the context, the MCP
adapter's identity and hints, and an approval turn skipping clarifying questions."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import ToolApprovalAnswer, mcp_tool_identity
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import make_context


class TestTheContext:
    def test_an_answer_to_a_card_is_read(self) -> None:
        context = AgentContext.from_chat_state({"tool_approval": {"approvalId": "ap-1", "decision": "allow_chat"}})
        assert context.tool_approval == ToolApprovalAnswer(approval_id="ap-1", decision="allow_chat")

    def test_a_malformed_answer_is_ignored(self) -> None:
        for raw in ({"approvalId": "ap-1", "decision": "run_anything"}, {"decision": "deny"}, "ap-1", None):
            assert AgentContext.from_chat_state({"tool_approval": raw}).tool_approval is None

    def test_whose_rules_and_whether_someone_watches_come_through(self) -> None:
        context = AgentContext.from_chat_state({
            "client_name": "pipeshub-ai", "agent_key": "agent-1", "is_assistant_chat": False,
            "can_edit_agent": True, "chat_streaming": False,
        })
        assert (context.client_name, context.agent_key, context.is_assistant, context.can_edit_agent, context.chat_streaming) == (
            "pipeshub-ai", "agent-1", False, True, False,
        )

    def test_the_defaults_are_the_strict_ones(self) -> None:
        context = AgentContext.from_chat_state({})
        assert (context.client_name, context.agent_key, context.can_edit_agent, context.tool_approval) == (None, None, False, None)


def _adapter(name: str = "create_issue", annotations: dict[str, Any] | None = None) -> MCPToolAdapter:
    server = ResolvedMCPServer(
        instance_id="inst-1", name="Jira", display_name="Jira", instance={}, auth={}, owner_id="u", attached_tools=None,
    )
    info = MCPToolInfo(name=name, namespaced_name=f"mcp_jira_{name.replace('/', '_')}", annotations=annotations)
    return MCPToolAdapter(server, info, session_manager=None)  # type: ignore[arg-type]


class TestTheAdapter:
    def test_its_identity_doesnt_depend_on_the_namespace(self) -> None:
        adapter = _adapter()
        assert (adapter.instance_id, adapter.raw_name) == ("inst-1", "create_issue")
        assert mcp_tool_identity(adapter.path) == ("inst-1", "create_issue")

    def test_a_tool_name_with_a_slash_keeps_it(self) -> None:
        assert mcp_tool_identity(_adapter("issues/create").path) == ("inst-1", "issues/create")

    def test_other_paths_have_no_identity(self) -> None:
        assert mcp_tool_identity("/toolsets/jira/create_issue") is None
        assert mcp_tool_identity("/mcp/") is None

    def test_the_kind_comes_from_the_servers_own_tool_name_and_hints(self) -> None:
        assert _adapter(annotations={"readOnlyHint": True}).kind == "read"
        assert _adapter(annotations={"readOnlyHint": False}).kind == "write"
        assert _adapter(annotations={"readOnlyHint": "yes"}).kind == "write"
        assert _adapter().kind == "write"
        assert _adapter("delete_issue", annotations={"readOnlyHint": True}).kind == "destructive"


class TestAnApprovalTurnAlwaysRuns:
    async def _goal(self, **context_overrides: Any) -> list[Any]:  # noqa: ANN401
        from app.agents.agent_loop.intent import IntentRouteDecision
        from app.agents.agent_loop.router import select_loop_and_goal

        question = AskUserQuestionItemInput.model_construct(question="Which project?", options=[], multiSelect=False)
        decision = IntentRouteDecision.model_construct(
            reasoning="", rewritten_query="Allow once: create_issue on Jira", requirements=[], success_criteria=[],
            gaps=[], clarifying_questions=[question], route=None, whole_document=False,
        )
        with patch("app.agents.agent_loop.router.parse_intent_and_route", new=AsyncMock(return_value=decision)):
            _, _, questions, _ = await select_loop_and_goal(
                chat_mode="react", query="Allow once: create_issue on Jira", llm=MagicMock(),
                context=make_context(**context_overrides),
            )
        return questions

    async def test_its_clarifying_questions_are_dropped(self) -> None:
        answer = ToolApprovalAnswer(approval_id="ap-1", decision="allow_once")
        assert await self._goal(tool_approval=answer) == []

    async def test_another_turn_keeps_them(self) -> None:
        assert len(await self._goal()) == 1
