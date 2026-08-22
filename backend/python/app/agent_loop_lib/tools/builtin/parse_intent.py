from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent.goal import GoalBuilder
from app.agent_loop_lib.agent.intent import IntentParser
from app.agent_loop_lib.agent.single_shot_runner import build_task_complete_runtime
from app.agent_loop_lib.agent.spec import ModelSpec
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter

"""Intent-as-tool (closes the roadmap's final owner-notes gap): the
intent-parsing -> goal-building pipeline (agent/intent.py, agent/goal.py —
otherwise only reachable via `Agent.run_from_message()`, which nothing in
the CLI or serve app actually calls) becomes a plain callable tool. This
lets any agent turn a vague, free-text request from a user/tool-result/sub-
conversation into a structured `Goal` mid-run — e.g. an orchestrator role
handed a raw ticket body that wants to hand off a crisply-scoped goal to a
spawned sub-agent instead of the raw text.
"""


class ParseIntentTool(Tool):
    """Parses a raw message into a structured Intent, then a structured
    Goal (requirements/success_criteria/constraints/gaps), via the same
    single-shot `Agent` runs `Agent.run_from_message()` makes internally —
    exposed here as an ordinary tool call instead of a separate entrypoint."""

    def __init__(self, transport_registry: Any, provider: str) -> None:
        self._transport_registry = transport_registry
        self._provider = provider

    @property
    def name(self) -> str:
        return "parse_intent"

    @property
    def short_description(self) -> str:
        return "Parse a raw message into a structured goal."

    @property
    def description(self) -> str:
        return (
            "Parse a raw, free-text message into a structured goal (description, "
            "requirements, success_criteria, constraints, gaps) — use this to turn "
            "a vague request into something precise enough to act on or hand off."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/parse_intent"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="The raw message/request to parse",
                required=True,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        message: str = kwargs["message"]
        runtime = build_task_complete_runtime(self._transport_registry)
        model_spec = ModelSpec(provider=self._provider)
        intent = await IntentParser(runtime, model_spec).parse(message)
        goal = await GoalBuilder(runtime, model_spec).build(intent)
        return ToolOutput(
            success=True,
            data={
                "parsed_intent": intent.parsed_intent,
                "context": intent.context,
                "goal": {
                    "description": goal.description,
                    "requirements": goal.requirements,
                    "success_criteria": goal.success_criteria,
                    "constraints": goal.constraints,
                    "gaps": goal.gaps,
                },
            },
        )
