from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import PlanningError
from app.agent_loop_lib.core.types import Confidence, Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan, Planner

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_STEP_SCHEMA = {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "tools": {"type": "array", "items": {"type": "string"}},
    },
}

_STEP_SYSTEM = (
    "You are a step-planner agent. Given a goal, produce the single most important next step. "
    "Be concrete and actionable. Return exactly one step."
)


class StepPlanner(Planner):
    """
    Produces one step at a time.
    Runs Plan → Execute → Observe in a tight loop, replanning after each step.
    """

    def __init__(self, model: "SupportsStructuredComplete | None" = None) -> None:
        self._model = model

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            return Plan(
                goal=goal,
                phases=[Phase(name="Execute", description=goal.description)],
                confidence=Confidence.MEDIUM,
            )

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"Determine the single best next step."
        )
        msg = UserMessage(content=prompt)
        try:
            response = await self._model.complete_structured(
                messages=[msg],
                output_schema=_STEP_SCHEMA,
                system=_STEP_SYSTEM,
            )
            result = response.data
            phase = Phase(
                name=result["name"],
                description=result["description"],
                tools=result.get("tools") or [],
            )
            return Plan(goal=goal, phases=[phase], confidence=Confidence.MEDIUM)
        except (KeyError, ValueError) as e:
            raise PlanningError(f"StepPlanner failed to parse step: {e}") from e
