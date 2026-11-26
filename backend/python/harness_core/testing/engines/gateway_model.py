"""An engine world's model served by the real model gateway (G-49 / GA-26).

``EngineWorld``'s fake engine asks its model over a small chat wire through the egress proxy. Here that call is
put on the OpenAI chat wire and served by ``ModelGateway`` (token, grant, budget ledger and metering are the
gateway's own), whose provider transport is any ``LLMTransport``: a ``CassetteTransport`` replaying a recorded
journey, or recording one from ``BrainTransport``, the scripted brain the conformance gateway answers with, speaking
the kernel's types. So a P0 journey runs on the mock harness through the gateway with no provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast, override

from harness_core.access.types import ENGINE_MODEL_ID, RunBudget
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.gateway.model_gateway import ChatCompletion
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.messages import ToolCall as KernelToolCall
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import StreamCompleteEvent
from harness_core.models.transports.base import LLMTransport
from harness_core.testing.engines.script import (
    ChatMessage,
    ModelReply,
    ModelUsage,
    ScriptedBrain,
    ToolCall,
)
from harness_core.testing.model_gateway import GatewayRig, body_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from harness_core.engines.types import JsonValue
    from harness_core.gateway.types import RunAttemptBinding
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.testing.sandbox.egress import EgressRequest


def chat_body(messages: Sequence[ChatMessage], tools: Sequence[str]) -> dict[str, Any]:
    """The fake engine's request on the OpenAI chat wire."""
    wire: list[dict[str, Any]] = []
    for m in messages:
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                for c in m.tool_calls
            ]
        if m.tool_call_id is not None:
            entry["tool_call_id"] = m.tool_call_id
        wire.append(entry)
    body: dict[str, Any] = {"model": ENGINE_MODEL_ID, "messages": wire}
    if tools:
        body["tools"] = [
            {"type": "function", "function": {"name": t, "parameters": {"type": "object"}}} for t in tools
        ]
    return body


def reply_of(completion: dict[str, Any]) -> ModelReply:
    """The gateway's chat completion as the fake engine's reply."""
    message = as_object(as_object(as_list(completion.get("choices"))[0]).get("message"))
    usage = as_object(completion.get("usage"))
    calls = tuple(
        ToolCall(
            id=as_str(call.get("id")),
            name=as_str(as_object(call.get("function")).get("name")),
            arguments=as_str(as_object(call.get("function")).get("arguments")),
        )
        for call in (as_object(c) for c in as_list(message.get("tool_calls")))
    )
    content = message.get("content")
    return ModelReply(
        content=content if isinstance(content, str) else "",
        tool_calls=calls,
        usage=ModelUsage(as_int(usage.get("prompt_tokens")), as_int(usage.get("completion_tokens"))),
    )


@dataclass
class GatewayModelEndpoint:
    """``EngineWorld.model``: each engine model call through ``rig``'s gateway, on one run's grant."""

    transport: LLMTransport
    rig: GatewayRig = field(default_factory=GatewayRig)
    run_id: str = "engine-run"
    budget: RunBudget = field(default_factory=lambda: RunBudget(max_cost_usd=5.0, max_tokens=2_000_000))
    _binding: RunAttemptBinding | None = None

    async def __call__(self, request: EgressRequest) -> JsonValue:
        if self._binding is None:
            self._binding = self.rig.pin(self.run_id, transport=self.transport, budget=self.budget)
        body = as_object(request.body)
        messages = [ChatMessage.from_json(m) for m in as_list(body.get("messages"))]
        tools = [as_str(as_object(t).get("name")) for t in as_list(body.get("tools"))]
        outcome = await self.rig.gateway.chat(
            self.rig.token(self._binding), {}, body_of(chat_body(messages, tools))
        )
        assert isinstance(outcome, ChatCompletion), "the engine's calls are not streamed"
        return reply_of(cast("dict[str, Any]", outcome.body)).to_json()


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(getattr(part, "text", "")) for part in cast("list[object]", content or []))


def chat_messages(messages: Sequence[Message], system: str | None) -> list[ChatMessage]:
    """The kernel's messages as the scripted brain reads them."""
    out = [ChatMessage("system", system)] if system else []
    for m in messages:
        if isinstance(m, UserMessage):
            out.append(ChatMessage("user", _text(m.content)))
        elif isinstance(m, AssistantMessage):
            calls = tuple(
                ToolCall(c.id, c.name, json.dumps(c.arguments, sort_keys=True)) for c in m.tool_calls or []
            )
            out.append(ChatMessage("assistant", _text(m.content), calls))
        elif isinstance(m, ToolMessage):
            out.append(ChatMessage("tool", _text(m.content), tool_call_id=m.tool_call_id))
        else:
            out.append(ChatMessage("system", _text(getattr(m, "content", ""))))
    return out


class BrainTransport(LLMTransport):
    """The conformance gateway's scripted brain as a provider transport: what a cassette is recorded from."""

    def __init__(self, brain: ScriptedBrain | None = None) -> None:
        super().__init__()
        self.brain = brain or ScriptedBrain()
        self._model = "scripted-brain"
        self._calls = 0

    @property
    @override
    def provider(self) -> str:
        return "scripted"

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        self._calls += 1
        reply = self.brain.reply(chat_messages(messages, system), [t.name for t in tools or ()], f"call_{self._calls}")
        calls = [KernelToolCall(id=c.id, name=c.name, arguments=c.args()) for c in reply.tool_calls]
        return ModelResponse(
            message=AssistantMessage(content=[TextPart(text=reply.content)], tool_calls=calls or None),
            usage=TokenUsage(input_tokens=reply.usage.input_tokens, output_tokens=reply.usage.output_tokens),
            stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
            model=self._model,
        )

    @override
    async def complete_structured(
        self, messages: list[Message], output_schema: dict[str, Any], system: str | None = None, model: str | None = None
    ) -> StructuredResponse:
        raise NotImplementedError("the scripted brain answers chat calls only")

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield StreamCompleteEvent(response=await self.complete(messages, tools, system))


__all__ = ["BrainTransport", "GatewayModelEndpoint", "chat_body", "chat_messages", "reply_of"]
