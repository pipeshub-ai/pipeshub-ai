"""Deep-mode decomposition-quality regression harness (p4-orchestrator-evals).

Answers one question, repeatedly, across `DECOMPOSITION_EVAL_QUERIES`: for
this query, does `create_plan` (under the SAME Phase 1 planning
instructions `OrchestratorLoop` actually runs — see `orchestrator.py::
build_phase1_planning_instructions`) produce a structurally sound plan?
Deliberately narrower than an end-to-end deep-mode eval: it runs ONLY
Phase 1 (create_plan + critique_plan) against a real model, never Phase 2/3
(spawning real sub-agents, each with their own real model calls, would
multiply this harness's cost and latency for a question this harness isn't
asking). `Phase1OnlyLoop` below drives that phase via the SAME `PhaseDriver`
`OrchestratorLoop` itself uses (`app/agent_loop_lib/agent/phase_driver.py`)
— the one piece that actually determines decomposition quality (the
instructions text) is shared via `build_phase1_planning_instructions`; the
looping mechanics around it are shared via `PhaseDriver` for the same
reason `OrchestratorLoop` and `PlanCritiqueExecuteLoop` share it instead of
each hand-rolling their own plan/critique/replan loop.

Usage (needs a real model — this is NOT part of the default fast test
suite, see `tests/unit/agents/adapter/test_decomposition_harness.py` for
the offline-safe subset that IS):

    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.transport.registry import TransportRegistry

    transport_registry = TransportRegistry()
    transport_registry.register("anthropic", ...)  # real transport factory
    report = await run_decomposition_eval(
        transport_registry=transport_registry,
        model=ModelSpec(provider="anthropic", model="claude-sonnet-4-6"),
    )
    print(report.render_text())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import LoopStrategy
from app.agent_loop_lib.agent.phase_driver import PhaseDriver
from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.domain_agents import DOMAIN_AGENT_DEFINITIONS
from app.agents.agent_loop.evals.decomposition_queries import (
    DECOMPOSITION_EVAL_QUERIES,
    DecompositionEvalQuery,
)
from app.agents.agent_loop.evals.decomposition_scorer import DecompositionScore, score_plan
from app.agents.agent_loop.loops.orchestrator import build_phase1_planning_instructions

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.core.types import AgentResult
    from app.agent_loop_lib.modules.pipeline.planner.base import Plan
    from app.agent_loop_lib.transport.registry import TransportRegistry

__all__ = [
    "DecompositionEvalReport",
    "Phase1OnlyLoop",
    "default_domain_block",
    "plan_for_query",
    "run_decomposition_eval",
]

_SYSTEM_PROMPT = (
    "You are a planning-only agent. Your ONLY job this run is to decompose the "
    "user's goal into a structured execution plan via create_plan, then validate "
    "it with critique_plan, revising until it passes (or you have made your best "
    "attempt). Do not try to answer the goal yourself — you have no tools for "
    "that here."
)


def default_domain_block() -> str:
    """A domain-catalog block for the eval agent's prompt, built straight
    from `DOMAIN_AGENT_DEFINITIONS` (`domain_agents.py`) — the same names
    and descriptions a real request's `_domain_overview()` would show,
    minus the per-request residual-tool listing that needs a live
    `ToolRegistry` this standalone harness has no reason to build."""
    lines = [f"- {d.name}: {d.description}" for d in DOMAIN_AGENT_DEFINITIONS]
    return "## Available Domains\n" + "\n".join(lines)


class Phase1OnlyLoop(LoopStrategy):
    """`OrchestratorLoop`'s Phase 1 (plan -> critique -> replan) in
    isolation, as its own `LoopStrategy` — everything after "did Phase 1
    produce a validated plan?" (Phase 2 dispatch, Phase 3 verify) is out of
    scope for a decomposition-quality eval."""

    name = "phase1_only_eval"

    def __init__(self, *, domain_block: str, max_planning_rounds: int = 3) -> None:
        self._domain_block = domain_block
        self._phases = PhaseDriver(max_planning_rounds=max_planning_rounds)

    async def run(self, agent: "Agent", goal: "Goal") -> "AgentResult":
        planning = await self._phases.run_planning_phase(
            agent, goal, agent.start_turn_index,
            planning_message=build_phase1_planning_instructions(self._domain_block),
            replan_message=(
                "REPLAN: critique_plan did not pass. Revise the plan to address the "
                "issues raised, then call create_plan again with corrected steps and "
                "critique_plan again."
            ),
        )
        if planning.stopped:
            return planning.result

        return await agent.succeed(
            goal, agent.last_assistant_text(),
            event="phase1_eval_complete",
            summary="Phase 1 planning loop finished (eval harness)",
        )


async def plan_for_query(
    query: str,
    *,
    transport_registry: "TransportRegistry",
    model: "ModelSpec",
    domain_block: str | None = None,
    max_turns: int = 6,
) -> "Plan | None":
    """Runs `Phase1OnlyLoop` for one query against a REAL model and
    returns the `Plan` `create_plan` stored in `STRUCTURED_PLAN_SLOT` —
    `None` if the agent never called it with a structured `steps` array
    before running out of turns (itself a scoreable failure, see
    `decomposition_scorer.score_plan`)."""
    from app.agent_loop_lib.modules.pipeline.planner.base import STRUCTURED_PLAN_SLOT

    registry = ToolRegistry()
    registry.register_tool(CreatePlanTool())
    registry.register_tool(CritiquePlanTool())

    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="decomposition-eval-agent",
        system_prompt=_SYSTEM_PROMPT,
        tool_names=["create_plan", "critique_plan"],
        model=model,
        loop=Phase1OnlyLoop(domain_block=domain_block or default_domain_block()),
        max_turns=max_turns,
    )
    agent = Agent(spec, runtime)
    await agent.run(Goal(description=query))

    scope = agent.scope
    if scope is None:
        return None
    return scope.get(STRUCTURED_PLAN_SLOT)


@dataclass
class DecompositionEvalReport:
    scores: list[DecompositionScore] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.scores if s.passed)

    @property
    def fail_count(self) -> int:
        return len(self.scores) - self.pass_count

    @property
    def pass_rate(self) -> float:
        return self.pass_count / len(self.scores) if self.scores else 0.0

    def render_text(self) -> str:
        lines = [f"Decomposition eval: {self.pass_count}/{len(self.scores)} passed ({self.pass_rate:.0%})", ""]
        for score in self.scores:
            status = "PASS" if score.passed else "FAIL"
            lines.append(f"[{status}] {score.query_id} ({score.step_count} step(s))")
            for issue in score.issues:
                lines.append(f"    - {issue.severity}: {issue.message}")
        return "\n".join(lines)


async def run_decomposition_eval(
    queries: tuple[DecompositionEvalQuery, ...] = DECOMPOSITION_EVAL_QUERIES,
    *,
    plan_for_query_fn: "Callable[[DecompositionEvalQuery], Awaitable[Plan | None]]",
) -> DecompositionEvalReport:
    """Runs `plan_for_query_fn` over every query and scores the result.

    Takes the plan-producing step as an injected callable rather than a
    `TransportRegistry`/`ModelSpec` pair directly — the harness's OWN
    correctness (does it score a plan correctly, does it aggregate
    correctly) is then testable with a fake `plan_for_query_fn` returning
    canned `Plan`s (see `test_decomposition_harness.py`), with zero live
    model calls; a real eval run supplies a closure over `plan_for_query`
    with a real `TransportRegistry`/`ModelSpec` instead (see this module's
    docstring for that usage).
    """
    scores = [score_plan(await plan_for_query_fn(query), query) for query in queries]
    return DecompositionEvalReport(scores=scores)
