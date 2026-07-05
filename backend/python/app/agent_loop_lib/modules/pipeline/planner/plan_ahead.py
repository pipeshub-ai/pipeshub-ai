from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import PlanningError
from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan, Planner, parse_confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_PLAN_SCHEMA = {
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

_PLAN_SYSTEM = (
    "You are a planning agent. Produce a complete, ordered multi-phase plan to accomplish the goal. "
    "Each phase has a clear name, description, and the tools required. "
    "Plan all phases upfront before any execution begins."
)


class PlanAheadPlanner(Planner):
    """
    Produces a full multi-phase plan upfront.
    Suitable for high-complexity goals that benefit from global planning
    before any execution begins.
    """

    def __init__(self, model: "SupportsStructuredComplete | None" = None) -> None:
        self._model = model

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            return Plan(goal=goal, phases=[], confidence=Confidence.LOW)

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"Success criteria: {'; '.join(goal.success_criteria) or 'none'}\n"
            f"Constraints: {'; '.join(goal.constraints) or 'none'}"
        )
        msg = UserMessage(content=prompt)
        try:
            response = await self._model.complete_structured(
                messages=[msg],
                output_schema=_PLAN_SCHEMA,
                system=_PLAN_SYSTEM,
            )
            result = response.data
            phases = [
                Phase(name=p["name"], description=p["description"], tools=p.get("tools") or [])
                for p in result["phases"]
            ]
            return Plan(goal=goal, phases=phases, confidence=parse_confidence(result.get("confidence", "medium")))
        except (KeyError, ValueError) as e:
            raise PlanningError(f"PlanAheadPlanner failed to parse plan: {e}") from e
