"""Tests for `app.agents.agent_loop.hooks.completion_gate` — the POST_MODEL
middleware that recovers from empty model responses (no text, no tool
calls). See the module docstring for the full rationale."""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.completion_gate import completion_gate
from tests.unit.agents.adapter.support.hook_helpers import run_post_model


def _make_context(**overrides) -> AgentContext:
    defaults: dict = {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}
    defaults.update(overrides)
    return AgentContext(**defaults)


def _turn_scope(tool_names: list[str]) -> TurnScope:
    spec = AgentSpec(
        name="agent-under-test", system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name="agent-under-test", model="m"),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run_scope, turn_index=0)


class TestCompletionGate:
    async def test_noop_when_tool_calls_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=""),
            tool_calls=[ToolCall(id="1", name="run_code", arguments={})],
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_nudges_on_empty_response(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""), scope=_turn_scope([]))
        assert ctx.recovery_message is not None
        assert isinstance(ctx.recovery_message, UserMessage)
        assert ctx.recovery_message.injected is True
        assert context.completion_gate_nudges == 1

    async def test_no_nudge_when_text_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer is 42."), scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_bounded_by_max_nudges(self) -> None:
        context = _make_context()
        gate = completion_gate(context, max_nudges=1)
        scope = _turn_scope(["run_code"])

        first = await run_post_model(gate, AssistantMessage(content=""), scope=scope)
        second = await run_post_model(gate, AssistantMessage(content=""), scope=scope)

        assert first.recovery_message is not None
        assert second.recovery_message is None

    async def test_skips_truncated_response(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        message = AssistantMessage(content="", truncated=True)
        ctx = await run_post_model(gate, message, scope=_turn_scope(["run_code"]))
        assert ctx.recovery_message is None

    async def test_works_without_a_scope(self) -> None:
        """Empty response nudge fires even without a TurnScope."""
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""))
        assert ctx.recovery_message is not None

    async def test_no_nudge_without_scope_when_text_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content="some text, no tool call"))
        assert ctx.recovery_message is None
