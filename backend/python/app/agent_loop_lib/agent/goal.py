from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.single_shot_runner import (
    StructuredSingleShotError,
    run_structured_single_shot,
)
from app.agent_loop_lib.core.structured_output import coerce_list
from app.agent_loop_lib.core.types import Goal, Intent

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

_GOAL_SCHEMA_HINT = (
    "\n\nYour JSON output must include:\n"
    '- "description": one-sentence summary of the goal\n'
    '- "requirements": array of strings (optional)\n'
    '- "success_criteria": array of strings (optional)\n'
    '- "constraints": array of strings (optional)\n'
    '- "gaps": array of strings (optional)\n'
)

_GOAL_SYSTEM = (
    "You convert a parsed user intent into a structured goal. "
    "Extract requirements, success criteria, constraints, and any information gaps. "
    "Be concrete and measurable. Do not invent facts not present in the intent."
)


class GoalBuilder:
    """Converts a parsed Intent into a structured Goal via a single-shot
    `Agent` run. Used at the top level only — sub-agents receive Goals
    directly."""

    def __init__(self, runtime: "AgentRuntime | None" = None, model_spec: "ModelSpec | None" = None) -> None:
        self._runtime = runtime
        self._model_spec = model_spec

    async def build(self, intent: Intent) -> Goal:
        if self._runtime is None or self._model_spec is None:
            return Goal(description=intent.parsed_intent)

        prompt = f"Intent: {intent.parsed_intent}\nContext: {intent.context}"
        try:
            result = await run_structured_single_shot(
                name="goal-builder",
                system_prompt=_GOAL_SYSTEM,
                goal=Goal(description=prompt),
                runtime=self._runtime,
                model_spec=self._model_spec,
                output_schema_hint=_GOAL_SCHEMA_HINT,
            )
        except StructuredSingleShotError:
            return Goal(description=intent.parsed_intent)

        description = str(result.get("description") or intent.parsed_intent).strip() or intent.parsed_intent
        return Goal(
            description=description,
            requirements=[str(x) for x in coerce_list(result.get("requirements"))],
            success_criteria=[str(x) for x in coerce_list(result.get("success_criteria"))],
            constraints=[str(x) for x in coerce_list(result.get("constraints"))],
            gaps=[str(x) for x in coerce_list(result.get("gaps"))],
        )
