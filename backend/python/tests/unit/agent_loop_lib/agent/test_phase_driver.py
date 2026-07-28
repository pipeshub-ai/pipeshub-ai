"""`PhaseDriver` (`app/agent_loop_lib/agent/phase_driver.py`) — the shared
plan/critique/replan and verify/retry looping mechanics extracted from
`PlanCritiqueExecuteLoop` and PipesHub's `OrchestratorLoop`.

Tested against a minimal fake standing in for `Agent` — `PhaseDriver` only
ever calls `step()`/`last_tool_result()`/`inject_user_message()`/reads
`max_turns`, so a fake implementing exactly that surface exercises every
branch deterministically, with no real model/tool wiring needed (that
wiring is covered separately by `test_plan_critique_execute_loop.py` and
`OrchestratorLoop`'s own tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_loop_lib.agent.loops import StepOutcome
from app.agent_loop_lib.agent.phase_driver import PhaseDriver
from app.agent_loop_lib.core.types import AgentResult, Goal

_GOAL = Goal(description="g")


@dataclass
class _ScriptedTurn:
    """One `agent.step()` call's canned outcome plus the gate-tool verdict
    `agent.last_tool_result()` should report for that same turn."""

    status: str = "continue"
    result: AgentResult | None = None
    verdict: Any = "NO_RESULT_SENTINEL"


class _FakeAgent:
    """Stands in for `Agent` across exactly the surface `PhaseDriver` uses.
    Every test below scripts `_ScriptedTurn.verdict` explicitly (either a
    dict or `None`), so the dataclass field's own default is never
    actually read at test time."""

    def __init__(self, turns: list[_ScriptedTurn], *, max_turns: int = 10) -> None:
        self._turns = turns
        self.max_turns = max_turns
        self.injected_messages: list[str] = []
        self.step_calls: list[int] = []

    async def step(self, goal: Goal, turn_index: int) -> StepOutcome:
        self.step_calls.append(turn_index)
        turn = self._turns[len(self.step_calls) - 1]
        return StepOutcome(status=turn.status, result=turn.result)

    def last_tool_result(self, name: str) -> Any:
        turn = self._turns[len(self.step_calls) - 1]
        verdict = turn.verdict
        return None if verdict == "NO_RESULT_SENTINEL" else verdict

    async def inject_user_message(self, text: str, *, pinned: bool = False) -> None:
        self.injected_messages.append(text)


def _stop_result(text: str = "done") -> AgentResult:
    return AgentResult(goal=_GOAL, output=text, turns=[], success=True)


class TestRunPlanningPhase:
    async def test_passes_on_the_first_round(self) -> None:
        agent = _FakeAgent([_ScriptedTurn(verdict={"passed": True})])
        driver = PhaseDriver(max_planning_rounds=3)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.stopped is False
        assert outcome.passed is True
        assert outcome.turn_index == 1
        assert agent.injected_messages == ["PLAN"]  # no REPLAN needed

    async def test_fails_then_passes_injects_replan_once(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict={"passed": False}),
            _ScriptedTurn(verdict={"passed": True}),
        ])
        driver = PhaseDriver(max_planning_rounds=3)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.passed is True
        assert outcome.turn_index == 2
        assert agent.injected_messages == ["PLAN", "REPLAN"]

    async def test_exhausts_round_budget_without_passing(self) -> None:
        agent = _FakeAgent([_ScriptedTurn(verdict={"passed": False}) for _ in range(5)])
        driver = PhaseDriver(max_planning_rounds=2)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.stopped is False
        assert outcome.passed is False
        assert outcome.turn_index == 2  # stopped after exactly max_planning_rounds turns
        assert len(agent.step_calls) == 2

    async def test_stops_immediately_when_step_ends_the_run(self) -> None:
        result = _stop_result("early exit")
        agent = _FakeAgent([_ScriptedTurn(status="stop", result=result)])
        driver = PhaseDriver()

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.stopped is True
        assert outcome.result is result
        assert outcome.turn_index == 1

    async def test_no_verdict_nudge_fires_when_no_gate_result_and_predicate_says_so(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict=None),  # no critique_plan result this turn
            _ScriptedTurn(verdict={"passed": True}),
        ])
        driver = PhaseDriver(max_planning_rounds=3)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
            no_verdict_nudge=lambda a: "NUDGE",
        )

        assert outcome.passed is True
        assert agent.injected_messages == ["PLAN", "NUDGE"]

    async def test_no_verdict_nudge_silent_when_predicate_returns_none(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict=None),
            _ScriptedTurn(verdict={"passed": True}),
        ])
        driver = PhaseDriver(max_planning_rounds=3)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
            no_verdict_nudge=lambda a: None,
        )

        assert outcome.passed is True
        assert agent.injected_messages == ["PLAN"]

    async def test_no_verdict_round_does_not_count_against_the_round_budget(self) -> None:
        """A turn with no gate-tool result at all (model didn't call it)
        must not consume a planning round — only a turn that DID call the
        gate tool (and failed) should."""
        agent = _FakeAgent([
            _ScriptedTurn(verdict=None),
            _ScriptedTurn(verdict=None),
            _ScriptedTurn(verdict={"passed": True}),
        ], max_turns=10)
        driver = PhaseDriver(max_planning_rounds=1)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.passed is True
        assert len(agent.step_calls) == 3

    async def test_respects_max_turns_even_within_round_budget(self) -> None:
        agent = _FakeAgent([_ScriptedTurn(verdict={"passed": False})] * 5, max_turns=2)
        driver = PhaseDriver(max_planning_rounds=10)

        outcome = await driver.run_planning_phase(
            agent, _GOAL, 0,
            planning_message="PLAN",
            replan_message="REPLAN",
        )

        assert outcome.turn_index == 2
        assert len(agent.step_calls) == 2


class TestRunVerifyPhase:
    async def test_passing_verdict_does_not_stop_the_loop_early(self) -> None:
        """Unlike planning, a passing verify verdict alone never ends
        Phase 3 — only the model itself ending the run (task_complete/
        reply) does, via `outcome.status == 'stop'`."""
        result = _stop_result("final answer")
        agent = _FakeAgent([
            _ScriptedTurn(verdict={"passed": True}),
            _ScriptedTurn(status="stop", result=result),
        ], max_turns=10)
        driver = PhaseDriver()

        outcome = await driver.run_verify_phase(
            agent, _GOAL, 0,
            verify_message="VERIFY",
            revise_message="REVISE",
            finish_message="FINISH",
        )

        assert outcome.stopped is True
        assert outcome.result is result
        assert agent.injected_messages == ["VERIFY"]  # no REVISE/FINISH — it passed

    async def test_failing_verdict_injects_revise_until_round_budget_then_finish(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict={"passed": False}),
            _ScriptedTurn(verdict={"passed": False}),
            _ScriptedTurn(status="stop", result=_stop_result()),
        ], max_turns=10)
        driver = PhaseDriver(max_verify_rounds=2)

        outcome = await driver.run_verify_phase(
            agent, _GOAL, 0,
            verify_message="VERIFY",
            revise_message="REVISE",
            finish_message="FINISH",
        )

        assert outcome.stopped is True
        assert agent.injected_messages == ["VERIFY", "REVISE", "FINISH"]

    async def test_runs_to_max_turns_when_the_model_never_stops_the_run(self) -> None:
        agent = _FakeAgent([_ScriptedTurn(verdict={"passed": False})] * 3, max_turns=3)
        driver = PhaseDriver(max_verify_rounds=10)

        outcome = await driver.run_verify_phase(
            agent, _GOAL, 0,
            verify_message="VERIFY",
            revise_message="REVISE",
            finish_message="FINISH",
        )

        assert outcome.stopped is False
        assert outcome.passed is False
        assert outcome.turn_index == 3

    async def test_reports_passed_true_when_the_last_seen_verdict_passed(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict={"passed": False}),
            _ScriptedTurn(verdict={"passed": True}),
        ], max_turns=2)
        driver = PhaseDriver()

        outcome = await driver.run_verify_phase(
            agent, _GOAL, 0,
            verify_message="VERIFY",
            revise_message="REVISE",
            finish_message="FINISH",
        )

        assert outcome.stopped is False
        assert outcome.passed is True

    async def test_no_verdict_turn_is_a_no_op(self) -> None:
        agent = _FakeAgent([
            _ScriptedTurn(verdict=None),
            _ScriptedTurn(status="stop", result=_stop_result()),
        ], max_turns=10)
        driver = PhaseDriver()

        outcome = await driver.run_verify_phase(
            agent, _GOAL, 0,
            verify_message="VERIFY",
            revise_message="REVISE",
            finish_message="FINISH",
        )

        assert outcome.stopped is True
        assert agent.injected_messages == ["VERIFY"]
