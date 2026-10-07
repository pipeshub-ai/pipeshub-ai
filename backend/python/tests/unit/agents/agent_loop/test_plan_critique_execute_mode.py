"""The `planExecute` chat mode (`app/agents/agent_loop/loops/plan_execute.py`):
the agent writes a plan, has it reviewed, carries out the steps itself, and
has its answer checked before replying.

Each test drives a real `Agent` with the mode's real planning tools, a real
lookup tool and in-memory stores; only the model is scripted. The critics
behind `critique_plan` and `verify_result` get their verdicts from
`VerdictTransport`.
"""

from __future__ import annotations

import re

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolCall
from app.agent_loop_lib.core.responses import ModelResponse, StopReason
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.events.base import AgentEvent, EventEmitter, EventType
from app.agent_loop_lib.modules.pipeline.planner.base import STRUCTURED_PLAN_SLOT
from app.agent_loop_lib.modules.pipeline.planner.replanner import _REPLAN_SYSTEM
from app.agent_loop_lib.modules.stores.checkpoint.in_memory import (
    InMemoryCheckpointStore,
)
from app.agent_loop_lib.modules.stores.state.in_memory import InMemoryStateStore
from app.agent_loop_lib.modules.stores.timeline.in_memory import InMemoryTimelineStore
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.loops.plan_execute import (
    PLANNING_TOOL_NAMES,
    PlanCritiqueExecuteLoop,
    register_planning_tools,
)
from tests.unit.agents.adapter.support.scripted_transport import ScriptedStep
from tests.unit.agents.adapter.support.verdict_transport import VerdictTransport

_GOAL = Goal(description="Summarise this week's open support tickets")
_PLAN = [
    {"id": "fetch", "description": "Look up this week's open tickets", "domain": "research"},
    {"id": "summarise", "description": "Summarise them by customer", "domain": "write-up", "depends_on": ["fetch"]},
]
_PASS = {"passed": True, "confidence": "high", "summary": "ok", "issues": []}


def _fail(description: str = "not good enough") -> dict[str, object]:
    return {
        "passed": False, "confidence": "high", "summary": description,
        "issues": [{"severity": "error", "description": description}],
    }


class _Transport(VerdictTransport):
    """Presses Stop right after the model's `stop_after_call`-th reply (0-based)."""

    def __init__(
        self,
        verdicts: list[dict[str, object]] | None = None,
        *,
        token: CancellationToken | None = None,
        stop_after_call: int | None = None,
    ) -> None:
        super().__init__(verdicts)
        self._token = token
        self._stop_after_call = stop_after_call

    async def complete(self, messages: list[Message], tools=None, system=None, model=None,
                       thinking_budget=None, effort=None, system_blocks=None) -> ModelResponse:
        response = await super().complete(messages, tools=tools, system=system, model=model)
        if self._token is not None and len(self.calls) - 1 == self._stop_after_call:
            self._token.cancel()
        return response


