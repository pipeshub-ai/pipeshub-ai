"""`PhaseDriver`: the round-limited "inject instructions -> step until a
gate tool's verdict passes or the round budget is exhausted -> nudge and
retry" looping mechanics shared by every Plan -> Critique -> Execute ->
Verify style `LoopStrategy`.

Extracted from `PlanCritiqueExecuteLoop` (this module) and PipesHub's
`OrchestratorLoop` (`app/agents/agent_loop/loops/orchestrator.py`), which
each independently hand-rolled a near-identical `while rounds < max and
turn_index < agent.max_turns: step(); check verdict; nudge` loop around
`critique_plan`/`verify_result` tool results before this existed — the
DUPLICATION was in the control flow (how many rounds, when to stop, how to
detect `agent.step()` ending the whole run), never in the phase-specific
CONTENT (the exact instruction text, what Phase 2 does in between, whether
there's a "you called create_plan but not critique_plan" nudge). This
class owns only the former; every caller still supplies its own message
text and decides its own Phase 2 shape — `PhaseDriver` never becomes a
generic "run my whole agent" driver, just the twice-duplicated gate-loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult, Goal

__all__ = ["PhaseDriver", "PhaseOutcome"]


@dataclass
class PhaseOutcome:
    """Result of running one gated phase via `PhaseDriver`.

    `stopped=True` means some `agent.step()` call inside this phase ended
    the WHOLE run (`task_complete`, a blocking hook, cancellation, ...) —
    the caller's `LoopStrategy.run()` MUST return `result` immediately and
    never proceed to whatever phase would normally come next; `result` is
    always set in this case. When `stopped=False`, `passed` tells the
    caller whether the gate tool (`critique_plan`/`verify_result`) actually
    reported success within the round budget, or whether the budget simply
    ran out — `result` is always `None` in this case, since the run isn't
    over, and `turn_index` is always the next unused turn index either way.
    """

    stopped: bool
    turn_index: int
    passed: bool = False
    result: "AgentResult | None" = None


class PhaseDriver:
    """Runs one round-limited gated phase at a time. Stateless across
    calls — a single instance can drive both the planning phase and the
    verify phase of the same run, each with its own round budget."""

    def __init__(self, *, max_planning_rounds: int = 3, max_verify_rounds: int = 2) -> None:
        self._max_planning_rounds = max_planning_rounds
        self._max_verify_rounds = max_verify_rounds

    async def run_planning_phase(
        self,
        agent: "Agent",
        goal: "Goal",
        turn_index: int,
        *,
        planning_message: str,
        gate_tool_name: str = "critique_plan",
        replan_message: str,
        no_verdict_nudge: "Callable[[Agent], str | None] | None" = None,
    ) -> PhaseOutcome:
        """Plan -> Critique -> Replan: inject `planning_message`, then step
        until `gate_tool_name`'s last result reports `passed: true` or
        `max_planning_rounds` gate-tool calls have been seen, re-injecting
        `replan_message` after every round that didn't pass.

        `no_verdict_nudge(agent)`, when given, runs on a turn that produced
        NO `gate_tool_name` result at all (e.g. the model called the
        plan-producing tool but never the gate tool) — returning a message
        to inject, or `None` to stay silent that round. Callers without
        this extra nudge (e.g. `PlanCritiqueExecuteLoop`) simply omit it.
        """
        await agent.inject_user_message(planning_message)
        rounds = 0
        while rounds < self._max_planning_rounds and turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return PhaseOutcome(stopped=True, turn_index=turn_index, result=outcome.result)
            verdict = agent.last_tool_result(gate_tool_name)
            if verdict is not None:
                rounds += 1
                if isinstance(verdict, dict) and verdict.get("passed"):
                    return PhaseOutcome(stopped=False, turn_index=turn_index, passed=True)
                await agent.inject_user_message(replan_message)
            elif no_verdict_nudge is not None:
                nudge = no_verdict_nudge(agent)
                if nudge:
                    await agent.inject_user_message(nudge)
        return PhaseOutcome(stopped=False, turn_index=turn_index, passed=False)

    async def run_verify_phase(
        self,
        agent: "Agent",
        goal: "Goal",
        turn_index: int,
        *,
        verify_message: str,
        gate_tool_name: str = "verify_result",
        revise_message: str,
        finish_message: str,
    ) -> PhaseOutcome:
        """Execute (already done by the caller before this call) -> Verify
        -> Revise: inject `verify_message`, then step until `agent.max_turns`
        is exhausted, re-injecting `revise_message` after every failing
        `gate_tool_name` verdict, switching to the terminal `finish_message`
        once `max_verify_rounds` failing rounds have been seen (so the model
        stops looping and just answers with what it has).

        Unlike the planning phase, this never returns `passed=True` early —
        Phase 3 in both current callers always runs to `agent.max_turns` (the
        model itself decides when to call `task_complete`/reply, which is
        what actually ends the run via `outcome.status == "stop"`); `passed`
        here only reflects whether the LAST verdict seen was a pass, for
        callers that want to log/report it.
        """
        await agent.inject_user_message(verify_message)
        rounds = 0
        passed = False
        while turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return PhaseOutcome(stopped=True, turn_index=turn_index, result=outcome.result)
            verdict = agent.last_tool_result(gate_tool_name)
            if verdict is not None:
                passed = bool(isinstance(verdict, dict) and verdict.get("passed"))
                if not passed:
                    rounds += 1
                    if rounds >= self._max_verify_rounds:
                        await agent.inject_user_message(finish_message)
                    else:
                        await agent.inject_user_message(revise_message)
        return PhaseOutcome(stopped=False, turn_index=turn_index, passed=passed)
