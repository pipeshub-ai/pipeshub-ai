"""End-to-end: a `task_complete` call carrying the typed output contract
(`confidence`/`record_ids`/`needs_input`, p4-output-schema) must reach the
run's own `AgentResult` — the full plumbing chain `TaskCompleteTool.
extract_outcome` -> `ToolCallOutcome` (`agent/tool_loop.py`) -> `Agent.
step()` -> `Agent.succeed()` -> `AgentResult` (`core/types.py`), not just
the tool's own parsing (see `test_task_complete.py` for that in isolation).
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Confidence, Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _build_agent(transport: ScriptedTransport) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="worker",
        system_prompt="You complete tasks.",
        tool_names=["task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=5,
    )
    return Agent(spec, runtime)


class TestTaskCompleteOutputContractReachesAgentResult:
    async def test_confidence_record_ids_and_needs_input_all_reach_agent_result(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete",
            arguments={
                "output": "Found 2 of the 3 requested tickets",
                "confidence": "medium",
                "record_ids": ["JIRA-1", "JIRA-2"],
                "needs_input": "the sprint name for the third ticket",
            },
        ))
        agent = _build_agent(transport)
        result = await agent.run(Goal(description="fetch tickets"))

        assert result.success is True
        assert result.output == "Found 2 of the 3 requested tickets"
        assert result.confidence is Confidence.MEDIUM
        assert result.record_ids == ["JIRA-1", "JIRA-2"]
        assert result.needs_input == "the sprint name for the third ticket"

    async def test_omitted_fields_default_to_empty_on_agent_result(self) -> None:
        """Back-compat: a plain task_complete call with none of the new
        fields must not populate them with anything but the same defaults
        every pre-existing caller already gets."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-done", name="task_complete", arguments={"output": "done"},
        ))
        agent = _build_agent(transport)
        result = await agent.run(Goal(description="do the thing"))

        assert result.success is True
        assert result.confidence is None
        assert result.record_ids == []
        assert result.needs_input is None
