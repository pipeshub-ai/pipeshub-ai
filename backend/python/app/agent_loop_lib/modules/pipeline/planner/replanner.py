from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import PlanningError
from app.agent_loop_lib.core.types import Confidence, Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan, Planner, parse_confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_REPLAN_SCHEMA = {
    "type": "object",
    "required": ["phases", "confidence"],
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

_REPLAN_SYSTEM = (
    "You are a replanning agent. Given a goal and a prior execution plan with its results, "
    "produce a revised plan covering remaining work. "
    "Only include phases that have NOT yet been completed. "
    "If the goal is already achieved, return an empty phases list with confidence=high."
)


class Replanner(Planner):
    """
    Re-evaluates the current plan after observing an execution result.
    Generates a revised plan if the prior result was insufficient or incorrect.
    """

    def __init__(
        self,
        model: "SupportsStructuredComplete | None" = None,
        prior_plan: Plan | None = None,
    ) -> None:
        self._model = model
        self._prior_plan = prior_plan

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            # No model: return empty plan (goal assumed complete)
            return Plan(goal=goal, phases=[], confidence=Confidence.LOW)

        prior_summary = ""
        if self._prior_plan is not None:
            phase_lines = "\n".join(
                f"- {p.name}: {p.description}" for p in self._prior_plan.phases
            )
            prior_summary = f"Prior plan phases:\n{phase_lines}\n"

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"{prior_summary}"
            f"Produce a revised plan for remaining work."
        )
        msg = UserMessage(content=prompt)
        try:
            response = await self._model.complete_structured(
                messages=[msg],
                output_schema=_REPLAN_SCHEMA,
                system=_REPLAN_SYSTEM,
            )
            result = response.data
            phases = [
                Phase(name=p["name"], description=p["description"], tools=p.get("tools") or [])
                for p in result["phases"]
            ]
            return Plan(goal=goal, phases=phases, confidence=parse_confidence(result.get("confidence", "medium")))
        except (KeyError, ValueError) as e:
            raise PlanningError(f"Replanner failed to parse revised plan: {e}") from e
