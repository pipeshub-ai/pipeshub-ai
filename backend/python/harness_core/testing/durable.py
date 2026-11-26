"""Test doubles for the durable kernel's model and tool ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.kernel.core.messages import (
    AssistantMessage,
    Part,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from harness_core.kernel.core.tool_content import UNKNOWN_TOOL_ERROR_PREFIX
from harness_core.kernel.durable.effects import (
    CallEffect,
    ToolEffect,
    classify_tool_effect,
    with_call_effect,
)
from harness_core.kernel.durable.ports import (
    ModelBackoff,
    ModelFailed,
    SampledTurn,
    ToolExecution,
)
from harness_core.kernel.durable.transcript import text_of
from harness_core.testing.model_types import (
    Completed,
    ModelCallError,
    ModelRequest,
    ReasoningDelta,
    RequestMessage,
    TextDelta,
)
from harness_core.tools.base import Tag

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable.ports import DeltaKind, ModelCall, ToolContext
    from harness_core.testing.model_script import Reply
    from harness_core.testing.model_types import StreamingModel

    ToolFn = Callable[[Mapping[str, JsonValue], ToolContext], Awaitable[ToolExecution]]


def to_model_request(call: ModelCall) -> ModelRequest:
    """Kernel messages -> the neutral request shape ``ScriptedModel`` checks."""
    names: dict[str, str] = {}
    out: list[RequestMessage] = []
    for message in call.messages:
        if isinstance(message, UserMessage):
            out.append(RequestMessage("user", text_of(message), injected=message.injected))
        elif isinstance(message, AssistantMessage):
            for tool_call in message.tool_calls or []:
                names[tool_call.id] = tool_call.name
            out.append(RequestMessage("assistant", message.text))
        elif isinstance(message, ToolMessage):
            out.append(
                RequestMessage(
                    "tool",
                    message.text,
                    tool_name=names.get(message.tool_call_id or ""),
                    tool_status="failed" if message.is_error else "succeeded",
                )
            )
    return ModelRequest(tuple(out))


class ScriptedModelPort:
    """``DurableModelPort`` over ``ScriptedModel``. ``raw`` is the full message
    (thinking included), standing in for provider-native state."""

    def __init__(
        self,
        model: StreamingModel,
        *,
        provider: str = "scripted",
        transport: str = "scripted",
    ) -> None:
        self.model = model
        self.provider = provider
        self.transport = transport
        self.calls: list[ModelCall] = []
        self.restored = 0

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        self.calls.append(call)
        completed: Completed | None = None
        try:
            # Drain the stream (Completed is last) so the generator closes normally.
            async for event in self.model.stream(to_model_request(call)):
                if isinstance(event, TextDelta):
                    await on_delta("text_delta", event.text)
                elif isinstance(event, ReasoningDelta):
                    await on_delta("reasoning_delta", event.text)
                elif isinstance(event, Completed):
                    completed = event
        except ModelCallError as exc:
            if exc.kind == "rate_limited":
                raise ModelBackoff(exc.retry_after_s or 1.0) from exc
            raise ModelFailed(exc.kind, str(exc)) from exc
        if completed is None:
            raise ModelFailed("malformed", "stream ended without a response")
        return self._sampled(completed)

    def _sampled(self, event: Completed) -> SampledTurn:
        response = event.response
        content: list[Part] = []
        if response.reasoning:
            content.append(ThinkingPart(thinking=response.reasoning))
        if response.text:
            content.append(TextPart(text=response.text))
        message = AssistantMessage(
            content=content,
            tool_calls=[
                ToolCall(id=c.id, name=c.name, arguments=dict(c.args))
                for c in response.tool_calls
            ]
            or None,
            # A reply scripted to stop at the output limit is a truncated turn, as every transport reports one.
            truncated=response.stop_reason == "max_tokens",
        )
        usage = response.usage
        return SampledTurn(
            message=message,
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            ),
            stop_reason=StopReason.MAX_TOKENS
            if message.truncated
            else StopReason.TOOL_USE
            if response.tool_calls
            else StopReason.END_TURN,
            provider=self.provider,
            transport=self.transport,
            model="scripted",
            raw=message.model_dump_json().encode("utf-8"),
        )

    def restore(
        self, raw: bytes, *, provider: str, transport: str
    ) -> AssistantMessage | None:
        if (provider, transport) != (self.provider, self.transport):
            return None
        self.restored += 1
        return AssistantMessage.model_validate_json(raw)


def provider_events(reply: Reply) -> list[StreamEvent]:
    """``reply`` as a provider transport streams it (``RecordedTransport``), the assembled response last."""
    content: list[Part] = [ThinkingPart(thinking=reply.reasoning)] if reply.reasoning else []
    content += [TextPart(text=reply.content)] if reply.content else []
    message = AssistantMessage(
        content=content,
        tool_calls=[
            ToolCall(id=c.id or f"call_{i}", name=c.name, arguments=dict(c.args)) for i, c in enumerate(reply.tool_calls)
        ]
        or None,
    )
    deltas: list[StreamEvent] = [ThinkingDeltaEvent(delta=reply.reasoning)] if reply.reasoning else []
    deltas += [TextDeltaEvent(delta=chunk) for chunk in reply.text_chunks()]
    response = ModelResponse(
        message=message,
        usage=TokenUsage(input_tokens=reply.usage.input_tokens, output_tokens=reply.usage.output_tokens),
        stop_reason=StopReason.TOOL_USE if reply.tool_calls else StopReason.END_TURN,
        model="recorded",
    )
    return [*deltas, StreamCompleteEvent(response=response)]


@dataclass
class _Registered:
    effect: ToolEffect
    fn: ToolFn
    call_effect: CallEffect | None = None
    schema: ToolSchema | None = None


@dataclass
class FunctionToolPort:
    """``DurableToolPort`` over plain async functions, classified by tags."""

    tools: dict[str, _Registered] = field(default_factory=dict[str, _Registered])
    executed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def add(
        self,
        name: str,
        fn: ToolFn,
        *,
        tags: Iterable[tuple[str, str]] = (),
        call_effect: CallEffect | None = None,
        effect: ToolEffect | None = None,
        schema: ToolSchema | None = None,
    ) -> None:
        """``effect``: the tool's effect as a host describes it, instead of one classified from ``tags``. ``schema``:
        what the model is shown for it; a port whose tools declare none shows the model no tool list."""
        self.tools[name] = _Registered(
            effect or classify_tool_effect(name, [Tag(k, v) for k, v in tags]), fn, call_effect, schema
        )

    def describe(self, name: str) -> ToolEffect | None:
        registered = self.tools.get(name)
        return registered.effect if registered else None

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        registered = self.tools.get(call.name)
        if registered is None:
            return None
        if registered.call_effect is None:
            return registered.effect
        return with_call_effect(registered.effect, registered.call_effect(call.arguments))

    def unknown(self, name: str) -> str:
        return f"{UNKNOWN_TOOL_ERROR_PREFIX} {name}"

    def schemas(self) -> list[ToolSchema] | None:
        declared = [r.schema for r in self.tools.values() if r.schema is not None]
        return declared or None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        registered = self.tools.get(call.name)
        if registered is None:
            return ToolExecution(status="error", content=self.unknown(call.name))
        self.executed.append((call.name, ctx.invocation_id))
        return await registered.fn(call.arguments, ctx)


__all__ = ["FunctionToolPort", "ScriptedModelPort", "provider_events", "to_model_request"]
