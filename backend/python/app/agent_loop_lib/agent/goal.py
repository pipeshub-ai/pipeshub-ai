from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, Intent, UserMessage

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_GOAL_SCHEMA = {
    "type": "object",
    "required": ["description"],
    "properties": {
        "description": {
            "type": "string",
            "description": "One-sentence summary of the goal",
        },
        "requirements": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Explicit requirements the agent must satisfy",
        },
        "success_criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Observable conditions that indicate task completion",
        },
        "constraints": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Hard constraints the agent must not violate",
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Information missing that could not be inferred",
        },
    },
}

_GOAL_SYSTEM = (
    "You convert a parsed user intent into a structured goal. "
    "Extract requirements, success criteria, constraints, and any information gaps. "
    "Be concrete and measurable. Do not invent facts not present in the intent."
)


class GoalBuilder:
    """
    Converts a parsed Intent into a structured Goal with requirements,
    success criteria, constraints, and identified gaps.
    Used at the top-level only — sub-agents receive Goals directly.
    """

    def __init__(self, model: "SupportsStructuredComplete | None" = None) -> None:
        self._model = model

    async def build(self, intent: Intent) -> Goal:
        if self._model is None:
            return Goal(description=intent.parsed_intent)

        prompt = (
            f"Intent: {intent.parsed_intent}\n"
            f"Context: {intent.context}"
        )
        messages = [UserMessage(content=prompt)]
        response = await self._model.complete_structured(
            messages=messages,
            output_schema=_GOAL_SCHEMA,
            system=_GOAL_SYSTEM,
        )
        result = response.data
        return Goal(
            description=result["description"],
            requirements=result.get("requirements", []),
            success_criteria=result.get("success_criteria", []),
            constraints=result.get("constraints", []),
            gaps=result.get("gaps", []),
        )
