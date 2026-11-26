# pyright: basic
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    Message,
    MessageRole,
    TextPart,
    ToolCall,
    ToolMessage,
    as_sent,
    render_context_note,
)
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
from harness_core.models.transports.base import LLMTransport
from harness_core.models.transports.failures import transport_error
from harness_core.models.transports.tool_results import (
    relocated_images,
    tool_result_text,
)

# Raw httpx — Ollama speaks NDJSON, no SSE complexity. No SDK needed.

def _with_images(message: dict[str, Any], images: list[ImagePart]) -> dict[str, Any]:
    """``/api/chat`` takes a message's images as bare base64 in ``images``; it cannot fetch a URL, so one is named in
    the text instead of being dropped unseen."""
    data = [i.source.data for i in images if i.source.type == "base64"]
    urls = [f"[image: {i.source.data}]" for i in images if i.source.type != "base64"]
    if urls:
        message["content"] = " ".join([message["content"], *urls]).strip()
    if data:
        message["images"] = data
    return message


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
        num_ctx: int | None = None,
        temperature: float = 0.2,
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        # Sampler options injected into every /api/chat payload.
        # repeat_penalty=1.0: Ollama defaults to >1.0 which corrupts tool-call
        # JSON (function arguments legitimately repeat tokens).
        # top_p=1.0: let temperature do the work; redundant nucleus cutoff hurts
        # structured output on quantized models.
        self._options: dict[str, Any] = {
            "temperature": temperature,
            "repeat_penalty": 1.0,
            "top_p": 1.0,
        }
        if num_ctx is not None:
            self._options["num_ctx"] = num_ctx
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

    @property
    def accepts_output_limit(self) -> bool:
        return True

    def _options_for(self, limit: int | None) -> dict[str, Any]:
        return self._options if limit is None else {**self._options, "num_predict": limit}

    @staticmethod
    def _finished(body: dict[str, Any], message: AssistantMessage) -> StopReason:
        """``done_reason: "length"`` is Ollama's cut at ``num_predict``: a truncated turn, never a whole one.
        Ollama reports ``"stop"`` for a turn that called tools, so the calls decide ``TOOL_USE`` as on every other
        transport."""
        if body.get("done_reason") == "length":
            message.truncated = True
            return StopReason.MAX_TOKENS
        return StopReason.TOOL_USE if message.tool_calls else StopReason.END_TURN

    # ------------------------------------------------------------------
    # Message / tool formatting helpers
    # ------------------------------------------------------------------

    def _format_message(self, msg: Message) -> dict[str, Any]:
        msg = as_sent(msg)
        if isinstance(msg, ToolMessage):
            # No tool_call_id on Ollama's `tool` role: the model tells pending calls apart by content alone. The
            # role takes text only; `_format_messages` sends the images in a user message after the results.
            return {"role": "tool", "content": tool_result_text(msg)}
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
            return _with_images(
                {"role": msg.role.value, "content": " ".join(p.text for p in content if isinstance(p, TextPart))},
                [p for p in content if isinstance(p, ImagePart)],
            )
        return {"role": msg.role.value, "content": content or ""}

    def _format_messages(self, messages: list[Message], system: str | None) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        if system:
            formatted.append({"role": "system", "content": system})
        relocated = relocated_images(messages)
        for index, msg in enumerate(messages):
            formatted.append(self._format_message(msg))
            if moved := relocated.get(index):
                # The loop's message (`relocated_message`), as `as_sent` marks it: an image this wire cannot carry is
                # named in its text, so inside the note.
                note = _with_images({"role": "user", "content": moved.note}, list(moved.images))
                note["content"] = render_context_note(note["content"])
                formatted.append(note)
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
        return AssistantMessage(content=text, tool_calls=tool_calls or None)  # pyright: ignore[reportArgumentType]

    def _usage_from(self, body: dict[str, Any]) -> TokenUsage:
        input_tokens = body.get("prompt_eval_count", 0) or 0
        output_tokens = body.get("eval_count", 0) or 0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_llm_calls += 1
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    def _wrap_error(self, exc: Exception, context: str) -> TransportError:
        return transport_error(
            f"Ollama API error ({context}): {exc}",
            exc,
            status_code=exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
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
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        resolved_model = model or self._model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": False,
            "options": self._options_for(max_output_tokens),
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
        stop = self._finished(body, message)
        return ModelResponse(message=message, usage=usage, model=resolved_model, stop_reason=stop)

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
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
        if max_output_tokens is not None:
            payload["options"] = {"num_predict": max_output_tokens}
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
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if system_blocks and not system:
            system = "\n\n".join(b for b in system_blocks if b)
        resolved_model = model or self._model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._format_messages(messages, system),
            "stream": True,
            "options": self._options_for(max_output_tokens),
        }
        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            payload["tools"] = formatted_tools

        text_parts: list[str] = []
        final_raw_message: dict[str, Any] = {}
        done: dict[str, Any] = {}
        stream_tool_calls: list[dict[str, Any]] = []
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
                        # Ollama can spread several tool calls over multiple chunks;
                        # accumulate them instead of keeping only the last chunk's.
                        stream_tool_calls.extend(msg["tool_calls"])
                        final_raw_message = msg
                    if chunk.get("done"):
                        done = chunk
                        usage = self._usage_from(chunk)
                        if not final_raw_message:
                            final_raw_message = msg
        except Exception as exc:
            raise self._wrap_error(exc, "stream") from exc

        final_text = "".join(text_parts) or final_raw_message.get("content") or None
        final_raw_message = {**final_raw_message, "content": final_text}
        if stream_tool_calls:
            final_raw_message["tool_calls"] = stream_tool_calls
        message = self._parse_message(final_raw_message)
        stop = self._finished(done, message)
        yield StreamCompleteEvent(
            response=ModelResponse(message=message, usage=usage, model=resolved_model, stop_reason=stop)
        )
