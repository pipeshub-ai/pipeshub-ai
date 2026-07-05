from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
    ToolCall,
)
from app.agent_loop_lib.core.responses import (
    ModelResponse,
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

# Raw httpx — Ollama speaks NDJSON, no SSE complexity. No SDK needed.

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OllamaTransport(LLMTransport):
    """Ollama local inference via raw HTTP (NDJSON streaming).

    Talks to Ollama's `/api/chat` endpoint directly — no SDK dependency,
    since the protocol is plain JSON (or newline-delimited JSON chunks when
    streaming) with OpenAI-function-calling-shaped tool definitions.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "llama3.2"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        # Cumulative usage across all calls on this transport instance —
        # diagnostic only; see AnthropicTransport's equivalent fields.
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Message / tool formatting helpers
    # ------------------------------------------------------------------

    def _format_message(self, msg: Message) -> dict[str, Any]:
        if msg.role == MessageRole.TOOL:
            # Ollama's /api/chat has no dedicated tool-result role field
            # beyond "tool" + content — no tool_call_id round-trip, so the
            # model must disambiguate multiple pending calls from content
            # alone (a known Ollama tool-calling limitation, not ours).
            return {"role": "tool", "content": msg.content or ""}
        if msg.role == MessageRole.ASSISTANT:
            return {
                "role": "assistant",
                "content": msg.text or "",
                "tool_calls": [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in msg.tool_calls or []
                ],
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

    def _parse_message(self, raw_message: dict[str, Any]) -> AssistantMessage:
        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(raw_message.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            tool_calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args))
        text = raw_message.get("content") or None
        return AssistantMessage(content=text, tool_calls=tool_calls or None)

    def _usage_from(self, body: dict[str, Any]) -> TokenUsage:
        input_tokens = body.get("prompt_eval_count", 0) or 0
        output_tokens = body.get("eval_count", 0) or 0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += 1
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        status_code: int | None = None
        retryable = isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            retryable = status_code in _RETRYABLE_STATUS_CODES
        return TransportError(
            f"Ollama API error ({context}): {exc}",
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
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": False,
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            payload["tools"] = formatted_tools
        # thinking_budget/effort have no Ollama equivalent — no-op, kept
        # for interface parity with the other transports.

        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise self._wrap_error(exc, "complete") from exc

        body = response.json()
        usage = self._usage_from(body)
        message = self._parse_message(body.get("message", {}))
        return ModelResponse(message=message, usage=usage, model=resolved_model)

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        """Ollama's native structured-output mode: pass the JSON schema as
        `format` and the model is constrained to emit matching JSON."""
        resolved_model = model or self._model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": False,
            "format": output_schema,
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise self._wrap_error(exc, "complete_structured") from exc

        body = response.json()
        usage = self._usage_from(body)
        content = body.get("message", {}).get("content", "")
        try:
            data = json.loads(content) if content else {}
        except (json.JSONDecodeError, ValueError) as exc:
            raise TransportError(f"Ollama structured output was not valid JSON: {exc}") from exc
        return StructuredResponse(data=data, usage=usage, model=resolved_model)

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
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": True,
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            payload["tools"] = formatted_tools

        text_parts: list[str] = []
        final_raw_message: dict[str, Any] = {}
        usage = TokenUsage()
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    msg = chunk.get("message") or {}
                    delta = msg.get("content") or ""
                    if delta:
                        text_parts.append(delta)
                        yield TextDeltaEvent(delta=delta)
                    if msg.get("tool_calls"):
                        final_raw_message = msg
                    if chunk.get("done"):
                        usage = self._usage_from(chunk)
                        if not final_raw_message:
                            final_raw_message = msg
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        final_text = "".join(text_parts) or final_raw_message.get("content") or None
        message = self._parse_message({**final_raw_message, "content": final_text})
        yield StreamCompleteEvent(response=ModelResponse(message=message, usage=usage, model=resolved_model))
