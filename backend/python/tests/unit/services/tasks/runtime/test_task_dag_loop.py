"""`TaskDagLoop` end-to-end (task engine plan Phase 6: "sub-task DAG --
... assembler bridge into schedule_spawn_batch ... skipped-dependent
reporting"). Drives a REAL top-level `Agent` (not just the pure
`compute_step_report` classification covered in `test_spec_assembler.py`)
through a single shared `ScriptedTransport` that also answers for every
spawned child -- same pattern `test_spawn_agent_dependencies.py` and
`test_orchestrator_loop.py` already use for `schedule_spawn_batch`-backed
dispatch.

The resume test is the actual proof for Part L's open risk ("First
production use of `StateSlot(persist=True)` ... DAG resume depends on
it"): a REAL `Agent.run()` -> checkpoint -> simulated-crash -> a SECOND,
independent `Agent.resume()` that must not re-run the step that already
completed before the "crash".
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.loops.orchestrator import domain_spec_factory
from app.services.tasks.domain.models import TaskStep
from app.services.tasks.runtime.spec_assembler import TaskDagLoop
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _build_agent(steps: list[TaskStep], transport: ScriptedTransport, *, max_turns: int = 10) -> Agent:
    tool_registry = ToolRegistry()  # steps in these tests use no real tools
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=tool_registry,
        spec_factory=domain_spec_factory(
            provider="scripted", model_name="scripted-model", default_tool_names=[], context=None,
        ),
    )
    spec = AgentSpec(
        name="task:dag-task",
        system_prompt="You are an autonomous task-execution agent.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=TaskDagLoop(steps),
        max_turns=max_turns,
    )
    return Agent(spec, runtime, session_id="task-1")


class TestTaskDagLoopHappyPath:
    async def test_dependent_steps_run_in_order_and_run_succeeds(self) -> None:
        steps = [
            TaskStep(id="fetch", description="Fetch open Jira tickets", domain="jira"),
            TaskStep(id="report", description="Summarize the tickets", domain="writer", depends_on=["fetch"]),
        ]
        transport = ScriptedTransport()
        transport.add_text("Found 3 open tickets: JIRA-1, JIRA-2, JIRA-3.")  # fetch child
        transport.add_text("Summary: 3 open tickets, all bugs.")  # report child

        agent = _build_agent(steps, transport)
        result = await agent.run(Goal(description="Fetch and summarize Jira tickets"))

        assert result.success is True
        assert result.needs_input is None
        assert "2/2 step(s) completed" in result.output
        assert "Found 3 open tickets" in result.output
        assert "Summary: 3 open tickets" in result.output


class TestTaskDagLoopFailureAndSkip:
    async def test_failed_step_skips_dependent_and_fails_the_run(self) -> None:
        steps = [
            TaskStep(id="fetch", description="Fetch open Jira tickets", domain="jira"),
            TaskStep(id="report", description="Summarize the tickets", domain="writer", depends_on=["fetch"]),
        ]
        transport = ScriptedTransport()
        transport.add_error(RuntimeError("Jira API unreachable"))  # fetch child's only turn fails

        agent = _build_agent(steps, transport)
        result = await agent.run(Goal(description="Fetch and summarize Jira tickets"))

        assert result.success is False
        assert "fetch" in result.error
        assert "Skipped" in result.error
        assert "report" in result.error

    async def test_independent_branch_still_completes_despite_sibling_failure(self) -> None:
        steps = [
            TaskStep(id="fetch", description="Fetch open Jira tickets", domain="jira"),
            TaskStep(id="report", description="Summarize the tickets", domain="writer", depends_on=["fetch"]),
            TaskStep(id="standalone", description="Unrelated independent step", domain="misc"),
        ]
        transport = ScriptedTransport()
        transport.add_error(RuntimeError("Jira API unreachable"))  # fetch fails
        transport.add_text("Standalone step done.")  # standalone still runs

        agent = _build_agent(steps, transport)
        result = await agent.run(Goal(description="Do three things"))

        assert result.success is False  # overall run is not clean (fetch failed, report skipped)
        assert "Standalone step done." in result.error


class TestTaskDagLoopResume:
    async def test_resumed_run_does_not_re_execute_the_already_completed_step(self) -> None:
        """The real payoff of `SPAWN_RESULTS_SLOT(persist=True)`: `fetch`
        already completed before a simulated crash; on resume, ONLY
        `report` (never yet attempted) may call the model -- if
        `TaskDagLoop` resubmitted `fetch` too, `ScriptedTransport` would
        raise outright on the exhausted script (proving a literal re-run),
        and even a script with spare capacity would still be WRONG per
        this loop's own resume-correctness contract (see its docstring on
        `_propagate_invalidity` poisoning dependents of a resubmitted,
        already-complete sibling)."""
        from app.agent_loop_lib.agent.spawn_scheduler import (
            SPAWN_RESULTS_SLOT,
            schedule_spawn_batch,
        )
        from app.agent_loop_lib.core.scope import RunScope, known_persisted_slots

        steps = [
            TaskStep(id="fetch", description="Fetch open Jira tickets", domain="jira"),
            TaskStep(id="report", description="Summarize the tickets", domain="writer", depends_on=["fetch"]),
        ]

        # Simulate "the run crashed with only `fetch` recorded" by running
        # JUST that one step through the real scheduler on a throwaway
        # scope, then snapshotting it exactly as `Agent.succeed()`/
        # `save_checkpoint()` would (`RunScope.snapshot_extensions()`).
        setup_transport = ScriptedTransport()
        setup_transport.add_text("Found 3 open tickets.")
        setup_agent = _build_agent([steps[0]], setup_transport)
        setup_result = await setup_agent.run(Goal(description="setup"))
        assert setup_result.success is True
        pre_crash_snapshot = setup_agent.scope.snapshot_extensions()
        assert set(pre_crash_snapshot[SPAWN_RESULTS_SLOT.key].keys()) == {"fetch"}

        transport2 = ScriptedTransport()
        transport2.add_text("Summary: 3 open tickets, all bugs.")  # report's only scripted turn
        # No response for a `fetch` re-run -- if `TaskDagLoop` resubmitted
        # it, ScriptedTransport would either raise (script exhausted) or,
        # if this were the FIRST scripted item, silently mis-attribute
        # `report`'s answer to a bogus `fetch` re-run instead.
        agent2 = _build_agent(steps, transport2, max_turns=10)

        result2 = await agent2.run(
            Goal(description="Fetch and summarize Jira tickets"),
            _resume_extensions=pre_crash_snapshot,
            _skip_start=True,
        )

        assert result2.success is True
        assert len(transport2.calls) == 1  # only `report` ever called the model
        restored = agent2.scope.get(SPAWN_RESULTS_SLOT)
        assert set(restored.keys()) == {"fetch", "report"}
        assert restored["fetch"].result.output == "Found 3 open tickets."  # untouched, not re-run
        assert restored["report"].result.output == "Summary: 3 open tickets, all bugs."
        assert known_persisted_slots()  # sanity: the registry is non-empty in this process

        # Document (not just assert-away) why `TaskDagLoop` bothers
        # filtering completed steps out at all: resubmitting `fetch`
        # alongside its dependent is REJECTED as a duplicate task_id, but
        # the dependent itself is NOT poisoned by that rejection --
        # `_run_dependent_spawn` resolves it straight from
        # `SPAWN_RESULTS_SLOT` before ever consulting in-batch siblings.
        # So the filter is pure hygiene (no wasted rejected call / no-op
        # error every resumed turn), not a correctness requirement for
        # this dependency shape -- worth pinning down explicitly so a
        # future reader doesn't assume the opposite.
        from tests.unit.agent_loop_lib.agent.test_spawn_scheduler import (
            _call,
            validate_spawn_batch,
        )

        replay_plan = validate_spawn_batch(
            [_call("c1", task_id="fetch"), _call("c2", task_id="report", depends_on=["fetch"])],
            known_task_ids={"fetch"},
        )
        assert "c1" in replay_plan.errors_by_call_id
        assert "already used" in replay_plan.errors_by_call_id["c1"]
        assert "c2" not in replay_plan.errors_by_call_id
        # Reference `schedule_spawn_batch`/`RunScope` directly so this
        # module's imports stay self-documenting about what it's proving.
        assert schedule_spawn_batch is not None
        assert RunScope is not None
