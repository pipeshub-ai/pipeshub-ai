"""HG-03: a live tool-call id never repeats one already in the conversation.

Transports that number calls per response (`call_{i}` when the provider sent
no id, Ollama's `call_{i}`, LangChain's empty string) hand out `call_0` on
every turn. Replayed history already holds `call_0` from an earlier turn, so
the request carried two calls with one id: providers reject that ("duplicate
tool_use id") or bind the new result to the old call.
"""

from __future__ import annotations

from collections import Counter

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.context.manager import ContextManager
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _Note(Tool):
    @property
    def name(self) -> str:
        return "note"

    @property
    def short_description(self) -> str:
        return "Takes a note"

    @property
    def description(self) -> str:
        return "Takes a note"

    @property
    def path(self) -> str:
        return "/toolsets/test/note"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="text", type=ParameterType.STRING, description="note")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=f"noted {kwargs['text']}")


async def _run_after_history(live_ids: list[str]) -> list:
    """Run one turn on top of a replayed turn that used `call_0`, with the
    model's next reply calling `note` under `live_ids`; return what the
    model was sent on the call after the tools ran."""
    transport = ScriptedTransport().add_tool_calls(
        [ToolCall(id=cid, name="note", arguments={"text": f"t{i}"}) for i, cid in enumerate(live_ids)]
    ).add_text("done")
    registry = ToolRegistry()
    registry.register_tool(_Note())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    agent = Agent(
        AgentSpec(
            name="ids", system_prompt="You help.", model=ModelSpec(provider="scripted", model="m"),
            loop=ReActLoop(), max_turns=4,
        ),
        AgentRuntime(transport_registry=transports, tool_registry=registry),
    )
    history = ContextManager()
    for msg in (
        UserMessage(content="first question"),
        AssistantMessage(content=[], tool_calls=[ToolCall(id="call_0", name="note", arguments={"text": "old"})]),
        ToolMessage(content="noted old", tool_call_id="call_0"),
        AssistantMessage(content="first answer"),
    ):
        await history.add(msg)
    agent.seed_context(history)

    await agent.run(Goal(description="second question"))

    return transport.calls[-1]["messages"]


def _assert_paired_and_unique(messages: list) -> None:
    call_ids = [c.id for m in messages if isinstance(m, AssistantMessage) for c in m.tool_calls or ()]
    result_ids = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
    assert all(call_ids), call_ids
    assert Counter(call_ids).most_common(1)[0][1] == 1, call_ids
    assert sorted(call_ids) == sorted(result_ids)


class TestLiveToolCallIds:
    async def test_a_live_call_id_already_in_history_is_renamed(self) -> None:
        messages = await _run_after_history(["call_0"])

        _assert_paired_and_unique(messages)
        results = {m.tool_call_id: m.content for m in messages if isinstance(m, ToolMessage)}
        assert results["call_0"] == "noted old"

    async def test_empty_ids_in_one_reply_get_distinct_ids(self) -> None:
        messages = await _run_after_history(["", ""])

        _assert_paired_and_unique(messages)
        contents = sorted(m.content for m in messages if isinstance(m, ToolMessage))
        assert contents == ["noted old", "noted t0", "noted t1"]

    async def test_a_fresh_id_is_kept(self) -> None:
        messages = await _run_after_history(["toolu_abc"])

        _assert_paired_and_unique(messages)
        assert "toolu_abc" in {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
