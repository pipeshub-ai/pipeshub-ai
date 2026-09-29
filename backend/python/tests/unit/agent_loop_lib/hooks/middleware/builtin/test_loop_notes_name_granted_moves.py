"""Loop notes (truncation, stall, duplicate call, sub-agent rules) name
`task_complete` only when the run was granted it, and the stall note rides
on the latest tool result's loop footer rather than posing as the user
right after tool output -- the shape provider content filters reject."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.finish_moves import can_call, finish_move
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import AgentTurn, Goal, ToolResult
from app.agent_loop_lib.hooks.middleware.builtin.stall_detection import stall_detection
from app.agent_loop_lib.hooks.middleware.builtin.truncation_recovery import (
    default_truncation_recovery,
)
from app.agent_loop_lib.hooks.middleware.context import (
    ModelCallContext,
    ModelResponseContext,
    TurnContext,
)
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.tags import TAG_DEDUP_EXACT
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _Registry:
    def __init__(self, *names: str) -> None:
        self._names = set(names)

    def has(self, name: str) -> bool:
        return name in self._names


def _scope(*, registered: tuple[str, ...], granted: list[str]) -> TurnScope:
    run = RunScope(
        identity=SimpleNamespace(),
        spec=SimpleNamespace(tool_names=granted),
        runtime=SimpleNamespace(tool_registry=_Registry(*registered)),
        goal=Goal(description="g"),
    )
    return TurnScope(run=run, turn_index=3)


_WITH = {"registered": ("task_complete", "search"), "granted": []}
_WITHOUT = {"registered": ("search",), "granted": []}
_UNGRANTED = {"registered": ("task_complete", "search"), "granted": ["search"]}


class TestFinishMove:
    @pytest.mark.parametrize(
        ("grant", "expected"), [(_WITH, True), (_WITHOUT, False), (_UNGRANTED, False)],
    )
    def test_can_call_needs_registration_and_grant(self, grant, expected) -> None:
        assert can_call(_scope(**grant), "task_complete") is expected

    def test_no_scope_means_no_tool(self) -> None:
        assert "task_complete" not in finish_move(None)
        assert "plain text" in finish_move(None)


async def _noop() -> None:
    return None


def _truncated_ctx(scope: TurnScope) -> ModelResponseContext:
    response = AssistantMessage(
        content="", tool_calls=[ToolCall(id="c1", name="search", arguments={})], truncated=True,
    )
    return ModelResponseContext(
        response=response, tool_calls=list(response.tool_calls or []), turn_index=3, scope=scope,
    )


class TestTruncationNote:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("grant", "named"), [(_WITH, True), (_WITHOUT, False), (_UNGRANTED, False)])
    async def test_names_task_complete_only_when_granted(self, grant, named) -> None:
        ctx = _truncated_ctx(_scope(**grant))

        await default_truncation_recovery()(ctx, _noop)

        note = str(ctx.recovery_tool_results[0].content)
        assert ("task_complete" in note) is named
        assert note.startswith("[Tool call not executed") and note.endswith(".]")

    @pytest.mark.asyncio
    async def test_escalated_note_names_run_code_only_when_granted(self) -> None:
        without = default_truncation_recovery()
        with_code = default_truncation_recovery()
        plain = _scope(registered=("search",), granted=[])
        coder = _scope(registered=("search", "run_code"), granted=[])
        for _ in range(3):
            ctx_a, ctx_b = _truncated_ctx(plain), _truncated_ctx(coder)
            await without(ctx_a, _noop)
            await with_code(ctx_b, _noop)

        assert "AGAIN" in str(ctx_a.recovery_tool_results[0].content)
        assert "run_code" not in str(ctx_a.recovery_tool_results[0].content)
        assert "run_code" in str(ctx_b.recovery_tool_results[0].content)


_FOOTER = "\n\n[loop: step 5/20]"


def _history() -> list:
    return [
        UserMessage(content="question"),
        ToolMessage(tool_call_id="c1", content="first", step_footer=_FOOTER),
        ToolMessage(tool_call_id="c2", content="second", step_footer=_FOOTER),
    ]


async def _stalled_messages(scope: TurnScope, *, turns: int, history: list) -> list:
    post_turn, pre_model = stall_detection(warn_after=2, fail_after=4)
    turn = AgentTurn(
        messages=[AssistantMessage(content="")],
        tool_results=[ToolResult(tool_call_id="c", name="search", content="boom", is_error=True)],
    )
    for _ in range(turns):
        await post_turn(TurnContext(turn_index=scope.turn_index, turn=turn, scope=scope), _noop)
    ctx = ModelCallContext(
        messages=list(history), budget=ContextBudget(max_tokens=1000), scope=scope,
        turn_index=5, max_turns=20,
    )
    await pre_model(ctx, _noop)
    return ctx.messages


class TestStallNotes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("turns", [2, 4])
    async def test_rides_on_the_latest_footer_and_adds_no_message(self, turns) -> None:
        history = _history()

        messages = await _stalled_messages(_scope(**_WITHOUT), turns=turns, history=history)

        assert len(messages) == len(history)
        assert not any(isinstance(m, UserMessage) for m in messages[1:])
        assert messages[-2].step_footer == _FOOTER
        note = messages[-1].step_footer.removeprefix(_FOOTER)
        assert note.startswith("\n[loop: ")
        assert "System:" not in note

    @pytest.mark.asyncio
    async def test_stored_history_is_unchanged(self) -> None:
        history = _history()

        await _stalled_messages(_scope(**_WITH), turns=4, history=history)

        assert history[-1].step_footer == _FOOTER

    @pytest.mark.asyncio
    @pytest.mark.parametrize("turns", [2, 4])
    @pytest.mark.parametrize(("grant", "named"), [(_WITH, True), (_WITHOUT, False), (_UNGRANTED, False)])
    async def test_names_task_complete_only_when_granted(self, turns, grant, named) -> None:
        messages = await _stalled_messages(_scope(**grant), turns=turns, history=_history())

        note = messages[-1].step_footer
        assert "[loop: " in note
        assert ("task_complete" in note) is named

    @pytest.mark.asyncio
    async def test_without_a_tool_result_falls_back_to_an_injected_message(self) -> None:
        messages = await _stalled_messages(
            _scope(**_WITHOUT), turns=2, history=[UserMessage(content="q")],
        )

        assert len(messages) == 2
        assert messages[-1].injected is True
        assert "task_complete" not in str(messages[-1].content)


class _DedupSearch(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def short_description(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return "search"

    @property
    def path(self) -> str:
        return "/toolsets/builtin/web_search"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_DEDUP_EXACT]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="query", type=ParameterType.STRING, description="q")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data="hits")


async def _duplicate_note(*, with_task_complete: bool) -> str:
    registry = ToolRegistry()
    registry.register_tool(_DedupSearch())
    if with_task_complete:
        registry.register_tool(TaskCompleteTool())
    transport = ScriptedTransport()
    transport.add_tool_calls([
        ToolCall(id="a", name="web_search", arguments={"query": "same"}),
        ToolCall(id="b", name="web_search", arguments={"query": "same"}),
    ])
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    agent = Agent(
        AgentSpec(
            name="dup", system_prompt="s",
            model=ModelSpec(provider="scripted", model="scripted-model"), max_turns=3,
        ),
        AgentRuntime(transport_registry=transport_registry, tool_registry=registry),
    )
    result = await agent.run(Goal(description="search twice"))
    return next(
        str(tr.content) for tr in result.turns[0].tool_results
        if "Duplicate call skipped" in str(tr.content)
    )


class TestDuplicateCallNote:
    @pytest.mark.asyncio
    async def test_without_task_complete_it_asks_for_plain_text(self) -> None:
        note = await _duplicate_note(with_task_complete=False)

        assert "task_complete" not in note
        assert "plain text" in note

    @pytest.mark.asyncio
    async def test_with_task_complete_it_names_it(self) -> None:
        assert "call task_complete" in await _duplicate_note(with_task_complete=True)


class TestSubAgentPrompt:
    def test_artifact_rules_only_for_a_child_granted_task_complete(self) -> None:
        without = build_sub_agent_prompt("jira", None, tool_names=["jira__search"])
        granted = build_sub_agent_prompt("jira", None, tool_names=["jira__search", "task_complete"])

        assert "task_complete" not in without
        assert "task_complete(" in granted

    def test_default_grant_names_no_tool(self) -> None:
        assert "task_complete" not in build_sub_agent_prompt("web", None)
