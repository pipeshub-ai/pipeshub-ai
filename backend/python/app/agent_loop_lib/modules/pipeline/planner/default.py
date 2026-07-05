from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import PlanningError
from app.agent_loop_lib.core.types import Goal, UserMessage
from app.agent_loop_lib.modules.pipeline.planner.base import Phase, Plan, Planner, parse_confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


class DefaultPlanner(Planner):
    """LLM-driven planner that decomposes goals into ordered phases."""

    def __init__(self, model: "SupportsStructuredComplete") -> None:
        self._model = model

    async def plan(self, goal: Goal) -> Plan:
        user_text = (
            f"Decompose this goal into execution phases:\n\n"
            f"Goal: {goal.description}\n\n"
            f"Requirements:\n{chr(10).join(f'- {r}' for r in goal.requirements)}\n\n"
            f"Success criteria:\n{chr(10).join(f'- {s}' for s in goal.success_criteria)}"
        )
        user_msg = UserMessage(content=user_text)
        system_prompt = (
            "You are a planning agent. Decompose goals into clear, ordered execution phases. "
            "Each phase has a name, description, and the tools needed."
        )
        schema = {
            "type": "object",
            "properties": {
                "phases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "tools": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "description"],
                    },
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["phases", "confidence"],
        }

        try:
            response = await self._model.complete_structured(
                messages=[user_msg],
                output_schema=schema,
                system=system_prompt,
            )
            result = response.data
            phases = [
                Phase(
                    name=p["name"],
                    description=p["description"],
                    tools=p.get("tools") or [],
                )
                for p in result["phases"]
            ]
            confidence = parse_confidence(result.get("confidence", "medium"))
        except (PlanningError, KeyError, ValueError) as e:
            raise PlanningError(f"Failed to parse plan: {e}") from e

        return Plan(goal=goal, phases=phases, confidence=confidence)
