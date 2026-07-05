from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Intent, UserMessage

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import SupportsStructuredComplete


_INTENT_SCHEMA = {
    "type": "object",
    "required": ["parsed_intent"],
    "properties": {
        "parsed_intent": {
            "type": "string",
            "description": "Clear, unambiguous restatement of what the user wants to accomplish",
        },
        "context": {
            "type": "object",
            "description": "Key context extracted from the message (e.g. language, domain, constraints)",
            "additionalProperties": {"type": "string"},
        },
    },
}

_INTENT_SYSTEM = (
    "You parse user messages into structured intents. "
    "Restate the user's request clearly and extract any relevant context. "
    "Be precise and literal — do not add assumptions."
)


class IntentParser:
    """
    Parses a raw user message into a structured Intent using the model's
    complete_structured() method. Only called at the top level; sub-agents
    receive a Goal directly.
    """

    def __init__(self, model: "SupportsStructuredComplete") -> None:
        self._model = model

    async def parse(self, message: str) -> Intent:
        messages = [UserMessage(content=message)]
        response = await self._model.complete_structured(
            messages=messages,
            output_schema=_INTENT_SCHEMA,
            system=_INTENT_SYSTEM,
        )
        result = response.data
        return Intent(
            raw_message=message,
            parsed_intent=result["parsed_intent"],
            context=result.get("context", {}),
        )
