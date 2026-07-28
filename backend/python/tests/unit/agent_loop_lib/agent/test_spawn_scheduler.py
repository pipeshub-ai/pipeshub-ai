"""`spawn_scheduler.py`: dependency-aware ordering for a `spawn_agent`
batch — the fix for "two spawned sub-agents either race or the dependent
one can't see the prerequisite's output" (e.g. a 'fetch Jira tickets' task
and a 'build a PDF from them' task dispatched in the same turn).

Split into two layers, matching the module's own split:
  - `validate_spawn_batch` — pure, synchronous graph validation. No
    asyncio, no Agent, no runtime; every case here is deterministic.
  - `schedule_spawn_batch` — the actual async scheduling, exercised
    against a minimal fake `agent`/`runtime` (not a real `Agent.run()`)
    so ordering/injection/failure-propagation are each isolated to the
    scheduler's own logic. `test_orchestrator_loop.py` and
    `test_spawn_agent_dependencies.py` cover the same behavior through a
    real `Agent`+`ScriptedTransport` end-to-end.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.agent.spawn_scheduler import (
    SPAWN_RESULTS_SLOT,
    SpawnDependencyError,
    schedule_spawn_batch,
    validate_spawn_batch,
)
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.scope import RunScope
from app.agent_loop_lib.core.types import AgentResult, Artifact, Goal
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import peek_staged_input_files


def _call(call_id: str, *, role: str = "worker", goal: str = "do work", task_id: str | None = None,
          depends_on: list[str] | None = None) -> ToolCall:
    args: dict[str, Any] = {"role": role, "goal": goal, "reasoning": "test"}
    if task_id is not None:
        args["task_id"] = task_id
    if depends_on is not None:
        args["depends_on"] = depends_on
    return ToolCall(id=call_id, name="spawn_agent", arguments=args)


class TestValidateSpawnBatch:
    def test_independent_calls_have_no_errors(self) -> None:
        calls = [_call("c1", task_id="a"), _call("c2", task_id="b")]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert plan.errors_by_call_id == {}
        assert plan.task_id_by_call_id == {"c1": "a", "c2": "b"}

    def test_missing_task_id_defaults_to_call_id(self) -> None:
        plan = validate_spawn_batch([_call("c1")], known_task_ids=set())
        assert plan.task_id_by_call_id["c1"] == "c1"
        assert plan.errors_by_call_id == {}

    def test_valid_dependency_on_sibling_has_no_errors(self) -> None:
        calls = [_call("c1", task_id="a"), _call("c2", task_id="b", depends_on=["a"])]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert plan.errors_by_call_id == {}
        assert plan.depends_by_call_id["c2"] == ["a"]

    def test_valid_dependency_on_earlier_turn_has_no_errors(self) -> None:
        calls = [_call("c1", task_id="pdf", depends_on=["jira"])]
        plan = validate_spawn_batch(calls, known_task_ids={"jira"})
        assert plan.errors_by_call_id == {}

    def test_self_dependency_is_rejected(self) -> None:
        plan = validate_spawn_batch([_call("c1", task_id="a", depends_on=["a"])], known_task_ids=set())
        assert "c1" in plan.errors_by_call_id
        assert "cannot list itself" in plan.errors_by_call_id["c1"]

    def test_unknown_dependency_is_rejected(self) -> None:
        plan = validate_spawn_batch([_call("c1", task_id="a", depends_on=["ghost"])], known_task_ids=set())
        assert "c1" in plan.errors_by_call_id
        assert "ghost" in plan.errors_by_call_id["c1"]

    def test_duplicate_task_id_within_batch_is_rejected(self) -> None:
        calls = [_call("c1", task_id="dup"), _call("c2", task_id="dup")]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert "c2" in plan.errors_by_call_id
        assert "c1" not in plan.errors_by_call_id
        assert "unique" in plan.errors_by_call_id["c2"]

    def test_task_id_colliding_with_earlier_turn_is_rejected(self) -> None:
        plan = validate_spawn_batch([_call("c1", task_id="jira")], known_task_ids={"jira"})
        assert "c1" in plan.errors_by_call_id

    def test_direct_cycle_is_rejected(self) -> None:
        calls = [
            _call("c1", task_id="a", depends_on=["b"]),
            _call("c2", task_id="b", depends_on=["a"]),
        ]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert "c1" in plan.errors_by_call_id
        assert "c2" in plan.errors_by_call_id
        assert "circular" in plan.errors_by_call_id["c1"]

    def test_dependent_of_invalid_sibling_is_also_invalid(self) -> None:
        calls = [
            _call("c1", task_id="a", depends_on=["ghost"]),
            _call("c2", task_id="b", depends_on=["a"]),
        ]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert "c1" in plan.errors_by_call_id
        assert "c2" in plan.errors_by_call_id
        assert "itself invalid" in plan.errors_by_call_id["c2"]

    def test_independent_sibling_of_an_invalid_call_stays_valid(self) -> None:
        calls = [
            _call("c1", task_id="a", depends_on=["ghost"]),
            _call("c2", task_id="b"),
        ]
        plan = validate_spawn_batch(calls, known_task_ids=set())
        assert "c1" in plan.errors_by_call_id
        assert "c2" not in plan.errors_by_call_id


class _RunChildRecorder:
    """Fake `AgentRuntime.run_child` — returns a canned `AgentResult` per
    role and records call order/goals for assertions, without needing a
    real `Agent`/transport."""

    def __init__(self, canned: dict[str, AgentResult]) -> None:
        self._canned = canned
        self.calls: list[tuple[str, Goal]] = []

    async def __call__(self, spec: Any, goal: Goal, run_ctx: Any, **kwargs: Any) -> AgentResult:
        self.calls.append((spec, goal))
        return self._canned[spec]


class _ConcurrencyProbeRunChild:
    """Fake `run_child` that records the peak number of simultaneously
    in-flight calls — used to prove independent spawns still run fully
    concurrently (no regression from adding dependency support)."""

    def __init__(self, canned: dict[str, AgentResult]) -> None:
        self._canned = canned
        self._current = 0
        self.max_concurrent = 0
        self._lock = asyncio.Lock()

    async def __call__(self, spec: Any, goal: Goal, run_ctx: Any, **kwargs: Any) -> AgentResult:
        async with self._lock:
            self._current += 1
            self.max_concurrent = max(self.max_concurrent, self._current)
        await asyncio.sleep(0.01)
        async with self._lock:
            self._current -= 1
        return self._canned[spec]


class _FakeRuntime:
    """`spec_for_role` just echoes the role name back as a stand-in
    `AgentSpec` — the scheduler treats it as opaque and hands it straight
    to `run_child`, so a real `AgentSpec` isn't needed for these tests."""

    def __init__(self, run_child: Any) -> None:
        self.state_store = None
        self.timeline_store = None
        self.tool_registry = None
        self.run_child = run_child

    def spec_for_role(self, role_name: str, **overrides: Any) -> str:
        return role_name


