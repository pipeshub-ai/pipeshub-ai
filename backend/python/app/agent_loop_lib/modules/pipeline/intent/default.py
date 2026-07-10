from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.goal import GoalBuilder
from app.agent_loop_lib.agent.intent import IntentParser
from app.agent_loop_lib.agent.single_shot_runner import build_task_complete_runtime
from app.agent_loop_lib.agent.spec import ModelSpec
from app.agent_loop_lib.core.types import Goal, Intent
from app.agent_loop_lib.modules.pipeline.intent.base import IntentParser as IntentParserABC

if TYPE_CHECKING:
    from app.agent_loop_lib.transport.registry import TransportRegistry


class DefaultIntentParser(IntentParserABC):
    """LLM-powered intent parser for top-level agent entry — delegates to
    the shared single-shot `Agent` path in `agent/intent.py`."""

    def __init__(self, transport_registry: "TransportRegistry", provider: str = "anthropic") -> None:
        self._transport_registry = transport_registry
        self._provider = provider

    def _model_spec(self) -> ModelSpec:
        return ModelSpec(provider=self._provider)

    async def parse(self, message: str, context: dict | None = None) -> Intent:
        runtime = build_task_complete_runtime(self._transport_registry)
        intent = await IntentParser(runtime, self._model_spec()).parse(message)
        if context:
            merged = dict(intent.context)
            merged.update({str(k): str(v) for k, v in context.items()})
            intent = Intent(raw_message=intent.raw_message, parsed_intent=intent.parsed_intent, context=merged)
        return intent

    async def to_goal(self, intent: Intent) -> Goal:
        runtime = build_task_complete_runtime(self._transport_registry)
        return await GoalBuilder(runtime, self._model_spec()).build(intent)
