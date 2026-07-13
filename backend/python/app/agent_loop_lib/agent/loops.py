from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.agent_loop_lib.core.types import (
    AgentResult,
    AgentTurn,
    Goal,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.agent import Agent

"""LoopStrategy: the SHAPE of an agent's turn loop, inverted from control
(Agent used to own a single fixed `for turn in range(max_turns)` loop with a
handful of before/after mount points) to a driver: a `LoopStrategy` owns the
loop itself and calls `Agent.step()` — the one, fixed, hook-instrumented unit
of "call the model once, maybe run tool calls" — as many times as its shape
needs, in whatever order/branching pattern it wants.

This is the framework's answer to "how do I build a Plan -> Critique ->
Execute -> Verify agent, or an incremental planner, or anything in between"
— write a small class that calls `agent.step()`, `agent.inject_user_message()`,
and `agent.last_tool_result()` in a loop. No Agent/runtime/tool-executor code
changes needed for a new loop shape (Open/Closed): see `examples/` for five
different loops built this way.

Every deterministic concern (budget, cancellation, deadline warnings,
truncation recovery, guardrails, permission/approval) still fires INSIDE
`Agent.step()` via the shared HookRegistry kernel, regardless of which loop
is driving — a loop can change WHEN/HOW OFTEN the model is called, never
whether the deterministic guards run around each call.
"""

__all__ = [
    "StepOutcome",
    "LoopStrategy",
    "ReActLoop",
    "SingleShotLoop",
    "ReflexionLoop",
    "PlanExecuteLoop",
    "PlanCritiqueExecuteLoop",
    "IncrementalLoop",
]


@dataclass
class StepOutcome:
    """Returned by `Agent.step()`.

    `status == "continue"`: the turn loop should keep going — `turn` is the
    just-completed `AgentTurn` (already recorded on `agent`).
    `status == "stop"`: the run is over — `result` is the fully-built
    `AgentResult` (already emitted/persisted via `agent.succeed()`/`agent.fail()`).
    A `LoopStrategy` just returns `outcome.result` unchanged in this case;
    it never needs to know WHY the run stopped (task_complete, no tool
    calls, a blocked hook, cancellation, max_turns, ...).
    """

    status: Literal["continue", "stop"]
    turn: AgentTurn | None = None
    result: AgentResult | None = None


class LoopStrategy(ABC):
    """Governs the turn-loop's SHAPE for one agent run. See module
    docstring. `name` is descriptive only (shows up in timeline entries)."""

    name: str = "base"

    @abstractmethod
    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        """Drive the run to completion, calling `agent.step()` as needed,
        and return the final `AgentResult`."""


# Below this many characters, the last assistant turn's text is treated as
# a stray fragment (a lone acknowledgement, a mid-thought sentence) rather
# than a real answer worth returning — see `_finish_after_max_turns`.
_MIN_DEGRADED_OUTPUT_CHARS = 40


async def _finish_after_max_turns(agent: "Agent", goal: Goal) -> AgentResult:
    """Shared max_turns-exhausted tail for every loop shape below.

    A hard `fail()` here throws away whatever the model DID produce, even
    when it was most of the way to a real answer — a slower-converging
    small model hits the turn cap mid-answer far more often than a larger
    one does. If the last assistant turn already has substantive text,
    return it as a degraded SUCCESS (flagged in the summary/detail) instead
    of an opaque error; only a run that produced no usable text still
    fails, since there is nothing better to hand back.
    """
    last_text = agent.last_assistant_text().strip()
    if len(last_text) >= _MIN_DEGRADED_OUTPUT_CHARS:
        return await agent.succeed(
            goal, last_text, [],
            event="agent_complete_partial",
            summary=f"Exceeded max_turns={agent.max_turns} — returning the best-effort answer from the last turn",
            detail={"degraded": True, "reason": f"Exceeded max_turns={agent.max_turns}"},
        )
    return await agent.fail(goal, f"Exceeded max_turns={agent.max_turns}", event="agent_failed")


class ReActLoop(LoopStrategy):
    """Default: reason -> act -> observe, one `step()` per turn, until a
    turn stops the run or `agent.max_turns` is exhausted. Every other loop
    in this module either wraps this one or reimplements the same shape
    with extra message injection around each `step()` call."""

    name = "react"

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        for turn_index in range(agent.start_turn_index, agent.max_turns):
            outcome = await agent.step(goal, turn_index)
            if outcome.status == "stop":
                return outcome.result
        return await _finish_after_max_turns(agent, goal)


class SingleShotLoop(LoopStrategy):
    """For trivial tasks: exactly one `step()` — one model call, plus
    whichever tool calls that single turn requested — then the run ends
    regardless of whether the model would have asked for more turns. Use
    for cheap, low-latency agents-as-tools where a full back-and-forth
    conversation would be overkill (see `examples/02_orchestrator.py`)."""

    name = "single_shot"

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        outcome = await agent.step(goal, agent.start_turn_index)
        if outcome.status == "stop":
            return outcome.result
        return await agent.succeed(
            goal,
            agent.last_assistant_text(),
            event="single_shot_complete",
            summary="Single-shot agent completed after its one turn",
        )


class ReflexionLoop(LoopStrategy):
    """Self-critique between turns (Shinn et al., 'Reflexion'): after each
    non-terminal turn, runs a (pluggable) critique over that turn's outcome
    and, if it flags a problem, injects a short course-correction message
    before the model's next turn.

    `critique_fn` defaults to a cheap, deterministic, offline heuristic
    (flag failing tool calls) so this strategy is useful and fully testable
    with zero extra LLM calls out of the box.
    """

    name = "reflexion"

    def __init__(self, critique_fn: Callable[[AgentTurn], Awaitable[str | None]] | None = None) -> None:
        self._critique_fn = critique_fn or self._default_critique

    @staticmethod
    async def _default_critique(turn: AgentTurn) -> str | None:
        errors = [tr for tr in turn.tool_results if tr.is_error]
        if not errors:
            return None
        names = ", ".join(sorted({tr.name for tr in errors}))
        return (
            f"Self-critique: the last turn had {len(errors)} failing tool call(s) ({names}). "
            "Reconsider your approach before trying again — don't just repeat the same call."
        )

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        for turn_index in range(agent.start_turn_index, agent.max_turns):
            outcome = await agent.step(goal, turn_index)
            if outcome.status == "stop":
                return outcome.result
            note = await self._critique_fn(outcome.turn)
            if note:
                await agent.inject_user_message(note)
        return await _finish_after_max_turns(agent, goal)


class PlanExecuteLoop(LoopStrategy):
    """Plan-and-Execute: calls a `Planner` exactly once, upfront, and
    injects the resulting plan as a user message the model executes step by
    step — then hands off to `ReActLoop` for execution. No further
    programmatic planning happens mid-run under this strategy (contrast
    with the `replan`/`create_plan` TOOLS, still callable regardless of
    active loop, which are the model's own probabilistic escape hatch)."""

    name = "plan_execute"

    def __init__(self, planner: object) -> None:
        self._planner = planner

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        plan = await self._planner.plan(goal)
        # Every `Planner` in `modules/pipeline/planner/` now returns the
        # model's raw text, unparsed — inject it verbatim so nothing (its
        # own formatting, caveats between steps, ...) is lossily re-joined.
        if plan.text:
            await agent.inject_user_message(
                f"## Execution Plan\n\n{plan.text}\n\nExecute this plan step by step."
            )
        return await ReActLoop().run(agent, goal)


class PlanCritiqueExecuteLoop(LoopStrategy):
    """Plan -> Critique -> Replan -> Execute -> Verify -> Replan-if-needed.

    Pure composition over the `create_plan` / `critique_plan` / `replan` /
    `verify_result` / `task_complete` TOOLS (see `tools/builtin/planning/`)
    — this class only sequences PHASES by injecting instruction messages
    and inspecting each step's tool results; it never calls a planner or
    critic module directly. See `examples/03_plan_critique_execute.py`.
    """

    name = "plan_critique_execute"

    def __init__(self, *, max_planning_rounds: int = 3, max_verify_rounds: int = 2) -> None:
        self._max_planning_rounds = max_planning_rounds
        self._max_verify_rounds = max_verify_rounds

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        await agent.inject_user_message(
            "Phase 1 — PLAN: call create_plan, then call critique_plan with your plan. "
            "If critique_plan returns passed=false, revise the plan and call "
            "critique_plan again. Do not start executing until critique_plan passes."
        )
        turn_index = agent.start_turn_index
        planning_rounds = 0
        while planning_rounds < self._max_planning_rounds and turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            verdict = agent.last_tool_result("critique_plan")
            if verdict is not None:
                planning_rounds += 1
                if isinstance(verdict, dict) and verdict.get("passed"):
                    break
                await agent.inject_user_message(
                    "Phase 1 — REPLAN: critique_plan did not pass. Revise the plan to address "
                    "the issues raised, then call critique_plan again."
                )

        await agent.inject_user_message(
            "Phase 2 — EXECUTE: the plan is approved. Execute it step by step. Before "
            "calling task_complete, call verify_result with your candidate final output."
        )
        verify_rounds = 0
        while turn_index < agent.max_turns:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            verdict = agent.last_tool_result("verify_result")
            if verdict is not None and not (isinstance(verdict, dict) and verdict.get("passed")):
                verify_rounds += 1
                if verify_rounds >= self._max_verify_rounds:
                    await agent.inject_user_message(
                        "Phase 3 — FINISH: verification still failing after retries. "
                        "Call task_complete now with your best current answer."
                    )
                else:
                    await agent.inject_user_message(
                        "Phase 3 — REPLAN: verify_result did not pass. Call replan to address "
                        "the issues, execute the revised steps, then call verify_result again."
                    )
        return await _finish_after_max_turns(agent, goal)


class IncrementalLoop(LoopStrategy):
    """Plan-next-step -> Execute -> Verify+Reflect -> Plan-next-step, ...

    Same underlying tool primitives as `PlanCritiqueExecuteLoop` (plan/
    verify), composed into a different shape: instead of one upfront plan
    covering the whole goal, this loop asks for exactly ONE next step at a
    time and verifies it before planning the next — useful when later steps
    genuinely depend on what earlier ones discover. See
    `examples/04_incremental_planner.py`.
    """

    name = "incremental_plan_execute"

    def __init__(self, *, max_steps: int = 20) -> None:
        self._max_steps = max_steps

    async def run(self, agent: "Agent", goal: Goal) -> AgentResult:
        await agent.inject_user_message(
            "Work incrementally: use write_todos to track exactly ONE 'in_progress' next "
            "step at a time. Execute just that step, then call verify_result on what you "
            "did before moving on. If verify_result fails, reflect on why before retrying "
            "the same step — do not plan further steps until this one verifies."
        )
        turn_index = agent.start_turn_index
        steps_done = 0
        while turn_index < agent.max_turns and steps_done < self._max_steps:
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result
            verdict = agent.last_tool_result("verify_result")
            if verdict is not None:
                steps_done += 1
                if isinstance(verdict, dict) and verdict.get("passed"):
                    await agent.inject_user_message(
                        "That step verified. Plan the next incremental step via write_todos, "
                        "or call task_complete if the whole goal is now satisfied."
                    )
                else:
                    await agent.inject_user_message(
                        "That step did not verify. Reflect on what went wrong and retry the "
                        "SAME step before planning any further ones."
                    )
        return await _finish_after_max_turns(agent, goal)
