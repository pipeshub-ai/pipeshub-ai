from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, Planner

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsComplete

_STEP_SYSTEM = (
    "You are a step-planner agent. Given a goal, produce the single most important next step. "
    "Be concrete and actionable. Respond with just that one step, nothing else."
)


class StepPlanner(Planner):
    """
    Produces one step at a time.
    Runs Plan → Execute → Observe in a tight loop, replanning after each step.
    """

    def __init__(self, model: "SupportsComplete | None" = None) -> None:
        self._model = model

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            return Plan(goal=goal, text=goal.description)

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"Determine the single best next step."
        )
        msg = UserMessage(content=prompt)
        response = await self._model.complete(messages=[msg], system=_STEP_SYSTEM)
        return Plan(goal=goal, text=response.message.text)
