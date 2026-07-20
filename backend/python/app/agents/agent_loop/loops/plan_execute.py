"""`planExecute` mode's `LoopStrategy`: single-agent Plan -> Critique ->
Replan -> Execute -> Verify -> Revise, composed over the SAME `create_plan`
/ `critique_plan` / `verify_result` / `replan` TOOLS and the SAME
`PhaseDriver` (`app/agent_loop_lib/agent/phase_driver.py`) `OrchestratorLoop`
uses for its own Phase 1/Phase 3 — this is that convergence (previously
`planExecute` ran an entirely different shape: `PlanAheadPlanner` produced
one free-text plan upfront with no critique, no verify, and no replan, then
handed off straight to `ReActLoop`).

The one thing this class does NOT share with `OrchestratorLoop`: Phase 2
here is the SAME agent executing directly with its own granted tools —
never `spawn_agent`. That's the actual definition of `planExecute` vs
`deep`: single agent with rigor, vs multi-agent delegation. Accordingly
this module registers `create_plan`/`critique_plan`/`verify_result`/
`replan` but never `spawn_agent`/`DomainSpawnAgentTool` (contrast
`register_coordination_tools()` in `orchestrator.py`, which does).

Also why this can't just BE `agent_loop_lib`'s own generic
`PlanCritiqueExecuteLoop`: that class's Phase 3 tells the model to call
`task_complete` to finish — a tool PipesHub's `PipesHubToolLoader` never
registers, since a PipesHub run's actual termination signal is a plain,
tool-call-free reply (see `Agent.step()`). Every instruction message here
is phrased around that same convention `OrchestratorLoop` already uses
("reply now, no more tool calls" — never "call task_complete").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.loops import LoopStrategy
from app.agent_loop_lib.agent.phase_driver import PhaseDriver
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.builtin.planning.replan import ReplanTool
from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool

if TYPE_CHECKING:
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.core.types import AgentResult, Goal
    from app.agent_loop_lib.tools.registry import ToolRegistry

__all__ = [
    "PLANNING_TOOL_NAMES",
    "PlanCritiqueExecuteLoop",
    "build_single_agent_planning_instructions",
    "register_planning_tools",
]

# The four tools `planExecute` mode's top-level grant always includes, on
# top of whatever domain/composed tools it already has — deliberately
# EXCLUDES `spawn_agent` (see module docstring: this mode never delegates).
PLANNING_TOOL_NAMES: tuple[str, ...] = (
    "create_plan",
    "critique_plan",
    "verify_result",
    "replan",
)


def register_planning_tools(registry: "ToolRegistry") -> None:
    """Registers the four builtin tools `PlanCritiqueExecuteLoop` composes
    over onto a fresh `ToolRegistry` — the `planExecute`-mode counterpart to
    `orchestrator.py::register_coordination_tools()`, minus `spawn_agent`.
    Call once per request (see `factory.py`)."""
    for tool in (CreatePlanTool(), CritiquePlanTool(), VerifyResultTool(), ReplanTool()):
        registry.register_tool(tool)


def build_single_agent_planning_instructions() -> str:
    """Phase 1's planning message. Unlike `orchestrator.py::build_phase1_
    planning_instructions` (which decomposes a goal into steps handed to
    SEPARATE spawned sub-agents), every step here is executed by THIS SAME
    agent — so a step's `domain` is purely descriptive (useful to a human/
    critique reading the plan, never used for dispatch), and `tool_names`
    is omitted entirely: the agent already has its full tool grant and
    doesn't need to pre-declare which of its own tools it'll reach for."""
    return (
        "Phase 1 -- PLAN: decompose this goal into an ordered list of "
        "steps YOU will execute yourself (this mode does not delegate to "
        "other agents) by calling create_plan with a structured `steps` "
        "array. Each step must have:\n"
        "- `id`: short stable identifier\n"
        "- `description`: what this step accomplishes and how (which of "
        "your tools, if any, it needs)\n"
        "- `domain`: a short descriptive label for the kind of work (e.g. "
        "'research', 'calculation', 'write-up') — for readability only, "
        "not used for dispatch\n"
        "- `depends_on`: IDs of steps this step's work builds on (empty "
        "if independent)\n\n"
        "A SIMPLE goal needs exactly one step, or none at all if you can "
        "answer directly — do not manufacture multiple steps just to use "
        "the machinery.\n\n"
        "Then call critique_plan with the resulting text. If critique_plan "
        "returns passed=false, revise and call create_plan again with "
        "corrected steps. Do not start executing until critique_plan "
        "passes."
    )


class PlanCritiqueExecuteLoop(LoopStrategy):
    """`planExecute` mode's loop. See module docstring for the full
    rationale; in one line: `OrchestratorLoop`'s Phase 1/Phase 3 rigor
    (via the same `PhaseDriver`), Phase 2 executed by this single agent
    directly instead of dispatching to spawned sub-agents."""

    name = "plan_critique_execute_single_agent"

    def __init__(self, *, max_planning_rounds: int = 3, max_verify_rounds: int = 2) -> None:
        self._phases = PhaseDriver(max_planning_rounds=max_planning_rounds, max_verify_rounds=max_verify_rounds)

    async def run(self, agent: "Agent", goal: "Goal") -> "AgentResult":
        turn_index = agent.start_turn_index

        # --- Phase 1: PLAN + CRITIQUE ---
        planning = await self._phases.run_planning_phase(
            agent, goal, turn_index,
            planning_message=build_single_agent_planning_instructions(),
            replan_message=(
                "Phase 1 -- REPLAN: critique_plan did not pass. Revise "
                "the plan to address the issues raised, then call "
                "create_plan again with corrected steps and critique_plan "
                "again."
            ),
        )
        if planning.stopped:
            return planning.result
        turn_index = planning.turn_index

        if not planning.passed and turn_index < agent.max_turns:
            await agent.inject_user_message(
                "Phase 1 -- FINALIZE: critique did not pass after maximum "
                "revision rounds. Call create_plan ONE MORE TIME with your "
                "best corrected plan incorporating all feedback. This plan "
                "will be executed as-is — no further critique."
            )
            outcome = await agent.step(goal, turn_index)
            turn_index += 1
            if outcome.status == "stop":
                return outcome.result

        # --- Phase 2: EXECUTE, Phase 3: VERIFY + retry ---
        verify = await self._phases.run_verify_phase(
            agent, goal, turn_index,
            verify_message=(
                "Phase 2 -- EXECUTE: the plan is approved. Execute it step "
                "by step using your available tools. Before replying with "
                "your final answer, call verify_result with your candidate "
                "output. If verify_result fails, revise your approach "
                "(call replan first if the plan itself needs to change) "
                "and call verify_result again."
            ),
            revise_message=(
                "Phase 3 -- REVISE: verify_result did not pass. Call "
                "replan if your plan itself needs to change, otherwise "
                "revise your approach directly, then call verify_result "
                "again."
            ),
            finish_message=(
                "Phase 3 -- FINISH: verification still failing after "
                "retries. Reply now with your best current answer "
                "(no more tool calls)."
            ),
        )
        if verify.stopped:
            return verify.result

        return await agent.fail(goal, f"Exceeded max_turns={agent.max_turns}", event="agent_failed")
