from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Goal, Intent, UserMessage
from app.agent_loop_lib.modules.pipeline.intent.base import IntentParser

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


class DefaultIntentParser(IntentParser):
    """LLM-powered intent parser for top-level agent entry."""

    def __init__(self, model: "SupportsStructuredComplete") -> None:
        self._model = model

    async def parse(self, message: str, context: dict | None = None) -> Intent:
        system = "Extract the user's intent from their message. Identify what they want to accomplish."
        user_text = f"User message: {message}\n\nContext: {context or {}}"
        user_msg = UserMessage(content=user_text)
        schema = {
            "type": "object",
            "properties": {
                "parsed_intent": {"type": "string"},
                "context": {"type": "object"},
            },
            "required": ["parsed_intent"],
        }
        response = await self._model.complete_structured(
            messages=[user_msg],
            output_schema=schema,
            system=system,
        )
        result = response.data
        return Intent(
            raw_message=message,
            parsed_intent=result["parsed_intent"],
            context=result.get("context", {}),
        )

    async def to_goal(self, intent: Intent) -> Goal:
        system = "Convert a user intent into a structured goal with requirements and success criteria."
        user_text = f"Intent: {intent.parsed_intent}"
        user_msg = UserMessage(content=user_text)
        schema = {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "requirements": {"type": "array", "items": {"type": "string"}},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["description", "requirements", "success_criteria"],
        }
        response = await self._model.complete_structured(
            messages=[user_msg],
            output_schema=schema,
            system=system,
        )
        result = response.data
        return Goal(
            description=result["description"],
            requirements=result["requirements"],
            success_criteria=result["success_criteria"],
            constraints=result.get("constraints", []),
        )
