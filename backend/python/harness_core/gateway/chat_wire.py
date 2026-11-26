"""OpenAI chat completions on the wire ↔ the kernel's model types (pure; E3).

Inbound, an engine's request becomes kernel ``Message``s and ``ToolSchema``s, the
types every ``LLMTransport`` already speaks; outbound, the transport's
``ModelResponse`` or ``StreamEvent``s become a ``chat.completion`` or its
chunks. Provider differences are the transports' job; what differs in how they
*report* a turn (tool calls streamed in fragments, only on the final message,
under a content-block index, ids late) is reconciled here, once.

``parallel_tool_calls`` and ``prompt_cache_key`` are read and passed to a transport that can send them
(``models.call_options``), as on the Responses wire (WP-62, WP56-WIRE): the cache key goes out as the run's org's
routing key, never the engine's own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from harness_core.access.types import ModelApi
from harness_core.gateway.errors import InvalidChatRequest, UnsupportedParameter
from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    ImageSource,
    SystemMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import StopReason
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.transports.openai_responses import normalise_tool_call

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.gateway.wire import Json, WireCall
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.core.responses import ModelResponse, TokenUsage
    from harness_core.kernel.core.streaming import StreamEvent

# Parameters whose meaning the transports cannot honour: refused rather than ignored (principle 6).
REFUSED_PARAMETERS = frozenset(
    {
        "stop",
        "logprobs",
        "top_logprobs",
        "response_format",
        "functions",
        "function_call",
        "audio",
        "modalities",
        "prediction",
        "web_search_options",
        "logit_bias",
    }
)
# Accepted but not forwarded: sampling is pinned by the org's model configuration,
# and the rest identify or route the caller to a vendor.
DROPPED_PARAMETERS = frozenset(
    {
        "temperature",
        "top_p",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "user",
        "metadata",
        "store",
        "service_tier",
        "safety_identifier",
        "verbosity",
    }
)
_READ = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "reasoning_effort",
        "n",
        "parallel_tool_calls",
        "prompt_cache_key",
    }
)

_FINISH: Mapping[StopReason, str] = {
    StopReason.END_TURN: "stop",
    StopReason.TOOL_USE: "tool_calls",
    StopReason.MAX_TOKENS: "length",
    StopReason.STOP_SEQUENCE: "stop",
    StopReason.ERROR: "stop",
    StopReason.CANCELLED: "stop",
}


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    system: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    stream: bool
    include_usage: bool
    reasoning_effort: str | None
    max_tokens: int | None
    dropped: tuple[str, ...] = ()
    # Chat history carries no reasoning a transport could replay; the Responses wire counts its items.
    reasoning_items: int = 0
    encrypted_reasoning: bool = False
    parallel_tool_calls: bool | None = None
    cache_key: str | None = None

    @property
    def output_limit(self) -> int | None:
        return self.max_tokens


def _object(value: object, what: str) -> Json:
    if not isinstance(value, dict):
        raise InvalidChatRequest(f"{what} must be an object", param=what)
    return cast("Json", value)


def _text(value: object, what: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part.text for part in _parts(cast("list[object]", value), what) if isinstance(part, TextPart))
    raise InvalidChatRequest(f"{what} must be a string or a list of parts", param=what)


def image_source(url: str, what: str) -> ImageSource:
    """A `data:` URI as the kernel's source. A remote URL is refused: the provider would fetch
    it from its own network, a channel out of the sandbox to any host the caller names (SEC1 MX-8)."""
    if url.startswith("data:") and ";base64," in url:
        header, data = url[5:].split(";base64,", 1)
        return ImageSource(type="base64", media_type=header or None, data=data)
    raise UnsupportedParameter(what, f"parameter '{what}' must be a data: URI; remote image URLs are not served")


def _parts(raw: list[object], what: str) -> list[Part]:
    parts: list[Part] = []
    for item in raw:
        part = _object(item, what)
        kind = part.get("type")
        if kind == "text":
            parts.append(TextPart(text=str(part.get("text") or "")))
        elif kind == "image_url":
            image = part.get("image_url")
            url = cast("Json", image).get("url") if isinstance(image, dict) else image
            if not isinstance(url, str) or not url:
                raise InvalidChatRequest("image_url part without a url", param=what)
            parts.append(ImagePart(source=image_source(url, f"{what}.image_url")))
        else:
            raise UnsupportedParameter(f"{what}.type={kind}")
    return parts


def _content(value: object, what: str) -> str | list[Part]:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = _parts(cast("list[object]", value), what)
        return parts if any(isinstance(p, ImagePart) for p in parts) else "".join(
            p.text for p in parts if isinstance(p, TextPart)
        )
    raise InvalidChatRequest(f"{what} must be a string or a list of parts", param=what)


def _tool_calls(value: object) -> list[ToolCall] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidChatRequest("tool_calls must be a list", param="messages.tool_calls")
    calls: list[ToolCall] = []
    for item in cast("list[object]", value):
        call = _object(item, "messages.tool_calls")
        function = _object(call.get("function"), "messages.tool_calls.function")
        call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
        if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
            raise InvalidChatRequest("a tool call needs an id and a name", param="messages.tool_calls")
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments if arguments is not None else {})
        calls.append(normalise_tool_call(call_id, name, raw))
    return calls or None


def _message(raw: Json, *, leading: bool) -> Message | str:
    """A kernel message, or the text of a leading system message (the transports' ``system``)."""
    role = raw.get("role")
    if role in ("system", "developer"):
        text = _text(raw.get("content"), "messages.content")
        return text if leading else SystemMessage(content=text)
    if role == "user":
        return UserMessage(content=_content(raw.get("content"), "messages.content"))
    if role == "assistant":
        return AssistantMessage(
            content=_text(raw.get("content"), "messages.content") or None,  # pyright: ignore[reportArgumentType]
            tool_calls=_tool_calls(raw.get("tool_calls")),
        )
    if role == "tool":
        call_id = raw.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            raise InvalidChatRequest("a tool message needs a tool_call_id", param="messages.tool_call_id")
        return ToolMessage(content=_content(raw.get("content"), "messages.content"), tool_call_id=call_id)
    raise InvalidChatRequest(f"unknown message role {role!r}", param="messages.role")


def _tools(value: object) -> tuple[ToolSchema, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvalidChatRequest("tools must be a list", param="tools")
    schemas: list[ToolSchema] = []
    for item in cast("list[object]", value):
        tool = _object(item, "tools")
        if tool.get("type", "function") != "function":
            raise UnsupportedParameter(f"tools.type={tool.get('type')}")
        function = _object(tool.get("function"), "tools.function")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidChatRequest("a tool needs a name", param="tools.function.name")
        parameters = function.get("parameters")
        schemas.append(
            ToolSchema(
                name=name,
                description=str(function.get("description") or ""),
                input_schema=cast("dict[str, object]", parameters) if isinstance(parameters, dict) else {},
            )
        )
    return tuple(schemas)


def _positive(value: object, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidChatRequest(f"{what} must be a positive integer", param=what)
    return value


def parse_chat_request(body: object) -> ChatRequest:
    request = _object(body, "body")
    for name in sorted(REFUSED_PARAMETERS & request.keys()):
        if request[name] is not None:
            raise UnsupportedParameter(name)
    n = request.get("n")
    if n is not None and n != 1:
        raise UnsupportedParameter("n")
    tool_choice = request.get("tool_choice")
    if tool_choice not in (None, "auto"):
        raise UnsupportedParameter("tool_choice")
    model = request.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidChatRequest("model is required", param="model")
    raw_messages = request.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise InvalidChatRequest("messages must be a non-empty list", param="messages")
    system: list[str] = []
    messages: list[Message] = []
    for item in cast("list[object]", raw_messages):
        parsed = _message(_object(item, "messages"), leading=not messages)
        if isinstance(parsed, str):
            system.append(parsed)
        else:
            messages.append(parsed)
    stream = request.get("stream") is True
    options = request.get("stream_options")
    include_usage = stream and isinstance(options, dict) and cast("Json", options).get("include_usage") is True
    effort = request.get("reasoning_effort")
    dropped = tuple(sorted(name for name in request if name not in _READ and name not in REFUSED_PARAMETERS))
    return ChatRequest(
        model=model,
        system="\n\n".join(s for s in system if s) or None,
        messages=tuple(messages),
        tools=_tools(request.get("tools")),
        stream=stream,
        include_usage=include_usage,
        reasoning_effort=effort if isinstance(effort, str) and effort else None,
        max_tokens=_positive(request.get("max_completion_tokens"), "max_completion_tokens")
        or _positive(request.get("max_tokens"), "max_tokens"),
        dropped=dropped,
        parallel_tool_calls=_flag(request.get("parallel_tool_calls"), "parallel_tool_calls"),
        cache_key=_cache_key(request.get("prompt_cache_key")),
    )


def _flag(value: object, what: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise InvalidChatRequest(f"{what} must be a boolean", param=what)


def _cache_key(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidChatRequest("prompt_cache_key must be a string", param="prompt_cache_key")
    return value


def tool_arguments(call: ToolCall) -> str:
    """The arguments as the model wrote them: a malformed call keeps its raw text."""
    raw = call.arguments.get(MALFORMED_TOOL_CALL_ARGS_KEY)
    if isinstance(raw, str) and MALFORMED_TOOL_CALL_ERROR_KEY in call.arguments:
        return raw
    return json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))



def usage_body(usage: TokenUsage) -> Json:
    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "prompt_tokens_details": {"cached_tokens": usage.cache_read_tokens},
    }


def finish_reason(response: ModelResponse) -> str:
    if response.message.tool_calls and response.stop_reason != StopReason.MAX_TOKENS:
        return "tool_calls"
    return _FINISH.get(response.stop_reason, "stop")


def _reasoning(message: AssistantMessage) -> str:
    from harness_core.kernel.core.messages import ThinkingPart

    return "".join(p.thinking for p in message.content if isinstance(p, ThinkingPart))


def completion_body(response: ModelResponse, *, model_id: str, completion_id: str, created: int) -> Json:
    message = response.message
    out: Json = {"role": "assistant", "content": message.text or None}
    reasoning = _reasoning(message)
    if reasoning:
        out["reasoning_content"] = reasoning
    if message.tool_calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": tool_arguments(c)}}
            for c in message.tool_calls
        ]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "message": out, "finish_reason": finish_reason(response)}],
        "usage": usage_body(response.usage),
    }


