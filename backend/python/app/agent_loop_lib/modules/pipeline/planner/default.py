from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, Planner, extract_trailing_confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsComplete

_SYSTEM_PROMPT = (
    "You are a planning agent. Decompose goals into clear, ordered execution phases, as a "
    "numbered list, one phase per line: `1. Phase Name: description`. Mention the tools a phase "
    "needs, if any specific one applies, in that phase's own description. "
    "End your response with exactly one trailing line, after the last phase, in the form "
    "`Confidence: low|medium|high` reflecting how confident you are that this plan is complete "
    "and correct for the goal — do not add anything after that line."
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
        text = response.message.text
        return Plan(goal=goal, text=text, confidence=extract_trailing_confidence(text))
