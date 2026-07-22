"""Tests for `app.agents.agent_loop.hooks.knowledge_first_gate` — the
POST_MODEL middleware that stops the model from answering an informational
question from training data when an internal-search surface is actually
available, plus the POST_TOOL_USE tracker that flips
`AgentContext.internal_search_attempted` once that surface has been used.
See the module docstring for the full rationale."""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.knowledge_first_gate import (
    internal_search_attempted_tracking,
    knowledge_first_gate,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model, run_post_tool


def _make_context(**overrides) -> AgentContext:
    defaults: dict = {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}
    defaults.update(overrides)
    return AgentContext(**defaults)


def _turn_scope(tool_names: list[str], *, spec_name: str = "agent-under-test") -> TurnScope:
    spec = AgentSpec(
        name=spec_name, system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name=spec_name, model="m"),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run_scope, turn_index=0)


class TestKnowledgeFirstGate:
    async def test_noop_when_tool_calls_present(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=""),
            tool_calls=[ToolCall(id="1", name="internal_exploration_agent", arguments={})],
            scope=_turn_scope(["internal_exploration_agent"]),
        )
        assert ctx.recovery_message is None

    async def test_noop_on_truncated_response(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context)
        message = AssistantMessage(content="The answer is 42.", truncated=True)
        ctx = await run_post_model(gate, message, scope=_turn_scope(["internal_exploration_agent"]))
        assert ctx.recovery_message is None

    async def test_conservative_without_a_scope(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content="some text, no tool call"))
        assert ctx.recovery_message is None

    async def test_noop_when_agent_has_neither_search_surface(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The capital of France is Paris."),
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_never_nudges_the_internal_exploration_agent_itself(self) -> None:
        """The child agent's OWN `tool_names` contains the flat
        `retrieval_search_internal_knowledge` tool it was built to claim
        (see `domain_agents.py`) — nudging it to "delegate to
        `internal_exploration_agent`" would be circular. Excluded by
        `AgentSpec.name`, not tool presence."""
        context = _make_context()
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="Found nothing relevant in the indexed sources."),
            scope=_turn_scope(
                ["retrieval_search_internal_knowledge", "knowledgehub_list_files"],
                spec_name="internal_exploration_agent",
            ),
        )
        assert ctx.recovery_message is None

    async def test_nudges_when_composed_delegate_available_and_unattempted(self) -> None:
        context = _make_context(internal_search_attempted=False)
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The capital of France is Paris."),
            scope=_turn_scope(["internal_exploration_agent", "web_agent"]),
        )
        assert ctx.recovery_message is not None
        assert "internal_exploration_agent" in ctx.recovery_message.content
        assert context.knowledge_first_nudges == 1

    async def test_nudges_referencing_flat_tool_when_composition_is_off(self) -> None:
        context = _make_context(internal_search_attempted=False)
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The capital of France is Paris."),
            scope=_turn_scope(["retrieval_search_internal_knowledge"]),
        )
        assert ctx.recovery_message is not None
        assert "retrieval_search_internal_knowledge" in ctx.recovery_message.content

    async def test_no_nudge_once_internal_search_already_attempted(self) -> None:
        context = _make_context(internal_search_attempted=True)
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="Based on the search, the answer is X."),
            scope=_turn_scope(["internal_exploration_agent"]),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_on_empty_response_leaves_it_to_completion_gate(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""), scope=_turn_scope(["internal_exploration_agent"]))
        assert ctx.recovery_message is None
        assert context.knowledge_first_nudges == 0

    async def test_bounded_by_max_nudges(self) -> None:
        context = _make_context()
        gate = knowledge_first_gate(context, max_nudges=1)
        scope = _turn_scope(["internal_exploration_agent"])

        first = await run_post_model(gate, AssistantMessage(content="Paris is the capital."), scope=scope)
        second = await run_post_model(gate, AssistantMessage(content="Paris, again."), scope=scope)

        assert first.recovery_message is not None
        assert second.recovery_message is None


class TestInternalSearchAttemptedTracking:
    async def test_flips_flag_on_composed_delegate_call(self) -> None:
        # No `scope` -> `resolve_tool_name` falls back to the raw
        # `tool_path` (see its docstring) — a bare, slash-free path is the
        # simplest way to pin the fallback-resolved name in a unit test
        # without standing up a real `ToolRegistry`.
        context = _make_context()
        tracker = internal_search_attempted_tracking(context)
        await run_post_tool(
            tracker, ToolOutput(success=True, data="findings"),
            tool_path="internal_exploration_agent",
        )
        assert context.internal_search_attempted is True

    async def test_flips_flag_on_flat_retrieval_tool_call(self) -> None:
        context = _make_context()
        tracker = internal_search_attempted_tracking(context)
        await run_post_tool(
            tracker, ToolOutput(success=True, data=[]),
            tool_path="retrieval_search_internal_knowledge",
        )
        assert context.internal_search_attempted is True

    async def test_flips_flag_even_on_a_failed_search(self) -> None:
        """A failed/empty search is still an attempt, not a skip — the
        gate cares about whether search was TRIED, not whether it
        succeeded."""
        context = _make_context()
        tracker = internal_search_attempted_tracking(context)
        await run_post_tool(
            tracker, ToolOutput(success=False, error="timeout"),
            tool_path="internal_exploration_agent",
        )
        assert context.internal_search_attempted is True

    async def test_unrelated_tool_call_leaves_flag_unset(self) -> None:
        context = _make_context()
        tracker = internal_search_attempted_tracking(context)
        await run_post_tool(
            tracker, ToolOutput(success=True, data="7"),
            tool_path="calculator_agent",
        )
        assert context.internal_search_attempted is False
