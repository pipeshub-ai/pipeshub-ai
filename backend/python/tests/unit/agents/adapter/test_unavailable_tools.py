"""Attached tools the user has not connected: the run goes on without them, and the saved answer names the ones
it called (their stubs, `test_not_connected_tools.py`) for the chat to offer a fix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.not_connected_tools import NEEDED_TOOLS_STATE_KEY
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.protocol.stream_collector import StreamOutcome
from app.agents.agent_loop.respond import AnswerFinalizer
from tests.unit.agents.adapter.conftest import make_context

_GMAIL = {
    "kind": "toolset", "instanceId": "i-gmail", "displayName": "Gmail", "name": "gmail",
    "problem": "not_configured", "authMode": None, "sharedCredential": False, "userCanConnect": True,
}
_CALENDAR = {**_GMAIL, "instanceId": "i-cal", "displayName": "Calendar", "name": "calendar"}
_SHARED_MCP = {
    "kind": "mcp", "instanceId": "i-wiki", "displayName": "Wiki", "name": "wiki", "problem": "not_authenticated",
    "authMode": "api_token", "sharedCredential": True, "userCanConnect": False,
}


def _blocks(unavailable: list[dict] | None) -> tuple[str, str]:
    context = make_context()
    context.tool_state["unavailable_tools"] = unavailable
    spec = AgentSpec(
        name="pipeshub-agent", system_prompt="BASE", tool_names=[],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )
    patches = {
        "_build_knowledge_context": MagicMock(return_value=""),
        "build_llm_time_context": MagicMock(return_value="Current time: 2026-01-01T00:00:00Z"),
        "build_capability_summary": MagicMock(return_value=""),
        "_format_user_context": MagicMock(return_value=""),
    }
    with patch.multiple("app.agents.agent_loop.prompt_builder", **patches), \
         patch("app.modules.agents.context.tool_surface.sandbox_network_enabled", MagicMock(return_value=False)):
        return PipesHubPromptBuilder(context).build_blocks(
            spec, AgentRuntime(tool_registry=ToolRegistry()), Goal(description="do you like coffee?"), [], {},
        )


class TestPromptNote:
    def test_the_prompt_names_no_unconnected_tool(self) -> None:
        # Each one is a stub tool of its own now (`not_connected_tools`): the model needs no list, and nothing
        # tells it to name a tool so the chat can guess which one the answer needed.
        stable, volatile = _blocks([_GMAIL, _CALENDAR, _SHARED_MCP])
        assert "Tools that are not connected" not in stable + volatile
        assert "Gmail" not in stable + volatile and "Wiki" not in stable + volatile

    def test_left_out_when_every_tool_is_connected(self) -> None:
        stable, volatile = _blocks([])
        assert "Tools that are not connected" not in stable + volatile


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def write(self, event: dict) -> bool:
        self.events.append(event)
        return True


class TestSavedAnswer:
    async def test_an_answer_that_called_no_stub_names_no_tool(self) -> None:
        context = make_context()
        context.tool_state["unavailable_tools"] = [_GMAIL]
        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output="I like coffee.", streamed_answer="I like coffee.",
            event_sink=_Sink(),
        )
        assert "unavailableTools" not in result

    async def test_an_error_answer_names_the_tools_it_called(self) -> None:
        context = make_context()
        context.tool_state["unavailable_tools"] = [_GMAIL, _CALENDAR]
        context.tool_state[NEEDED_TOOLS_STATE_KEY] = [_GMAIL]
        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=False, agent_error="boom", agent_output=None, event_sink=_Sink(),
        )
        assert result["unavailableTools"] == [_GMAIL]

    async def test_nothing_missing_leaves_the_field_out(self) -> None:
        context = make_context()
        result = await AnswerFinalizer(context, CitationCollector(context)).run(
            agent_success=True, agent_error=None, agent_output="Yes.", streamed_answer="Yes.", event_sink=_Sink(),
        )
        assert "unavailableTools" not in result


class TestNonStreamingError:
    def test_details_reach_the_caller(self) -> None:
        details = {"agentId": "sa", "serviceAccount": True, "items": [_GMAIL], "servers": []}
        response = StreamOutcome(
            error={"message": "Ask an admin", "code": "toolset_config_missing", "details": details},
        ).to_response()
        assert response.status_code == 424
        assert b'"details"' in response.body and b'"i-gmail"' in response.body

    def test_an_error_without_details_is_unchanged(self) -> None:
        response = StreamOutcome(error={"message": "busy", "code": "rate_limit"}).to_response()
        assert b"details" not in response.body
