from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, Planner

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsComplete

_REPLAN_SYSTEM = (
    "You are a replanning agent. Given a goal and a prior execution plan with its results, "
    "produce a revised plan covering remaining work, as a numbered list, one phase per line: "
    "`1. Phase Name: description`. Only include phases that have NOT yet been completed. "
    "If the goal is already achieved, say so plainly instead of listing phases."
)


class Replanner(Planner):
    """
    Re-evaluates the current plan after observing an execution result.
    Generates a revised plan if the prior result was insufficient or incorrect.
    """

    def __init__(
        self,
        model: "SupportsComplete | None" = None,
        prior_plan_text: str | None = None,
    ) -> None:
        self._model = model
        self._prior_plan_text = prior_plan_text

    async def plan(self, goal: Goal) -> Plan:
        if self._model is None:
            # No model: return empty plan (goal assumed complete)
            return Plan(goal=goal, text="")

        prior_summary = f"Prior plan:\n{self._prior_plan_text}\n" if self._prior_plan_text else ""

        prompt = (
            f"Goal: {goal.description}\n"
            f"Requirements: {'; '.join(goal.requirements) or 'none'}\n"
            f"{prior_summary}"
            f"Produce a revised plan for remaining work."
        )
        msg = UserMessage(content=prompt)
        response = await self._model.complete(messages=[msg], system=_REPLAN_SYSTEM)
        return Plan(goal=goal, text=response.message.text)