@dataclass(slots=True)
class _StreamedCall:
    index: int
    call_id: str | None = None
    name: str | None = None
    pending: str = ""
    opened: bool = False


@dataclass(slots=True)
class ChunkEncoder:
    """One completion's chunks. Tool-call fragments are held until their id and name
    are known (an OpenAI client keys a call on its first fragment) and renumbered in
    order of appearance; calls a transport reports only on its final response are
    sent whole at the end."""

    model_id: str
    completion_id: str
    created: int
    include_usage: bool
    _calls: dict[int, _StreamedCall] = field(default_factory=dict[int, _StreamedCall])
    _streamed_text: bool = False
    _streamed_reasoning: bool = False

    def _chunk(self, delta: Json, finish: str | None = None) -> Json:
        return {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    def start(self) -> list[Json]:
        return [self._chunk({"role": "assistant", "content": ""})]

    def on_event(self, event: StreamEvent) -> list[Json]:
        match event:
            case TextDeltaEvent(delta=text) if text:
                self._streamed_text = True
                return [self._chunk({"content": text})]
            case ThinkingDeltaEvent(delta=text) if text:
                self._streamed_reasoning = True
                return [self._chunk({"reasoning_content": text})]
            case ToolCallDeltaEvent():
                return self._tool_fragment(event)
            case StreamCompleteEvent(response=response):
                return self.finish(response)
            case _:
                return []

    def _tool_fragment(self, event: ToolCallDeltaEvent) -> list[Json]:
        call = self._calls.get(event.index)
        if call is None:
            call = self._calls[event.index] = _StreamedCall(index=len(self._calls))
        call.call_id = call.call_id or event.id
        call.name = call.name or event.name
        call.pending += event.arguments_delta
        if not call.opened and call.call_id and call.name:
            call.opened = True
            arguments, call.pending = call.pending, ""
            return [self._chunk({"tool_calls": [_tool_delta(call.index, call.call_id, call.name, arguments)]})]
        if call.opened and call.pending:
            arguments, call.pending = call.pending, ""
            return [self._chunk({"tool_calls": [{"index": call.index, "function": {"arguments": arguments}}]})]
        return []

    def _reconcile(self, final: Sequence[ToolCall]) -> list[Json]:
        streamed = sorted(self._calls.values(), key=lambda c: c.index)
        matched: set[int] = set()
        out: list[Json] = []
        for position, call in enumerate(final):
            mine = next((s for s in streamed if s.index not in matched and s.call_id == call.id), None)
            if mine is None:
                mine = next((s for s in streamed if s.index not in matched and s.name == call.name), None)
            if mine is None:
                index = len(streamed) + position
                out.append(_tool_delta(index, call.id, call.name, tool_arguments(call)))
                continue
            matched.add(mine.index)
            if not mine.opened:
                out.append(_tool_delta(mine.index, mine.call_id or call.id, mine.name or call.name, tool_arguments(call)))
            elif mine.pending:
                out.append({"index": mine.index, "function": {"arguments": mine.pending}})
        return out

    def failed(self, code: str, message: str) -> list[Json]:
        return [error_body(message, code=code, kind="provider_error")]

    def finish(self, response: ModelResponse) -> list[Json]:
        chunks: list[Json] = []
        # A transport may report text only on its final response (none streamed): send it whole.
        reasoning = _reasoning(response.message)
        if reasoning and not self._streamed_reasoning:
            chunks.append(self._chunk({"reasoning_content": reasoning}))
        if response.message.text and not self._streamed_text:
            chunks.append(self._chunk({"content": response.message.text}))
        tail = self._reconcile(response.message.tool_calls or ())
        if tail:
            chunks.append(self._chunk({"tool_calls": tail}))
        chunks.append(self._chunk({}, finish_reason(response)))
        if self.include_usage:
            chunks.append(
                {
                    "id": self.completion_id,
                    "object": "chat.completion.chunk",
                    "created": self.created,
                    "model": self.model_id,
                    "choices": [],
                    "usage": usage_body(response.usage),
                }
            )
        return chunks


def _tool_delta(index: int, call_id: str, name: str, arguments: str) -> Json:
    return {"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def error_body(message: str, *, code: str, kind: str, param: str | None = None) -> Json:
    """OpenAI's error envelope; ``code`` is the gateway's reason code."""
    return {"error": {"message": message, "type": kind, "param": param, "code": code}}


def openai_error(
    status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
) -> tuple[Json, dict[str, str]]:
    """The OpenAI wires' (chat, Responses) error answer: the envelope, no headers."""
    kind = "provider_error" if provider else ("authentication_error" if status == 401 else "invalid_request_error")
    return error_body(message, code=code, kind=kind, param=param), {}


def models_body(model_ids: Sequence[str]) -> Json:
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": 0, "owned_by": "pipeshub"} for m in model_ids],
    }

# A gateway reason code: what a refusal's message is tagged with (``[gateway:<code>]``).
_GATEWAY_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ChatWire:
    """OpenAI chat completions (opencode, jcode): ``data:`` chunks, then ``[DONE]``."""

    api = ModelApi.CHAT_COMPLETIONS
    id_prefix = "chatcmpl"
    terminal = b"data: [DONE]\n\n"
    # An SSE comment: every reader skips it (the SSE specification), the OpenAI SDKs and ai-sdk included.
    keepalive = b": keepalive\n\n"
    # Chat history has no field a provider's reasoning state could ride back in.
    provider_state = None

    def parse(self, body: object, headers: Mapping[str, str] | None = None) -> ChatRequest:
        return parse_chat_request(body)

    def body(self, request: ChatRequest, response: ModelResponse, call: WireCall) -> Json:
        return completion_body(response, model_id=call.model_id, completion_id=call.response_id, created=call.created)

    def encoder(self, request: ChatRequest, call: WireCall) -> ChunkEncoder:
        return ChunkEncoder(call.model_id, call.response_id, call.created, request.include_usage)

    def frame(self, event: Json) -> bytes:
        return f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()

    def error(
        self, status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
    ) -> tuple[Json, dict[str, str]]:
        # An ACP agent passes on only a refusal's message (opencode's ACP server: "Internal error: <message>"): the
        # code travels in it too, so the run stops for the gateway's reason, as the Responses and Messages wires do.
        tagged = f"{message} [gateway:{code}]" if _GATEWAY_CODE.match(code) else message
        return openai_error(status, code, tagged, param=param, provider=provider)


CHAT_WIRE = ChatWire()


__all__ = [
    "CHAT_WIRE",
    "DROPPED_PARAMETERS",
    "REFUSED_PARAMETERS",
    "ChatRequest",
    "ChatWire",
    "ChunkEncoder",
    "completion_body",
    "error_body",
    "finish_reason",
    "image_source",
    "models_body",
    "openai_error",
    "parse_chat_request",
    "tool_arguments",
    "usage_body",
]
