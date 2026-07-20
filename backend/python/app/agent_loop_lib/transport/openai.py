from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
    ToolCall,
)
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

# Uses `openai` SDK internally.
# Install: pip install 'agent-loop[openai]'

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_FINISH_REASON_MAP: dict[str | None, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}


class OpenAITransport(LLMTransport):
    """OpenAI Chat Completions API via the official SDK.

    Install: pip install 'agent-loop[openai]'

    The SDK is used internally to handle SSE parsing and streaming
    tool-call-argument accumulation correctly. It never leaks past this
    class — all callers see only the LLMTransport interface. `base_url`
    lets this same transport talk to any OpenAI-compatible endpoint
    (vLLM, LiteLLM proxy, Azure OpenAI's OpenAI-compat surface, etc.).
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None) -> None:
        super().__init__()
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "openai SDK is required for OpenAITransport. "
                "Install it with: pip install 'agent-loop[openai]'"
            ) from exc
        self._openai = _openai
        self._model = model
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = _openai.AsyncOpenAI(**client_kwargs)
        # Cumulative usage across all calls on this transport instance —
        # diagnostic only; see AnthropicTransport's equivalent fields.
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0
        self.total_cache_read_tokens: int = 0

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Message / tool formatting helpers
    # ------------------------------------------------------------------

    def _format_message(self, msg: Message) -> dict[str, Any]:
        """Convert a framework Message to an OpenAI chat message dict."""
        if msg.role == MessageRole.TOOL:
            return {
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content or "",
            }
        if msg.role == MessageRole.ASSISTANT:
            return {
                "role": "assistant",
                "content": msg.text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in msg.tool_calls or []
                ] or None,
            }
        content = msg.content
        if isinstance(content, list):
            content = " ".join(getattr(p, "text", "") for p in content)
        return {"role": msg.role.value, "content": content or ""}

    def _format_messages(self, messages: list[Message], system: str | None) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})
        formatted.extend(self._format_message(m) for m in messages)
        return formatted

    def _format_tools(self, tools: list[ToolSchema] | None) -> list[dict] | None:
        """Our internal, provider-agnostic `ToolSchema` into OpenAI's
        function-calling shape."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    def _parse_tool_calls(self, raw_tool_calls: Any) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls or []:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, ValueError):
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return tool_calls

    def _parse_response(self, choice_message: Any) -> AssistantMessage:
        """Convert an OpenAI SDK response message to a framework AssistantMessage."""
        tool_calls = self._parse_tool_calls(getattr(choice_message, "tool_calls", None))
        text = getattr(choice_message, "content", None)
        return AssistantMessage(content=text, tool_calls=tool_calls or None)

    def _usage_from(self, response: Any) -> TokenUsage:
        usage = getattr(response, "usage", None)

        def _int_field(obj: Any, name: str) -> int:
            value = getattr(obj, name, 0) if obj is not None else 0
            return value if isinstance(value, int) else 0

        input_tokens = _int_field(usage, "prompt_tokens")
        output_tokens = _int_field(usage, "completion_tokens")
        details = getattr(usage, "prompt_tokens_details", None)
        cached = _int_field(details, "cached_tokens")

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += 1
        self.total_cache_read_tokens += cached

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cached,
            cache_write_tokens=0,
        )

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        status_code = getattr(exc, "status_code", None)
        is_network = isinstance(
            exc, (self._openai.APIConnectionError, self._openai.APITimeoutError)
        )
        retryable = is_network or (
            status_code is not None and status_code in _RETRYABLE_STATUS_CODES
        )
        return TransportError(
            f"OpenAI API error ({context}): {exc}",
            status_code=status_code,
            retryable=retryable,
        )

    # ------------------------------------------------------------------
    # Public LLMTransport interface
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> ModelResponse:
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if effort:
            # o-series/gpt-5-series "reasoning effort" knob — accepted as a
            # no-op parameter by models that don't support it.
            kwargs["reasoning_effort"] = effort
        # thinking_budget has no Chat Completions equivalent — no-op, kept
        # for interface parity with AnthropicTransport.

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        usage = self._usage_from(response)
        message = self._parse_response(response.choices[0].message)
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN)
        return ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        """Force structured JSON output using the tool-call trick (same
        approach as AnthropicTransport.complete_structured)."""
        tool_def = {
            "type": "function",
            "function": {
                "name": "structured_output",
                "description": "Return the structured result.",
                "parameters": output_schema,
            },
        }
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "tools": [tool_def],
            "tool_choice": {"type": "function", "function": {"name": "structured_output"}},
        }

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._wrap_error(exc, "complete_structured") from exc

        usage = self._usage_from(response)
        message = response.choices[0].message
        for tc in getattr(message, "tool_calls", None) or []:
            if tc.function.name == "structured_output":
                try:
                    data = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, ValueError):
                    data = {}
                return StructuredResponse(data=data, usage=usage, model=resolved_model)

        raise TransportError("structured_output tool call not found in response")

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        resolved_model = model or self._model
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            kwargs["tools"] = formatted_tools
        if effort:
            kwargs["reasoning_effort"] = effort

        text_parts: list[str] = []
        usage = TokenUsage()
        finish_reason: str | None = None
        # Tool call deltas arrive fragmented across chunks, indexed by
        # position in the assistant's tool_calls list — accumulate by
        # index until the stream ends, same pattern every OpenAI streaming
        # client uses for function calling.
        tool_call_accum: dict[int, dict[str, Any]] = {}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = self._usage_from(chunk)
                if not chunk.choices:
                    continue
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDeltaEvent(delta=delta.content)
                for tc_delta in delta.tool_calls or []:
                    entry = tool_call_accum.setdefault(
                        tc_delta.index, {"id": None, "name": None, "arguments": ""}
                    )
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        final_tool_calls: list[ToolCall] = []
        for idx, entry in sorted(tool_call_accum.items()):
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except (json.JSONDecodeError, ValueError):
                args = {}
            final_tool_calls.append(
                ToolCall(id=entry["id"] or f"call_{idx}", name=entry["name"] or "", arguments=args)
            )
        final_text = "".join(text_parts) or None
        message = AssistantMessage(content=final_text, tool_calls=final_tool_calls or None)
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN)
        yield StreamCompleteEvent(
            response=ModelResponse(message=message, usage=usage, stop_reason=stop_reason, model=resolved_model)
        )
