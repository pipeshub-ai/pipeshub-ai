"""Phase 2 proof: "an agent run executes with no HTTP request and leaves a
resumable checkpoint" (the plan's Phase 2 deliverable, Part I).

No FastAPI route, no `ChatState`, no `AgentContext.from_chat_state()` — the
`Agent`/`AgentRuntime` pair here is built the same way
`TaskSpecAssembler.assemble()` builds one (`AgentSpec` + a `ToolRegistry` +
a `GraphCheckpointStore`/`GraphTimelineStore` pair), just with a
`ScriptedTransport` standing in for a real LLM so the test is deterministic
and network-free (same pattern `test_task_complete_output_contract.py`
uses for the chat-adapter test suite).

The crash/resume half simulates a process restart by throwing away the
first `Agent`/`AgentRuntime`/`ToolRegistry`/`TransportRegistry` entirely and
building a SECOND set that only shares the same `GraphCheckpointStore`
backing store (a fresh instance pointed at the same fake graph, mirroring
"the graph database survived, the Query service process did not") —
resuming from a `checkpoint_id` a completely different process instance
saved is exactly the durability guarantee Part C2/Part L call out as
previously unproven ("first production use of checkpoint/resume code").
"""
from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.stores.checkpoint.base import CheckpointKind
from app.agent_loop_lib.modules.stores.checkpoint.graph_store import (
    GraphCheckpointStore,
)
from app.agent_loop_lib.modules.stores.timeline.graph_store import GraphTimelineStore
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agent_loop_lib.modules.stores.fakes import FakeGraphProvider
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _FetchTicketsTool(Tool):
    """Stand-in for a real connector tool -- the point is just that it's a
    tool call the `post_tool` checkpoint fires after (see
    `agent/__init__.py`'s `step()`), not what it does."""

    @property
    def name(self) -> str:
        return "fetch_tickets"

    @property
    def short_description(self) -> str:
        return "Fetch yesterday's support tickets."

    @property
    def description(self) -> str:
        return "Fetch yesterday's support tickets."

    @property
    def path(self) -> str:
        return "/toolsets/test/fetch_tickets"

    @property
    def parameters(self) -> list:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data={"tickets": ["JIRA-1", "JIRA-2"]})


def _build_agent(
    transport: ScriptedTransport, *, checkpoint_store, timeline_store=None, max_turns: int = 5,
) -> Agent:
    """Mirrors `TaskSpecAssembler.build_agent_spec`/`build_runtime` closely
    enough to exercise the same wiring, without needing a real
    `ConfigurationService`/`IGraphDBProvider`-backed `PipesHubToolLoader`
    round trip -- that full assembly path is covered by
    `test_spec_assembler.py`'s pure-method tests instead."""
    tool_registry = ToolRegistry()
    tool_registry.register_tool(_FetchTicketsTool())
    tool_registry.register_tool(TaskCompleteTool())

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=tool_registry,
        checkpoint_store=checkpoint_store,
        timeline_store=timeline_store,
    )
    spec = AgentSpec(
        name="task:daily-digest",
        system_prompt="You are an autonomous task-execution agent.",
        tool_names=["fetch_tickets", "task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime, session_id="task-1")


class TestHeadlessExecutionLeavesAResumableCheckpoint:
    async def test_run_completes_headlessly_and_saves_checkpoints(self) -> None:
        graph = FakeGraphProvider()
        checkpoint_store = GraphCheckpointStore(graph, org_id="org-1")
        timeline_store = GraphTimelineStore(graph, org_id="org-1")

        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="c1", name="fetch_tickets", arguments={}))
        transport.add_tool_call(ToolCall(
            id="c2", name="task_complete",
            arguments={"output": "Posted digest to #support", "confidence": "high"},
        ))

        agent = _build_agent(transport, checkpoint_store=checkpoint_store, timeline_store=timeline_store)
        result = await agent.run(Goal(description="summarize yesterday's tickets"))

        assert result.success is True
        assert result.output == "Posted digest to #support"

        # No HTTP request, no event_sink, no ChatState anywhere above --
        # yet a durable checkpoint trail exists purely from
        # `runtime.checkpoint_store` being set (see
        # `agent/observability.py::save_checkpoint`, called internally by
        # `Agent.step()`/`Agent.succeed()`, never by this test directly).
        history = await checkpoint_store.history(agent.run_ctx.run_id)
        assert len(history) >= 2
        assert history[-1].kind == CheckpointKind.AGENT_COMPLETE

        timeline = await timeline_store.get_by_run(agent.run_ctx.run_id)
        assert len(timeline) >= 1

    async def test_resume_after_simulated_process_restart(self) -> None:
        graph = FakeGraphProvider()
        checkpoint_store = GraphCheckpointStore(graph, org_id="org-1")

        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="c1", name="fetch_tickets", arguments={}))
        # Deliberately no second scripted step, and `max_turns=1` on the
        # spec: the "crash" happens right after the single turn's
        # post_tool checkpoint, before task_complete is ever called on
        # this Agent instance (`ScriptedTransport` would otherwise happily
        # fall back to a "done" text message once its script is exhausted,
        # masking the scenario this test needs -- an interrupted run).
        agent = _build_agent(transport, checkpoint_store=checkpoint_store, max_turns=1)
        await agent.run(Goal(description="summarize yesterday's tickets"))
        run_id = agent.run_ctx.run_id

        latest = await checkpoint_store.latest(run_id)
        assert latest is not None
        assert latest.kind == CheckpointKind.POST_TOOL

        # Simulate a process restart: throw away every in-memory object
        # except the checkpoint's own id and a FRESH store instance bound
        # to the SAME underlying fake graph (standing in for "the database
        # survived the restart, the process didn't").
        restarted_checkpoint_store = GraphCheckpointStore(graph, org_id="org-1")
        transport2 = ScriptedTransport()
        transport2.add_tool_call(ToolCall(
            id="c2", name="task_complete",
            arguments={"output": "Posted digest to #support", "confidence": "high"},
        ))
        resumed_agent = _build_agent(transport2, checkpoint_store=restarted_checkpoint_store)

        result = await resumed_agent.resume(latest.checkpoint_id)

        assert result.success is True
        assert result.output == "Posted digest to #support"
        # Resume continues the SAME run identity, not a new one.
        assert resumed_agent.run_ctx.run_id == run_id
