"""Pausing for a person's answer and picking the run up again later, plus the
records a run leaves behind (state, timeline, checkpoints, memory).

The pause/resume flow is what lets an agent ask "which quarter did you
mean?" and continue once someone answers, even after the process that asked
has gone away: the question is checkpointed, a fresh `Agent` resumes from
that checkpoint with the answer, and the run keeps its identity. Rollback
restarts from an earlier checkpoint as a separate branch without touching
the original.

Real `Agent`, `AgentRuntime`, in-memory stores and the production `clarify`
tool; only the LLM is scripted.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import AgentError
from app.agent_loop_lib.core.messages import ToolCall, ToolMessage
from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.providers.budget.tracker import BudgetTracker
from app.agent_loop_lib.modules.providers.memory.in_memory import InMemoryProvider
from app.agent_loop_lib.modules.stores.checkpoint.base import CheckpointKind
from app.agent_loop_lib.modules.stores.checkpoint.in_memory import (
    InMemoryCheckpointStore,
)
from app.agent_loop_lib.modules.stores.hil.base import HILResponse
from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore
from app.agent_loop_lib.modules.stores.state.in_memory import InMemoryStateStore
from app.agent_loop_lib.modules.stores.timeline.in_memory import InMemoryTimelineStore
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.clarify import ClarifyTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_GOAL = Goal(description="Summarise sales")


class _CountTool(Tool):
    @property
    def name(self) -> str:
        return "count"

    @property
    def short_description(self) -> str:
        return "Counts rows"

    @property
    def description(self) -> str:
        return "Counts rows"

    @property
    def path(self) -> str:
        return "/toolsets/test/count"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="table", type=ParameterType.STRING, description="table")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data={"table": kwargs["table"], "rows": 42})


def _runtime(transport: ScriptedTransport, **stores: object) -> AgentRuntime:
    """One runtime per "process": the durable stores are shared by passing
    the same objects in; the model connection is new each time."""
    registry = ToolRegistry()
    registry.register_tool(ClarifyTool())
    registry.register_tool(_CountTool())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    return AgentRuntime(transport_registry=transports, tool_registry=registry, **stores)


def _agent(runtime: AgentRuntime, *, max_turns: int = 4) -> Agent:
    spec = AgentSpec(
        name="resume-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime, session_id="session-1")


async def _pause_on_a_question(hil: InMemoryHILStore, **stores: object) -> Agent:
    """Runs until the agent is waiting on `clarify`, then abandons it, the
    way a restarted process would."""
    transport = ScriptedTransport().add_tool_call(ToolCall(
        id="ask-1", name="clarify", arguments={"question": "Which quarter?", "context": "two are on file"},
    ))
    agent = _agent(_runtime(transport, hil_store=hil, **stores))
    task = asyncio.create_task(agent.run(_GOAL))
    for _ in range(200):
        if await hil.list_pending():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return agent


class TestResumeAfterAQuestion:
    async def test_fresh_agent_continues_the_same_run_with_the_answer(self) -> None:
        hil = InMemoryHILStore()
        checkpoints = InMemoryCheckpointStore()
        paused = await _pause_on_a_question(hil, checkpoint_store=checkpoints)

        [request] = await hil.list_pending()
        assert request.question == "Which quarter?"
        assert request.context == {"note": "two are on file"}
        pause = await checkpoints.latest(paused.run_ctx.run_id)
        assert pause.kind == CheckpointKind.HIL_PAUSE
        assert pause.pending_tool_call_id == "ask-1"

        transport = ScriptedTransport().add_text("Q3 sales were up 4%.")
        resumed = _agent(_runtime(transport, hil_store=hil, checkpoint_store=checkpoints))
        result = await resumed.resume(pause.checkpoint_id, hil_responses={request.request_id: "Q3"})

        assert result.success is True
        assert result.output == "Q3 sales were up 4%."
        assert resumed.run_ctx.run_id == paused.run_ctx.run_id
        answers = [m for m in transport.calls[0]["messages"] if isinstance(m, ToolMessage)]
        assert answers[-1].tool_call_id == "ask-1"
        assert '"answer": "Q3"' in answers[-1].content

    async def test_resume_thread_picks_the_latest_checkpoint_for_the_run(self) -> None:
        hil = InMemoryHILStore()
        checkpoints = InMemoryCheckpointStore()
        paused = await _pause_on_a_question(hil, checkpoint_store=checkpoints)
        [request] = await hil.list_pending()

        transport = ScriptedTransport().add_text("Done.")
        result = await _agent(_runtime(transport, checkpoint_store=checkpoints)).resume_thread(
            paused.run_ctx.run_id, hil_responses={request.request_id: "Q4"},
        )

        assert result.output == "Done."
        answers = [m for m in transport.calls[0]["messages"] if isinstance(m, ToolMessage)]
        assert '"answer": "Q4"' in answers[-1].content

    async def test_resume_starts_at_the_turn_after_the_pause(self) -> None:
        hil = InMemoryHILStore()
        checkpoints = InMemoryCheckpointStore()
        paused = await _pause_on_a_question(hil, checkpoint_store=checkpoints)
        pause = await checkpoints.latest(paused.run_ctx.run_id)

        transport = ScriptedTransport().add_tool_call(ToolCall(id="c", name="count", arguments={"table": "t"}))
        resumed = _agent(_runtime(transport, checkpoint_store=checkpoints), max_turns=2)
        result = await resumed.resume(pause.checkpoint_id)

        assert resumed.start_turn_index == pause.turn_index + 1
        assert len(transport.calls) == 1
        assert result.success is False
        assert result.error == "Exceeded max_turns=2"

    async def test_asking_without_a_question_store_is_a_tool_error(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="ask", name="clarify", arguments={"question": "Which?"}),
        ).add_text("Going with the default.")
        agent = _agent(_runtime(transport))

        result = await agent.run(_GOAL)

        clarify_result = agent.scope.turns[0].tool_results[0]
        assert clarify_result.is_error is True
        assert "cannot pause for clarification" in clarify_result.content
        assert result.output == "Going with the default."

    async def test_answered_question_flows_back_into_the_same_run(self) -> None:
        hil = InMemoryHILStore()
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="ask", name="clarify", arguments={"question": "Which?"}),
        ).add_text("Using Q2.")
        agent = _agent(_runtime(transport, hil_store=hil))
        task = asyncio.create_task(agent.run(_GOAL))
        pending = []
        for _ in range(200):
            pending = await hil.list_pending()
            if pending:
                break
            await asyncio.sleep(0.01)
        await hil.respond(HILResponse(request_id=pending[0].request_id, approved=False))

        result = await task

        assert agent.scope.turns[0].tool_results[0].content == {"answer": "denied", "approved": False}
        assert result.output == "Using Q2."


class TestResumeAndRollbackNeedACheckpointStore:
    @pytest.mark.parametrize(("call", "args"), [
        ("resume", ("cp",)), ("resume_thread", ("run",)), ("rollback", ("run", 0)),
    ])
    async def test_without_a_store_the_error_says_so(self, call: str, args: tuple) -> None:
        agent = _agent(_runtime(ScriptedTransport()))

        with pytest.raises(AgentError, match="no checkpoint_store configured"):
            await getattr(agent, call)(*args)

    async def test_unknown_thread_is_reported(self) -> None:
        agent = _agent(_runtime(ScriptedTransport(), checkpoint_store=InMemoryCheckpointStore()))

        with pytest.raises(AgentError, match="No checkpoint found for thread_id='nope'"):
            await agent.resume_thread("nope")


async def _finished_run(checkpoints: InMemoryCheckpointStore, budget: BudgetTracker) -> str:
    usage = TokenUsage(input_tokens=100, output_tokens=10)
    transport = (
        ScriptedTransport()
        .add_tool_call(ToolCall(id="c1", name="count", arguments={"table": "q1"}), usage=usage)
        .add_tool_call(ToolCall(id="c2", name="count", arguments={"table": "q2"}), usage=usage)
        .add_text("Q1 and Q2 both have 42 rows.", usage=usage)
    )
    agent = _agent(_runtime(transport, checkpoint_store=checkpoints, budget=budget))
    await agent.run(_GOAL)
    return agent.run_ctx.run_id


class TestRollback:
    async def test_rollback_branches_from_the_chosen_turn_and_keeps_the_original(self) -> None:
        checkpoints = InMemoryCheckpointStore()
        run_id = await _finished_run(checkpoints, BudgetTracker())
        original = await checkpoints.history(run_id)
        assert [(cp.kind, cp.turn_index) for cp in original] == [
            (CheckpointKind.PRE_TOOL, 0), (CheckpointKind.POST_TOOL, 0),
            (CheckpointKind.PRE_TOOL, 1), (CheckpointKind.POST_TOOL, 1),
            (CheckpointKind.AGENT_COMPLETE, 2),
        ]

        transport = ScriptedTransport().add_text("Only Q1: 42 rows.")
        branch_agent = _agent(_runtime(transport, checkpoint_store=checkpoints))
        result = await branch_agent.rollback(run_id, turn_index=0)

        assert result.output == "Only Q1: 42 rows."
        branch_id = branch_agent.run_ctx.run_id
        assert branch_id != run_id
        seed = (await checkpoints.history(branch_id))[0]
        assert seed.metadata["branched_from_run_id"] == run_id
        assert seed.metadata["branched_from_checkpoint_id"] == original[1].checkpoint_id
        assert seed.metadata["branched_from_turn"] == 0
        assert await checkpoints.history(run_id) == original
        seen = transport.calls[0]["messages"]
        assert any(isinstance(m, ToolMessage) and m.tool_call_id == "c1" for m in seen)
        assert not any(isinstance(m, ToolMessage) and m.tool_call_id == "c2" for m in seen)

    async def test_rollback_restores_the_budget_to_the_checkpoint(self) -> None:
        checkpoints = InMemoryCheckpointStore()
        budget = BudgetTracker()
        run_id = await _finished_run(checkpoints, budget)
        assert (await budget.snapshot()).input_tokens == 300

        transport = ScriptedTransport().add_text("again", usage=TokenUsage(input_tokens=5))
        await _agent(_runtime(transport, checkpoint_store=checkpoints, budget=budget)).rollback(run_id, turn_index=0)

        assert (await budget.snapshot()).input_tokens == 105

    async def test_rollback_before_the_first_checkpoint_is_refused(self) -> None:
        checkpoints = InMemoryCheckpointStore()
        run_id = await _finished_run(checkpoints, BudgetTracker())

        with pytest.raises(AgentError, match="No checkpoint at or before turn -1"):
            await _agent(_runtime(ScriptedTransport(), checkpoint_store=checkpoints)).rollback(run_id, turn_index=-1)


class TestRunRecords:
    async def test_state_timeline_and_memory_follow_the_run(self) -> None:
        transport = (
            ScriptedTransport()
            .add_tool_call(ToolCall(id="c1", name="count", arguments={"table": "orders"}))
            .add_text("Orders has 42 rows.")
        )
        state = InMemoryStateStore()
        timeline = InMemoryTimelineStore()
        memory = InMemoryProvider()
        agent = _agent(_runtime(transport, state_store=state, timeline_store=timeline, memory=memory))

        await agent.run(_GOAL)
        run_id = agent.run_ctx.run_id

        final_state = await state.get(run_id)
        assert final_state.status.value == "completed"
        assert final_state.goal_description == "Summarise sales"
        entries = await timeline.get_by_run(run_id)
        assert [e.sequence_id for e in entries] == list(range(1, len(entries) + 1))
        assert entries[0].event_type == "agent_start"
        assert entries[-1].event_type == "agent_complete"
        remembered = {(r.metadata["type"], r.content) for r in await memory.search("")}
        assert remembered == {
            ("tool_result", 'Tool \'count\' result: {"table": "orders", "rows": 42}'),
            ("assistant_response", "Orders has 42 rows."),
        }