class _FakeAgent:
    def __init__(self, runtime: _FakeRuntime, scope: RunScope) -> None:
        self.runtime = runtime
        self.run_ctx = RunContext(role_name="parent", model="scripted-model")
        self.session_id = "session-1"
        self.scope = scope
        self.spec = SimpleNamespace(name="parent-agent")

    async def emit(self, event_type: Any, payload: dict) -> None:
        return None


def _run_scope(runtime: _FakeRuntime) -> RunScope:
    return RunScope(
        identity=RunContext(role_name="parent", model="scripted-model"),
        spec=SimpleNamespace(mode="react", max_turns=10),
        runtime=runtime,
        goal=Goal(description="top-level goal"),
    )


class TestScheduleSpawnBatch:
    async def test_dependent_task_waits_and_receives_prerequisite_output(self) -> None:
        recorder = _RunChildRecorder({
            "jira": AgentResult(goal=Goal(description="x"), output="Found 3 tickets: A, B, C", success=True),
            "pdf": AgentResult(goal=Goal(description="x"), output="PDF created", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="jira", goal="Fetch and categorize Jira tickets", task_id="jira"),
            _call("c2", role="pdf", goal="Create a PDF report from the fetched tickets",
                  task_id="pdf", depends_on=["jira"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        jira_result = await tasks["c1"]
        pdf_result = await tasks["c2"]

        assert jira_result.output == "Found 3 tickets: A, B, C"
        assert pdf_result.output == "PDF created"
        # Ordering: the PDF child's run_child call must happen strictly
        # after the Jira child's — proven by call order, not a sleep/race.
        assert [role for role, _ in recorder.calls] == ["jira", "pdf"]
        # Data handoff: the PDF child's goal must contain the Jira
        # child's actual output, not just an isolated goal string.
        pdf_goal = recorder.calls[1][1]
        assert "Found 3 tickets: A, B, C" in pdf_goal.description
        assert "Create a PDF report from the fetched tickets" in pdf_goal.description

    async def test_independent_task_goal_is_unmodified(self) -> None:
        recorder = _RunChildRecorder({
            "solo": AgentResult(goal=Goal(description="x"), output="done", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [_call("c1", role="solo", goal="Just do this one thing", task_id="solo")]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        await tasks["c1"]

        assert recorder.calls[0][1].description == "Just do this one thing"

    async def test_independent_tasks_still_run_concurrently(self) -> None:
        recorder = _ConcurrencyProbeRunChild({
            "a": AgentResult(goal=Goal(description="x"), output="a-done", success=True),
            "b": AgentResult(goal=Goal(description="x"), output="b-done", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [_call("c1", role="a", task_id="a"), _call("c2", role="b", task_id="b")]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        await asyncio.gather(*tasks.values())

        assert recorder.max_concurrent == 2

    async def test_invalid_call_errors_without_blocking_valid_siblings(self) -> None:
        recorder = _RunChildRecorder({
            "a": AgentResult(goal=Goal(description="x"), output="ok", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="a", task_id="a"),
            _call("c2", role="b", task_id="b", depends_on=["ghost"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )

        a_result = await tasks["c1"]
        assert a_result.output == "ok"
        with pytest.raises(SpawnDependencyError, match="ghost"):
            await tasks["c2"]

    async def test_failed_prerequisite_skips_dependent_without_running_it(self) -> None:
        recorder = _RunChildRecorder({
            "jira": AgentResult(goal=Goal(description="x"), success=False, error="boom"),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="jira", task_id="jira"),
            _call("c2", role="pdf", task_id="pdf", depends_on=["jira"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )

        jira_result = await tasks["c1"]
        assert jira_result.success is False
        with pytest.raises(SpawnDependencyError, match="boom"):
            await tasks["c2"]
        # The PDF child must never have been launched at all.
        assert [role for role, _ in recorder.calls] == ["jira"]

    async def test_cross_turn_dependency_resolves_from_run_scope(self) -> None:
        recorder = _RunChildRecorder({
            "jira": AgentResult(goal=Goal(description="x"), output="Found 3 tickets", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        turn1 = await schedule_spawn_batch(
            agent, runtime, [_call("c1", role="jira", task_id="jira")], scope,
            goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        await turn1["c1"]
        assert "jira" in scope.get(SPAWN_RESULTS_SLOT)

        recorder._canned["pdf"] = AgentResult(goal=Goal(description="x"), output="PDF ready", success=True)
        turn2 = await schedule_spawn_batch(
            agent, runtime, [_call("c2", role="pdf", task_id="pdf", depends_on=["jira"])], scope,
            goal=Goal(description="top"), turn_index=1, started_at="t0",
        )
        pdf_result = await turn2["c2"]

        assert pdf_result.output == "PDF ready"
        assert "Found 3 tickets" in recorder.calls[-1][1].description

    async def test_infrastructure_failure_records_result_for_dependents(self) -> None:
        """When run_child() raises (infrastructure error, not a normal
        AgentResult failure), the failed task must still record a result in
        SPAWN_RESULTS_SLOT so dependents see the actual failure instead of
        the confusing 'never produced a result' error."""

        async def _exploding_run_child(spec, goal, run_ctx, **kwargs):
            if spec == "boom":
                raise RuntimeError("transport crashed")
            return AgentResult(goal=Goal(description="x"), output="ok", success=True)

        runtime = _FakeRuntime(_exploding_run_child)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="boom", task_id="boom_task"),
            _call("c2", role="ok", task_id="ok_task", depends_on=["boom_task"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope,
            goal=Goal(description="top"), turn_index=0, started_at="t0",
        )

        with pytest.raises(RuntimeError, match="transport crashed"):
            await tasks["c1"]
        with pytest.raises(SpawnDependencyError, match="failed"):
            await tasks["c2"]

        completed = scope.get(SPAWN_RESULTS_SLOT)
        assert "boom_task" in completed
        assert completed["boom_task"].result.success is False

    async def test_every_task_created_before_any_observability_write(self) -> None:
        """Every call's `asyncio.create_task` must happen in one tight,
        `await`-free loop — BEFORE any `obs.write_state`/`append_timeline`
        call for the batch. Regression for the old ordering, where each
        call's own observability writes were interleaved into the same
        loop that created its task, so call N's task creation waited behind
        calls 0..N-1's sequential I/O for no reason (nothing downstream
        depends on write-vs-create ordering)."""
        recorder = _RunChildRecorder({
            "a": AgentResult(goal=Goal(description="x"), output="a", success=True),
            "b": AgentResult(goal=Goal(description="x"), output="b", success=True),
            "c": AgentResult(goal=Goal(description="x"), output="c", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="a", task_id="a"),
            _call("c2", role="b", task_id="b"),
            _call("c3", role="c", task_id="c"),
        ]

        running_task_count_at_first_write: list[int] = []

        async def _spy_write_state(*args: Any, **kwargs: Any) -> None:
            if not running_task_count_at_first_write:
                current = asyncio.current_task()
                still_running = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
                running_task_count_at_first_write.append(len(still_running))

        with patch("app.agent_loop_lib.agent.spawn_scheduler.obs.write_state", side_effect=_spy_write_state):
            tasks = await schedule_spawn_batch(
                agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
            )
        await asyncio.gather(*tasks.values())

        # All 3 spawn tasks were already alive by the time the FIRST
        # observability write fired — none of them was still waiting on an
        # earlier sibling's write to even be created.
        assert running_task_count_at_first_write == [3]

    async def test_batch_of_one_with_no_dependency_is_unaffected(self) -> None:
        """Back-compat: a lone spawn_agent call with no task_id/depends_on
        behaves exactly as before — no goal mutation, no artificial wait."""
        recorder = _RunChildRecorder({
            "solo": AgentResult(goal=Goal(description="x"), output="done", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        tasks = await schedule_spawn_batch(
            agent, runtime, [_call("c1", role="solo", goal="solo goal")], scope,
            goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        result = await tasks["c1"]

        assert result.output == "done"
        assert recorder.calls[0][1].description == "solo goal"

    async def test_dependent_receives_full_artifact_content_via_staged_files(self) -> None:
        """A prerequisite's `AgentResult.artifacts` must reach the dependent
        child as staged, un-truncated files (not inlined/truncated prose) —
        the dependent's goal only carries a reference path, while the exact
        bytes are visible to the dependent's own run via
        `peek_staged_input_files()` at the moment its `run_child` fires."""
        big_ticket_list = [{"key": f"JIRA-{i}", "summary": "x" * 100} for i in range(50)]
        jira_result = AgentResult(
            goal=Goal(description="x"),
            output="Found 50 tickets (see artifact for full data).",
            artifacts=[Artifact(name="tickets.json", type="json", content=big_ticket_list)],
            success=True,
        )
        staged_snapshot: dict[str, dict[str, bytes] | None] = {}

        class _StagingProbeRunChild(_RunChildRecorder):
            async def __call__(self, spec: Any, goal: Goal, run_ctx: Any, **kwargs: Any) -> AgentResult:
                if spec == "pdf":
                    staged_snapshot["pdf"] = peek_staged_input_files()
                return await super().__call__(spec, goal, run_ctx, **kwargs)

        recorder = _StagingProbeRunChild({
            "jira": jira_result,
            "pdf": AgentResult(goal=Goal(description="x"), output="PDF created", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="jira", task_id="jira"),
            _call("c2", role="pdf", task_id="pdf", depends_on=["jira"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        await tasks["c1"]
        await tasks["c2"]

        pdf_goal = recorder.calls[-1][1]
        assert "input/artifacts/jira/tickets.json" in pdf_goal.description
        # The reference in the goal text is short — the full 50-item
        # payload is NOT inlined there.
        assert "JIRA-49" not in pdf_goal.description

        staged = staged_snapshot.get("pdf")
        assert staged is not None
        assert "input/artifacts/jira/tickets.json" in staged
        import json as _json
        restored = _json.loads(staged["input/artifacts/jira/tickets.json"])
        assert restored == big_ticket_list

    async def test_no_staged_files_when_prerequisite_has_no_artifacts(self) -> None:
        """Back-compat: `stage_input_files` must be a no-op (no staged
        files visible) when the prerequisite produced no artifacts at all."""
        staged_snapshot: dict[str, dict[str, bytes] | None] = {}

        class _StagingProbeRunChild(_RunChildRecorder):
            async def __call__(self, spec: Any, goal: Goal, run_ctx: Any, **kwargs: Any) -> AgentResult:
                if spec == "pdf":
                    staged_snapshot["pdf"] = peek_staged_input_files()
                return await super().__call__(spec, goal, run_ctx, **kwargs)

        recorder = _StagingProbeRunChild({
            "jira": AgentResult(goal=Goal(description="x"), output="Found 3 tickets", success=True),
            "pdf": AgentResult(goal=Goal(description="x"), output="PDF created", success=True),
        })
        runtime = _FakeRuntime(recorder)
        scope = _run_scope(runtime)
        agent = _FakeAgent(runtime, scope)

        calls = [
            _call("c1", role="jira", task_id="jira"),
            _call("c2", role="pdf", task_id="pdf", depends_on=["jira"]),
        ]
        tasks = await schedule_spawn_batch(
            agent, runtime, calls, scope, goal=Goal(description="top"), turn_index=0, started_at="t0",
        )
        await tasks["c1"]
        await tasks["c2"]

        assert staged_snapshot.get("pdf") is None
