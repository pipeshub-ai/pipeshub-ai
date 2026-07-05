"""Vendored from agent-loop's `tests/support/scripted_transport.py` (Phase 9
of the migration plan — "bring test patterns, not the full suite"). Kept
byte-for-byte identical to the upstream version so future upstream fixes
apply with a straight copy; PipesHub-specific test helpers live alongside
this file instead of being merged into it.

Deterministic `LLMTransport` double: scripted responses/usage/errors, no
network access or API key required. Used by the adapter-layer test suite to
drive a real `Agent` end-to-end without going through `LangChainTransport`
or a real LangChain chat model at all — useful for validating hook/tool/
prompt-builder wiring (`test_factory_wiring.py`, `test_agent_run_contract.py`)
independently of LangChain's own behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolCall
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
)
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.base import LLMTransport


@dataclass
class ScriptedStep:
    message: AssistantMessage | None = None
    usage: TokenUsage | None = None
    error: Exception | None = None
    delay: float = 0.0
    text_chunks: list[str] | None = None
    stop_reason: StopReason = StopReason.END_TURN


class ScriptedTransport(LLMTransport):
    """Replays a fixed script of responses/usage/errors, in call order.

    Falls back to a plain "done" text message once the script is exhausted
    so callers don't need to pad scripts with a trailing task_complete just
    to satisfy an agent that calls complete() one extra time.
    """

    def __init__(self, script: list[ScriptedStep] | None = None) -> None:
        super().__init__()
        self._script: list[ScriptedStep] = list(script or [])
        self._index = 0
        self.calls: list[dict] = []
        self._model = "scripted-model"

    @property
    def provider(self) -> str:
        return "scripted"

    def _next_step(self) -> ScriptedStep | None:
        if self._index >= len(self._script):
            return None
        step = self._script[self._index]
        self._index += 1
        return step

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system, "model": model})
        step = self._next_step()
        if step is None:
            return ModelResponse(message=AssistantMessage(content="done"), usage=TokenUsage(), model=self._model)
        if step.delay:
            await asyncio.sleep(step.delay)
        if step.error is not None:
            raise step.error
        message = step.message if step.message is not None else AssistantMessage(content="done")
        return ModelResponse(
            message=message,
            usage=step.usage if step.usage is not None else TokenUsage(),
            stop_reason=step.stop_reason,
            model=self._model,
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        step = self._next_step()
        if step is not None and step.error is not None:
            raise step.error
        usage = step.usage if step is not None and step.usage is not None else TokenUsage()
        return StructuredResponse(data={}, usage=usage, model=self._model)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        peeked = self._script[self._index] if self._index < len(self._script) else None
        response = await self.complete(messages, tools=tools, system=system, model=model)
        if peeked is not None and peeked.text_chunks:
            for chunk in peeked.text_chunks:
                yield TextDeltaEvent(delta=chunk)
        yield StreamCompleteEvent(response=response)

    # ---- fluent script builders ----

    def add_text(self, text: str, usage: TokenUsage | None = None) -> "ScriptedTransport":
        self._script.append(ScriptedStep(message=AssistantMessage(content=text), usage=usage))
        return self

    def add_tool_call(self, call: ToolCall, usage: TokenUsage | None = None) -> "ScriptedTransport":
        self._script.append(ScriptedStep(
            message=AssistantMessage(tool_calls=[call]), usage=usage,
        ))
        return self

    def add_tool_calls(self, calls: list[ToolCall], usage: TokenUsage | None = None) -> "ScriptedTransport":
        self._script.append(ScriptedStep(
            message=AssistantMessage(tool_calls=list(calls)), usage=usage,
        ))
        return self

    def add_text_chunks(self, chunks: list[str], usage: TokenUsage | None = None) -> "ScriptedTransport":
        self._script.append(ScriptedStep(
            message=AssistantMessage(content="".join(chunks)),
            usage=usage,
            text_chunks=list(chunks),
        ))
        return self

    def add_truncated(self, text: str = "", usage: TokenUsage | None = None) -> "ScriptedTransport":
        self._script.append(ScriptedStep(
            message=AssistantMessage(content=text, truncated=True),
            usage=usage,
            stop_reason=StopReason.MAX_TOKENS,
        ))
        return self

    def add_error(self, error: Exception) -> "ScriptedTransport":
        self._script.append(ScriptedStep(error=error))
        return self

    def remaining(self) -> int:
        return len(self._script) - self._index


__all__ = ["ScriptedStep", "ScriptedTransport"]
