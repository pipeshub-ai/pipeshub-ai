from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, Planner

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsComplete

_SYSTEM_PROMPT = (
    "You are a planning agent. Decompose goals into clear, ordered execution phases, as a "
    "numbered list, one phase per line: `1. Phase Name: description`. Mention the tools a phase "
    "needs, if any specific one applies, in that phase's own description."
)


class DefaultPlanner(Planner):
    """LLM-driven planner that decomposes goals into an ordered plan."""

    def __init__(self, model: "SupportsComplete") -> None:
        self._model = model

    async def plan(self, goal: Goal) -> Plan:
        user_text = (
            f"Decompose this goal into execution phases:\n\n"
            f"Goal: {goal.description}\n\n"
            f"Requirements:\n{chr(10).join(f'- {r}' for r in goal.requirements)}\n\n"
            f"Success criteria:\n{chr(10).join(f'- {s}' for s in goal.success_criteria)}"
        )
        user_msg = UserMessage(content=user_text)
        response = await self._model.complete(messages=[user_msg], system=_SYSTEM_PROMPT)
        return Plan(goal=goal, text=response.message.text)
