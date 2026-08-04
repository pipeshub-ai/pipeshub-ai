from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.single_shot_runner import (
    StructuredSingleShotError,
    run_structured_single_shot,
)
from app.agent_loop_lib.core.types import Goal, Intent

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

_INTENT_SCHEMA_HINT = (
    "\n\nYour JSON output must include:\n"
    '- "parsed_intent": clear, unambiguous restatement of what the user wants\n'
    '- "context": object of key context strings (optional, default {})\n'
)

_INTENT_SYSTEM = (
    "You parse user messages into structured intents. "
    "Restate the user's request clearly and extract any relevant context. "
    "Be precise and literal — do not add assumptions."
)


class IntentParser:
    """Parses a raw user message into a structured Intent via a single-shot
    `Agent` run (`SingleShotLoop` + `task_complete`). Only called at the top
    level; sub-agents receive a Goal directly."""

    def __init__(self, runtime: "AgentRuntime", model_spec: "ModelSpec") -> None:
        self._runtime = runtime
        self._model_spec = model_spec

    async def parse(self, message: str) -> Intent:
        try:
            result = await run_structured_single_shot(
                name="intent-parser",
                system_prompt=_INTENT_SYSTEM,
                goal=Goal(description=message),
                runtime=self._runtime,
                model_spec=self._model_spec,
                output_schema_hint=_INTENT_SCHEMA_HINT,
            )
        except StructuredSingleShotError as exc:
            return Intent(raw_message=message, parsed_intent=message, context={})

        parsed_intent = str(result.get("parsed_intent") or message).strip() or message
        context = result.get("context") or {}
        if not isinstance(context, dict):
            context = {}
        return Intent(
            raw_message=message,
            parsed_intent=parsed_intent,
            context={str(k): str(v) for k, v in context.items()},
        )
