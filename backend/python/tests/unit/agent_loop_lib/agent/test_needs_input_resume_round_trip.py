"""HIL hardening (task engine plan Part D, Phase 5) round-trip test --
written FIRST, before the fix, per the plan's explicit instruction ("Only
phase that changes shared chat behaviour... write missing round-trip test
first").

`Agent.resume(checkpoint_id, hil_responses={...})` (`agent/resume.py`) only
works if the checkpoint that ended the run carries `hil_request_id` (the key
`hil_responses` is looked up by) AND `pending_tool_call_id` (the tool_use id
the injected answer must be addressed to for the provider to accept it).
Today, `agent/observability.py::save_checkpoint` only ever receives those two
fields from the library's OWN built-in `clarify` tool's special-route
handler (`handle_clarify`). Any OTHER `TAG_LIFECYCLE_TERMINAL` tool that ends
a turn with `AgentResult.needs_input` set -- `task_complete(needs_input=...)`
being the general-purpose one every headless/no-UI agent already has -- gets
NO such checkpoint metadata, because `agent/__init__.py`'s turn loop calls
`save_checkpoint(self, "post_tool", ...)` unconditionally with neither field
set. A later `agent.resume(checkpoint_id, hil_responses={...})` then has no
`checkpoint.hil_request_id` to match against and silently drops the human's
answer instead of injecting it -- exactly the "no correlation ID survives
the round trip" bug from the plan's Part D1, generalized past the one tool
that happened to already get it right.

This is the mechanism the task engine's own `TaskExecutor` depends on for
Part C3 ("Runs needing input terminate with AWAITING_INPUT... Answer arrives
via API/chat, creates new run resuming from checkpoint via
`Agent.resume(hil_responses=...)`") -- `ask_user_question`'s own outcome fix
(see `test_ask_user_question_outcome.py`) sets `needs_input` on the SAME
`AgentResult`/turn-loop path this test drives via `task_complete`, so fixing
the checkpoint plumbing here fixes it for both.
"""
from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.stores.checkpoint.in_memory import InMemoryCheckpointStore
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _build_agent(transport: ScriptedTransport, checkpoint_store) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        checkpoint_store=checkpoint_store,
    )
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=5,
    )
    return Agent(spec, runtime)


class TestNeedsInputCheckpointCarriesCorrelationId:
    async def test_post_tool_checkpoint_after_needs_input_has_hil_correlation_fields(self) -> None:
        """The checkpoint saved for the turn that ends via `needs_input`
        must itself carry enough correlation info for a later `resume()`
        to work -- this is the assertion that fails before the fix."""
        checkpoint_store = InMemoryCheckpointStore()
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="call-1", name="task_complete",
            arguments={"output": "partial progress", "needs_input": "the target sprint name"},
        ))
        agent = _build_agent(transport, checkpoint_store)

        result = await agent.run(Goal(description="file a ticket in the current sprint"))

        assert result.needs_input == "the target sprint name"

        latest = await checkpoint_store.latest(agent.run_ctx.run_id)
        assert latest is not None
        assert latest.hil_request_id is not None, (
            "post_tool checkpoint after a needs_input-terminated turn must carry "
            "a hil_request_id for Agent.resume(hil_responses=...) to key off"
        )
        assert latest.pending_tool_call_id == "call-1", (
            "must carry the ORIGINAL terminal call's tool_use id so the injected "
            "answer can be addressed to it on resume"
        )

    async def test_full_round_trip_answer_reaches_a_fresh_agent_instance(self) -> None:
        """End-to-end: run ends on needs_input, a brand new `Agent` instance
        (simulating the next HTTP request / task dispatch) resumes with the
        human's answer, and the answer actually reaches the model as a real
        tool result -- not silently dropped."""
        checkpoint_store = InMemoryCheckpointStore()
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="call-1", name="task_complete",
            arguments={"output": "partial progress", "needs_input": "the target sprint name"},
        ))
        agent = _build_agent(transport, checkpoint_store)
        first_result = await agent.run(Goal(description="file a ticket in the current sprint"))
        assert first_result.needs_input == "the target sprint name"

        latest = await checkpoint_store.latest(agent.run_ctx.run_id)
        assert latest is not None

        transport2 = ScriptedTransport()
        transport2.add_tool_call(ToolCall(
            id="call-2", name="task_complete",
            arguments={"output": "Filed ticket in Sprint 42"},
        ))
        resumed_agent = _build_agent(transport2, checkpoint_store)

        final_result = await resumed_agent.resume(
            latest.checkpoint_id,
            hil_responses={latest.hil_request_id: "Sprint 42"},
        )

        assert final_result.success is True
        assert final_result.output == "Filed ticket in Sprint 42"
        assert final_result.needs_input is None

        # The injected answer must have actually reached the model as a real
        # message in its context -- not just been accepted and discarded.
        last_call_messages = transport2.calls[-1]["messages"]
        serialized = " ".join(str(getattr(m, "content", "")) for m in last_call_messages)
        assert "Sprint 42" in serialized