class _LookupTool(Tool):
    """Fails for any query starting with "broken", like a connector that is down."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    @property
    def name(self) -> str:
        return "lookup"

    @property
    def short_description(self) -> str:
        return "Looks something up"

    @property
    def description(self) -> str:
        return "Looks something up"

    @property
    def path(self) -> str:
        return "/toolsets/test/lookup"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="query")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        query = str(kwargs["q"])
        self.executed.append(query)
        if query.startswith("broken"):
            return ToolOutput(success=False, error="source unavailable")
        return ToolOutput(success=True, data=f"found {query}")


class _Recorder(EventEmitter):
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def turn_starts(self) -> list[int]:
        return [e.payload["turn_index"] for e in self.events if e.event_type == EventType.TURN_START]


class _Run:
    def __init__(
        self,
        transport: _Transport,
        *,
        loop: PlanCritiqueExecuteLoop | None = None,
        max_turns: int = 10,
        token: CancellationToken | None = None,
    ) -> None:
        self.transport = transport
        self.lookup = _LookupTool()
        self.recorder = _Recorder()
        self.timeline = InMemoryTimelineStore()
        registry = ToolRegistry()
        register_planning_tools(registry)
        registry.register_tool(self.lookup)
        transports = TransportRegistry()
        transports.register("scripted", lambda: transport)
        runtime = AgentRuntime(
            transport_registry=transports,
            tool_registry=registry,
            event_emitter=self.recorder,
            cancellation_token=token,
            state_store=InMemoryStateStore(),
            timeline_store=self.timeline,
            checkpoint_store=InMemoryCheckpointStore(),
        )
        spec = AgentSpec(
            name="pipeshub-plan-execute",
            system_prompt="You are a helpful assistant.",
            tool_names=["lookup", *PLANNING_TOOL_NAMES],
            model=ModelSpec(provider="scripted", model="scripted-model"),
            loop=loop or PlanCritiqueExecuteLoop(),
            max_turns=max_turns,
        )
        self.agent = Agent(spec, runtime)

    async def go(self) -> AgentResult:
        return await self.agent.run(_GOAL)

    async def phases(self) -> list[str]:
        """The phase instructions the loop sent, e.g. `["1 PLAN", "2 EXECUTE"]`."""
        found = []
        for m in await self.agent.context.messages():
            if m.role == "user" and getattr(m, "injected", False) and isinstance(m.content, str):
                match = re.match(r"Phase (\d) -- (\w+)", m.content)
                if match:
                    found.append(f"{match.group(1)} {match.group(2)}")
        return found


def _call(call_id: str, name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _plan(t: _Transport, call_id: str = "plan", steps: list[dict] | None = None) -> None:
    t.add_tool_call(_call(call_id, "create_plan", steps=steps or _PLAN))


def _review(t: _Transport, call_id: str = "review") -> None:
    t.add_tool_call(_call(call_id, "critique_plan", plan="1. fetch 2. summarise"))


def _plan_and_review(t: _Transport, n: int) -> None:
    t.add_tool_calls([
        _call(f"plan{n}", "create_plan", steps=_PLAN),
        _call(f"review{n}", "critique_plan", plan="1. fetch 2. summarise"),
    ])


def _check(t: _Transport, output: str, call_id: str = "check") -> None:
    t.add_tool_call(_call(call_id, "verify_result", output=output))


def _lookup(t: _Transport, q: str, call_id: str | None = None) -> None:
    t.add_tool_call(_call(call_id or f"lookup-{q}", "lookup", q=q))


def _user_texts(transport: _Transport, call_index: int) -> list[str]:
    return [str(m.content) for m in transport.calls[call_index]["messages"] if m.role == "user"]


class TestThePlanIsReviewedBeforeItRuns:
    async def test_an_approved_plan_runs_and_the_checked_answer_is_returned(self) -> None:
        t = _Transport([_PASS, _PASS])
        _plan(t)
        _review(t)
        _lookup(t, "open tickets")
        _check(t, "draft summary")
        t.add_text("Here is the summary.")
        run = _Run(t)

        result = await run.go()

        assert result.success is True
        assert result.output == "Here is the summary."
        assert await run.phases() == ["1 PLAN", "2 EXECUTE"]
        assert run.lookup.executed == ["open tickets"]
        assert not any("Phase 2 -- EXECUTE" in m for m in _user_texts(t, 1))
        assert any("Phase 2 -- EXECUTE" in m for m in _user_texts(t, 2))
        assert _GOAL.description in t.structured_prompts[0]
        assert "1. fetch 2. summarise" in t.structured_prompts[0]
        assert "draft summary" in t.structured_prompts[1]

    async def test_the_plan_lists_steps_in_order_with_their_dependencies(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        t.add_text("Done.")
        run = _Run(t)

        await run.go()

        stored = run.agent.scope.get(STRUCTURED_PLAN_SLOT)
        assert [s.id for s in stored.steps] == ["fetch", "summarise"]
        plan_text = run.agent.scope.turns[0].tool_results[0].content["plan"]
        assert plan_text.index("**fetch**") < plan_text.index("**summarise**")
        assert "(depends on: fetch)" in plan_text

    async def test_a_plan_with_a_step_depending_on_a_missing_step_is_refused(self) -> None:
        t = _Transport([_PASS])
        _plan(t, "bad-plan", steps=[{"id": "summarise", "description": "Summarise", "domain": "write-up", "depends_on": ["fetch"]}])
        _plan(t)
        _review(t)
        t.add_text("Done.")
        run = _Run(t)

        result = await run.go()

        refused = run.agent.scope.turns[0].tool_results[0]
        assert refused.is_error is True
        assert "'fetch' which does not exist" in refused.content
        assert result.success is True
        assert [s.id for s in run.agent.scope.get(STRUCTURED_PLAN_SLOT).steps] == ["fetch", "summarise"]

    async def test_a_simple_question_can_be_answered_without_a_plan(self) -> None:
        t = _Transport()
        t.add_text("It is Tuesday.")
        run = _Run(t)

        result = await run.go()

        assert result.success is True
        assert result.output == "It is Tuesday."
        assert await run.phases() == ["1 PLAN"]
        assert t.structured_prompts == []

    async def test_the_mode_offers_the_planning_tools_but_never_delegation(self) -> None:
        t = _Transport()
        t.add_text("Hi.")
        run = _Run(t)

        await run.go()

        offered = {schema.name for schema in t.calls[0]["tools"]}
        assert set(PLANNING_TOOL_NAMES) <= offered
        assert "spawn_agent" not in offered
        assert not run.agent.runtime.tool_registry.has("spawn_agent")


class TestARejectedPlanIsRevised:
    async def test_the_reviewers_issues_reach_the_model_and_the_revision_runs(self) -> None:
        t = _Transport([_fail("No step covers the deadline"), _PASS])
        _plan(t, "plan1")
        _review(t, "review1")
        _plan(t, "plan2")
        _review(t, "review2")
        t.add_text("Done.")
        run = _Run(t)

        result = await run.go()

        assert result.success is True
        assert await run.phases() == ["1 PLAN", "1 REPLAN", "2 EXECUTE"]
        rejection = run.agent.scope.turns[1].tool_results[0].content
        assert rejection["passed"] is False
        assert rejection["issues"][0]["description"] == "No step covers the deadline"

    async def test_after_the_revision_limit_the_final_plan_runs_without_another_review(self) -> None:
        t = _Transport([_fail(), _fail()])
        _plan_and_review(t, 1)
        _plan_and_review(t, 2)
        _plan(t, "final-plan")
        _lookup(t, "open tickets")
        t.add_text("Done.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_planning_rounds=2))

        result = await run.go()

        assert result.success is True
        phases = await run.phases()
        assert phases.count("1 REPLAN") == 2
        assert phases[-2:] == ["1 FINALIZE", "2 EXECUTE"]
        assert len(t.structured_prompts) == 2
        assert run.lookup.executed == ["open tickets"]

    async def test_an_answer_given_in_the_final_planning_turn_ends_the_run(self) -> None:
        t = _Transport([_fail()])
        _plan_and_review(t, 1)
        t.add_text("I can answer this directly.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_planning_rounds=1))

        result = await run.go()

        assert result.success is True
        assert result.output == "I can answer this directly."
        assert "2 EXECUTE" not in await run.phases()

    async def test_reviews_that_use_up_every_turn_end_the_run_as_a_failure(self) -> None:
        t = _Transport([_fail(), _fail()])
        _plan_and_review(t, 1)
        _plan_and_review(t, 2)
        run = _Run(t, max_turns=2)

        result = await run.go()

        assert result.success is False
        assert result.error == "Exceeded max_turns=2"
        phases = await run.phases()
        assert phases[:3] == ["1 PLAN", "1 REPLAN", "1 REPLAN"]
        assert "1 FINALIZE" not in phases
        assert len(t.calls) == 2


class TestAFailingStep:
    async def test_a_failing_tool_is_reported_to_the_model_and_the_run_carries_on(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        _lookup(t, "broken source")
        _lookup(t, "backup source")
        t.add_text("Done from the backup.")
        run = _Run(t)

        result = await run.go()

        assert result.success is True
        assert result.output == "Done from the backup."
        failed = run.agent.scope.turns[2].tool_results[0]
        assert failed.is_error is True
        assert "source unavailable" in str(failed.content)
        assert run.lookup.executed == ["broken source", "backup source"]
        assert await run.phases() == ["1 PLAN", "2 EXECUTE"]

    async def test_replanning_after_a_failing_step_starts_from_the_approved_plan(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        _lookup(t, "broken source")
        t.add_tool_call(_call("replan", "replan", reason="the ticket source is down"))
        t.add_text("1. fetch: use the backup source\n2. summarise")
        _lookup(t, "backup source")
        t.add_text("Done from the backup.")
        run = _Run(t)

        result = await run.go()

        assert result.success is True
        replanner_calls = [c for c in t.calls if c["system"] == _REPLAN_SYSTEM]
        assert len(replanner_calls) == 1
        prompt = str(replanner_calls[0]["messages"][0].content)
        assert "the ticket source is down" in prompt
        assert "Prior plan:" in prompt
        assert "**fetch**" in prompt and "**summarise**" in prompt
        assert run.agent.scope.turns[3].tool_results[0].content.startswith("1. fetch: use the backup source")
        timeline = await run.timeline.get_by_run(run.agent.run_ctx.run_id)
        assert any(e.event_type == "replan" and e.summary == "Replanned" for e in timeline)


    async def test_a_second_replan_starts_from_the_first_replans_plan(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        t.add_tool_call(_call("replan1", "replan", reason="the ticket source is down"))
        t.add_text("1. fetch: use the backup source\n2. summarise")
        t.add_tool_call(_call("replan2", "replan", reason="the backup source is down too"))
        t.add_text("1. fetch: ask the support lead\n2. summarise")
        t.add_text("Done.")
        run = _Run(t)

        await run.go()

        replanner_calls = [c for c in t.calls if c["system"] == _REPLAN_SYSTEM]
        assert len(replanner_calls) == 2
        second_prompt = str(replanner_calls[1]["messages"][0].content)
        assert "Prior plan:\n1. fetch: use the backup source" in second_prompt
        assert "**fetch**" not in second_prompt
        stored = run.agent.scope.get(STRUCTURED_PLAN_SLOT)
        assert stored.text == "1. fetch: ask the support lead\n2. summarise"
        assert stored.goal == _GOAL
        assert stored.steps is None


class TestTheAnswerCheck:
    async def test_a_weak_answer_is_sent_back_and_the_improved_one_is_returned(self) -> None:
        t = _Transport([_PASS, _fail("misses the totals"), _PASS])
        _plan(t)
        _review(t)
        _check(t, "draft 1", "check1")
        _lookup(t, "ticket totals")
        _check(t, "draft 2", "check2")
        t.add_text("Improved summary with totals.")
        run = _Run(t)

        result = await run.go()

        assert result.output == "Improved summary with totals."
        assert await run.phases() == ["1 PLAN", "2 EXECUTE", "3 REVISE"]
        assert "draft 1" in t.structured_prompts[1]
        assert "draft 2" in t.structured_prompts[2]

    async def test_after_the_check_limit_the_model_is_told_to_reply_with_its_best_answer(self) -> None:
        t = _Transport([_PASS, _fail(), _fail()])
        _plan(t)
        _review(t)
        _check(t, "draft 1", "check1")
        _check(t, "draft 2", "check2")
        t.add_text("Best effort summary.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_verify_rounds=2))

        result = await run.go()

        assert result.success is True
        assert result.output == "Best effort summary."
        assert await run.phases() == ["1 PLAN", "2 EXECUTE", "3 REVISE", "3 FINISH"]

    async def test_a_check_that_breaks_counts_as_a_failed_check(self) -> None:
        t = _Transport([_PASS, {"passed": True, "issues": ["not an object"]}])
        _plan(t)
        _review(t)
        _check(t, "draft")
        t.add_text("Done.")
        run = _Run(t)

        result = await run.go()

        broken = run.agent.scope.turns[2].tool_results[0]
        assert broken.is_error is True
        assert await run.phases() == ["1 PLAN", "2 EXECUTE", "3 REVISE"]
        assert result.output == "Done."


class TestRunningOutOfTurns:
    async def test_running_out_of_turns_while_executing_reports_a_failure(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        _lookup(t, "page 1")
        _lookup(t, "page 2")
        _lookup(t, "page 3")
        run = _Run(t, max_turns=4)

        result = await run.go()

        assert result.success is False
        assert result.cancelled is False
        assert result.error == "Exceeded max_turns=4"
        assert result.output is None
        assert run.lookup.executed == ["page 1", "page 2"]
        assert len(t.calls) == 4

    async def test_each_turn_number_is_used_once_across_all_phases(self) -> None:
        t = _Transport([_fail(), _PASS])
        _plan_and_review(t, 1)
        _plan(t, "final-plan")
        _lookup(t, "open tickets")
        _check(t, "draft")
        t.add_text("Done.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_planning_rounds=1))

        await run.go()

        assert await run.phases() == ["1 PLAN", "1 REPLAN", "1 FINALIZE", "2 EXECUTE"]
        assert run.recorder.turn_starts() == [0, 1, 2, 3, 4]


def _stop_while_planning(t: _Transport) -> None:
    _plan(t)


def _stop_while_finalizing(t: _Transport) -> None:
    _plan_and_review(t, 1)
    _plan(t, "final-plan")


def _stop_while_executing(t: _Transport) -> None:
    _plan(t)
    _review(t)
    _lookup(t, "open tickets")


def _stop_while_revising(t: _Transport) -> None:
    _plan(t)
    _review(t)
    _check(t, "draft")
    _lookup(t, "ticket totals")


class TestTheStopButton:
    @pytest.mark.parametrize(
        ("script", "verdicts", "stop_after_call", "expected_phases"),
        [
            pytest.param(_stop_while_planning, [], 0, ["1 PLAN"], id="planning"),
            pytest.param(
                _stop_while_finalizing, [_fail()], 1, ["1 PLAN", "1 REPLAN", "1 FINALIZE", "2 EXECUTE"],
                id="finalizing",
            ),
            pytest.param(_stop_while_executing, [_PASS], 2, ["1 PLAN", "2 EXECUTE"], id="executing"),
            pytest.param(_stop_while_revising, [_PASS, _fail()], 3, ["1 PLAN", "2 EXECUTE", "3 REVISE"], id="revising"),
        ],
    )
    async def test_stop_ends_the_run_as_cancelled_with_no_further_model_calls(
        self, script, verdicts, stop_after_call, expected_phases,
    ) -> None:
        token = CancellationToken()
        t = _Transport(verdicts, token=token, stop_after_call=stop_after_call)
        script(t)
        t.add_text("should never be asked")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_planning_rounds=1), token=token)

        result = await run.go()

        assert result.cancelled is True
        assert result.success is False
        assert len(t.calls) == stop_after_call + 1
        assert await run.phases() == expected_phases
        assert run.lookup.executed == []

    async def test_stop_in_the_middle_of_the_final_reply_is_reported_as_cancelled(self) -> None:
        t = _Transport([_PASS])
        _plan(t)
        _review(t)
        t._script.append(ScriptedStep(
            message=AssistantMessage(content="Here is the sum"), stop_reason=StopReason.CANCELLED,
        ))
        run = _Run(t)

        result = await run.go()

        assert result.cancelled is True
        assert result.output is None
        assert result.error == "Cancelled"


class TestNothingIsCountedTwice:
    async def test_a_turn_without_a_review_is_not_counted_as_another_rejection(self) -> None:
        t = _Transport([_fail(), _PASS])
        _plan_and_review(t, 1)
        _plan(t, "revised-plan")
        _review(t, "review2")
        t.add_text("Done.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_planning_rounds=2))

        result = await run.go()

        assert result.success is True
        assert await run.phases() == ["1 PLAN", "1 REPLAN", "2 EXECUTE"]

    async def test_a_revision_turn_without_a_check_does_not_trigger_finish(self) -> None:
        t = _Transport([_PASS, _fail()])
        _plan(t)
        _review(t)
        _check(t, "draft")
        _lookup(t, "ticket totals")
        t.add_text("Improved summary.")
        run = _Run(t, loop=PlanCritiqueExecuteLoop(max_verify_rounds=2))

        result = await run.go()

        assert result.output == "Improved summary."
        assert await run.phases() == ["1 PLAN", "2 EXECUTE", "3 REVISE"]

    async def test_an_answer_check_made_while_planning_is_not_counted_once_execution_starts(self) -> None:
        t = _Transport([_fail("premature"), _PASS])
        _plan(t)
        _check(t, "too early")
        _review(t)
        _lookup(t, "open tickets")
        t.add_text("Done.")
        run = _Run(t)

        result = await run.go()

        assert result.output == "Done."
        assert await run.phases() == ["1 PLAN", "2 EXECUTE"]
