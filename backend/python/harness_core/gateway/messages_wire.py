"""Anthropic's Messages API on the wire ↔ the kernel's model types (pure; E3c).

Claude Code speaks only this API to a custom ``ANTHROPIC_BASE_URL``. Inbound, the
request's headers (``anthropic-version``, ``anthropic-beta``) are checked against
what this wire serves, and ``system`` and the ``messages`` blocks (text, images,
``tool_use``, ``tool_result``, mid-conversation ``system`` messages) become the
kernel ``Message``s every ``LLMTransport`` speaks: the org's model is reached
through the Messages API where its transport is Anthropic's (direct, Bedrock,
Vertex), through chat or Responses otherwise. Outbound, a ``ModelResponse`` or its
``StreamEvent``s become a ``message`` or its ``message_start`` … ``message_stop``
events, and refusals and failures read in Anthropic's error vocabulary, with
``x-should-retry: false`` where retrying cannot help.

Stateless: nothing is stored; the caller sends its whole history every time. The provider's signed thinking travels
with that history (WP56-WIRE): the answer carries each thinking block with the provider's block sealed to the run's
org and route (``reasoning_state``), and a replayed sealed block rides on its assistant turn as provider state, which
an Anthropic transport sends back first and verbatim. A thinking block without a seal (a display copy, one from
elsewhere) is counted and never reaches the provider.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from harness_core.access.types import ModelApi
from harness_core.gateway.errors import InvalidMessagesRequest, UnsupportedParameter
from harness_core.gateway.reasoning_state import is_sealed, seal_anthropic_block
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    SystemMessage,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import StopReason
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.provider_state import (
    ANTHROPIC_MESSAGES,
    state_blocks,
    with_state,
)
from harness_core.models.transports.openai_responses import normalise_tool_call

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.gateway.wire import Json, WireCall
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.core.responses import ModelResponse, TokenUsage
    from harness_core.kernel.core.streaming import StreamEvent

ANTHROPIC_VERSION = "2023-06-01"
# Betas this wire serves: each one either means something the wire honours (system messages
# mid-conversation, effort, thinking edits) or changes nothing the gateway sends upstream
# (transports build their own requests; caching is the transport's). Any other beta is
# refused, never passed or ignored: Claude Code versions are pinned (E9), and a new
# version's betas are added here once it is tested against this wire.
ACCEPTED_BETAS = frozenset(
    {
        "claude-code-20250219",
        "interleaved-thinking-2025-05-14",
        "fine-grained-tool-streaming-2025-05-14",
        "token-efficient-tools-2025-02-19",
        "prompt-caching-2024-07-31",
        "prompt-caching-scope-2026-01-05",
        "extended-cache-ttl-2025-04-11",
        "context-management-2025-06-27",
        "mid-conversation-system-2026-04-07",
        "mid-conversation-tool-changes-2026-07-01",
        "effort-2025-11-24",
        # count_tokens' original beta (the endpoint is general now): Claude Code still sends it.
        "token-counting-2024-11-01",
        "thinking-token-count-2026-05-13",
        # Claude Code 2.1.289 in stream-json mode (E9 FB-1): asks for thinking blocks in the answer
        # (``thinking.display: updates``), which the wire serves as it serves any shown thinking.
        "thinking-display-updates-2026-08-18",
    }
)
# Parameters needing vendor state, vendor tools, a vendor-side safeguard or an inference region the
# gateway cannot honour: refused rather than ignored (principle 6).
REFUSED_PARAMETERS = frozenset({"stop_sequences", "container", "mcp_servers", "safeguards", "inference_geo", "output_format"})
_READ = frozenset(
    {
        "model",
        "messages",
        "system",
        "max_tokens",
        "stream",
        "tools",
        "tool_choice",
        "thinking",
        "output_config",
        "context_management",
    }
)
# Claude Code's first system block identifies the client to Anthropic, as a tracking header would.
BILLING_BLOCK_PREFIX = "x-anthropic-billing-header:"
# Claude Code classifies a 400 by these (``capability_rejected: <capability>``) and adapts to some.
CAPABILITY_REJECTED = "capability_rejected:"
# Claude Code recognises a context overflow by this text and compacts.
OVERFLOW_MESSAGE = "prompt is too long: the model's context window is exceeded"
_BETA_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_GATEWAY_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_STOP_REASONS: Mapping[StopReason, str] = {
    StopReason.END_TURN: "end_turn",
    StopReason.TOOL_USE: "tool_use",
    StopReason.MAX_TOKENS: "max_tokens",
    StopReason.STOP_SEQUENCE: "end_turn",
    StopReason.ERROR: "end_turn",
    StopReason.CANCELLED: "end_turn",
}
_STATUS_ERROR_TYPES: Mapping[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    402: "billing_error",
    403: "permission_error",
    404: "not_found_error",
    409: "invalid_request_error",
    413: "request_too_large",
    429: "rate_limit_error",
}
# The gateway's codes whose meaning Claude Code acts on, in Anthropic's error types.
_CODE_ERROR_TYPES: Mapping[str, str] = {
    "rate_limited": "rate_limit_error",
    "provider_quota": "billing_error",
    "budget_exhausted": "billing_error",
    "budget_insufficient": "billing_error",
    "context_length_exceeded": "invalid_request_error",
    "content_filter": "invalid_request_error",
    "provider_rejected": "invalid_request_error",
}
_RETRIED_STATUSES = frozenset({401, 408, 429})
# A 5xx Claude Code would retry, although the org's credential stays refused.
_NEVER_RETRIED = frozenset({"provider_auth_failed"})


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    model: str
    system: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    stream: bool
    reasoning_effort: str | None
    max_tokens: int | None
    show_thinking: bool = False
    betas: tuple[str, ...] = ()
    reasoning_items: int = 0
    encrypted_reasoning: bool = False
    dropped: tuple[str, ...] = ()
    display_reasoning: int = 0

    @property
    def output_limit(self) -> int | None:
        return self.max_tokens


def _object(value: object, what: str) -> Json:
    if not isinstance(value, dict):
        raise InvalidMessagesRequest(f"{what} must be an object", param=what)
    return cast("Json", value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidMessagesRequest(f"{what} must be a list", param=what)
    return cast("list[object]", value)


def _string(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidMessagesRequest(f"{what} must be a non-empty string", param=what)
    return value


def _rejected(param: str, capability: str, why: str) -> UnsupportedParameter:
    return UnsupportedParameter(param, f"{CAPABILITY_REJECTED} {capability} ({why})")


def check_headers(headers: Mapping[str, str]) -> tuple[str, ...]:
    """The request's betas, every one of them served; otherwise a refusal naming each refused one."""
    lowered = {name.lower(): value for name, value in headers.items()}
    version = lowered.get("anthropic-version")
    if version is not None and version.strip() != ANTHROPIC_VERSION:
        raise UnsupportedParameter("anthropic-version")
    betas = tuple(b.strip() for b in lowered.get("anthropic-beta", "").split(",") if b.strip())
    refused = sorted({b for b in betas if b not in ACCEPTED_BETAS})
    if refused:
        rejected = "; ".join(f"{CAPABILITY_REJECTED} beta_header:{b}" for b in refused)
        named = ",".join(b if _BETA_NAME.match(b) else "<unrecognised>" for b in refused)
        raise UnsupportedParameter(
            "anthropic-beta", f"{rejected} (this gateway does not serve these betas)", detail=named
        )
    return betas


@dataclass(slots=True)
class _Parse:
    """What a parse collects besides the messages: dropped names and reasoning blocks."""

    dropped: set[str] = field(default_factory=set[str])
    reasoning_items: int = 0
    # Thinking blocks with no signature: the gateway's own display copies, which carry nothing to hand back.
    display_reasoning: int = 0

    def note(self, block: Json, where: str) -> None:
        for key in ("cache_control", "citations"):
            if block.get(key) is not None:
                self.dropped.add(f"{where}.{key}")


def _text_blocks(ctx: _Parse, value: object, where: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    texts: list[str] = []
    for item in _list(value, where):
        block = _object(item, where)
        if block.get("type") != "text":
            raise UnsupportedParameter(f"{where}.type={block.get('type')}")
        ctx.note(block, where)
        texts.append(str(block.get("text") or ""))
    return texts


def _system(ctx: _Parse, value: object) -> list[str]:
    if value is None:
        return []
    texts: list[str] = []
    for text in _text_blocks(ctx, value, "system"):
        if text.startswith(BILLING_BLOCK_PREFIX):
            ctx.dropped.add("system.billing_header")
        elif text:
            texts.append(text)
    return texts


def _image(block: Json, where: str) -> ImagePart:
    source = _object(block.get("source"), f"{where}.image.source")
    kind = source.get("type")
    if kind == "base64":
        media = source.get("media_type")
        return ImagePart(
            source=ImageSource(
                type="base64",
                media_type=media if isinstance(media, str) else None,
                data=_string(source.get("data"), f"{where}.image.source.data"),
            )
        )
    # A ``url`` source is fetched by the provider from its own network: a channel out of the sandbox to any
    # host the caller names (SEC1 MX-8). A ``file`` source names a file uploaded to the vendor, which the
    # gateway does not hold.
    raise _rejected(f"{where}.image.source.type={kind}", "image_block", "only base64 images are served")


def _parts(ctx: _Parse, block: Json, where: str) -> Part:
    kind = block.get("type")
    ctx.note(block, where)
    if kind == "text":
        return TextPart(text=str(block.get("text") or ""))
    if kind == "image":
        return _image(block, where)
    if kind == "document":
        raise _rejected(f"{where}.type=document", "document_block", "documents are not served; send text")
    raise UnsupportedParameter(f"{where}.type={kind}")


def _content(parts: list[Part]) -> str | list[Part]:
    if any(isinstance(p, ImagePart) for p in parts):
        return parts
    return "".join(p.text for p in parts if isinstance(p, TextPart))


def _tool_result(ctx: _Parse, block: Json) -> ToolMessage:
    where = "messages.content.tool_result"
    call_id = _string(block.get("tool_use_id"), f"{where}.tool_use_id")
    raw = block.get("content")
    if raw is None or isinstance(raw, str):
        content: str | list[Part] = raw or ""
    else:
        content = _content([_parts(ctx, _object(item, where), f"{where}.content") for item in _list(raw, where)])
    return ToolMessage(content=content, tool_call_id=call_id, is_error=block.get("is_error") is True)


@dataclass(slots=True)
class _History:
    """Folds messages into kernel messages: consecutive assistant messages are one turn (what
    every transport's history expects), a user's tool results come before the rest of it."""

    ctx: _Parse
    system: list[str] = field(default_factory=list[str])
    messages: list[Message] = field(default_factory=list["Message"])
    _text: list[str] = field(default_factory=list[str])
    _calls: list[ToolCall] = field(default_factory=list[ToolCall])
    _thinking: list[Json] = field(default_factory=list["Json"])

    def _flush(self) -> None:
        if self._text or self._calls or self._thinking:
            message = AssistantMessage(
                content="".join(self._text) or None,  # pyright: ignore[reportArgumentType]
                tool_calls=list(self._calls) or None,
            )
            self.messages.append(with_state(message, ANTHROPIC_MESSAGES, self._thinking))
        self._text, self._calls, self._thinking = [], [], []

    def add(self, raw: Json) -> None:
        role = raw.get("role")
        content = raw.get("content")
        if role == "assistant":
            self._assistant(content)
            return
        self._flush()
        if role == "user":
            self._user(content)
        elif role == "system":
            text = "".join(_text_blocks(self.ctx, content if content is not None else "", "messages.content"))
            if self.messages:
                self.messages.append(SystemMessage(content=text))
            elif text:
                self.system.append(text)
        else:
            raise InvalidMessagesRequest(f"unknown message role {role!r}", param="messages.role")

    def _user(self, content: object) -> None:
        if isinstance(content, str):
            self.messages.append(UserMessage(content=content))
            return
        results: list[ToolMessage] = []
        parts: list[Part] = []
        for item in _list(content, "messages.content"):
            block = _object(item, "messages.content")
            if block.get("type") == "tool_result":
                self.ctx.note(block, "messages.content")
                results.append(_tool_result(self.ctx, block))
            else:
                parts.append(_parts(self.ctx, block, "messages.content"))
        self.messages.extend(results)
        if parts:
            self.messages.append(UserMessage(content=_content(parts)))

    def _assistant(self, content: object) -> None:
        if isinstance(content, str):
            self._text.append(content)
            return
        for item in _list(content, "messages.content"):
            block = _object(item, "messages.content")
            kind = block.get("type")
            self.ctx.note(block, "messages.content")
            if kind == "text":
                self._text.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                call_id = _string(block.get("id"), "messages.content.tool_use.id")
                name = _string(block.get("name"), "messages.content.tool_use.name")
                arguments = _object(block.get("input"), "messages.content.tool_use.input")
                self._calls.append(normalise_tool_call(call_id, name, json.dumps(arguments)))
            elif kind in ("thinking", "redacted_thinking"):
                self.ctx.reasoning_items += 1
                sealed = block.get("data") if kind == "redacted_thinking" else block.get("signature")
                if is_sealed(sealed):
                    self._thinking.append(dict(block))
                elif kind == "thinking" and not sealed:
                    self.ctx.display_reasoning += 1
            else:
                raise UnsupportedParameter(f"messages.content.type={kind}")

    def done(self) -> None:
        self._flush()


_TOOL_KEYS = frozenset({"type", "name", "description", "input_schema", "defer_loading", "strict"})


def _tools(ctx: _Parse, value: object) -> tuple[ToolSchema, ...]:
    if value is None:
        return ()
    schemas: list[ToolSchema] = []
    names: set[str] = set()
    for item in _list(value, "tools"):
        tool = _object(item, "tools")
        kind = tool.get("type")
        if kind not in (None, "custom"):
            raise UnsupportedParameter(f"tools.type={kind}")
        if tool.get("defer_loading") is True:
            raise UnsupportedParameter("tools.defer_loading")
        if tool.get("strict") is True:
            raise UnsupportedParameter("tools.strict")
        name = _string(tool.get("name"), "tools.name")
        if name in names:
            raise InvalidMessagesRequest(f"two tools are named {name!r}", param="tools.name")
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            raise InvalidMessagesRequest("a tool needs an input_schema object", param="tools.input_schema")
        ctx.dropped.update(f"tools.{key}" for key in tool if key not in _TOOL_KEYS)
        names.add(name)
        schemas.append(
            ToolSchema(
                name=name,
                description=str(tool.get("description") or ""),
                input_schema=cast("dict[str, object]", schema),
            )
        )
    return tuple(schemas)


def _check_options(ctx: _Parse, request: Json) -> None:
    for name in sorted(REFUSED_PARAMETERS & request.keys()):
        value = request[name]
        if value is None or (name == "stop_sequences" and value == []):
            continue
        if name == "output_format":
            raise _rejected(name, "structured_outputs_unsupported", "structured output is not served")
        raise UnsupportedParameter(name)
    choice = request.get("tool_choice")
    if choice is not None:
        chosen = _object(choice, "tool_choice")
        if chosen.get("type") != "auto":
            raise UnsupportedParameter("tool_choice")
        ctx.dropped.update(f"tool_choice.{key}" for key in chosen if key != "type")
    management = request.get("context_management")
    if management is not None:
        for edit in _list(_object(management, "context_management").get("edits") or [], "context_management.edits"):
            kind = _object(edit, "context_management.edits").get("type")
            # Clearing thinking needs nothing here: the provider strips earlier turns' thinking blocks itself, and
            # only the open tool turn's must go back (WP56-WIRE). Other edits are server-side state.
            if not isinstance(kind, str) or not kind.startswith("clear_thinking_"):
                raise UnsupportedParameter(f"context_management.edits.type={kind}")


def _effort(ctx: _Parse, value: object) -> str | None:
    if value is None:
        return None
    config = _object(value, "output_config")
    if config.get("format") is not None:
        raise _rejected("output_config.format", "structured_outputs_unsupported", "structured output is not served")
    ctx.dropped.update(f"output_config.{key}" for key in config if key not in ("effort", "format"))
    effort = config.get("effort")
    return effort if isinstance(effort, str) and effort else None


def _show_thinking(value: object) -> bool:
    if value is None:
        return False
    thinking = _object(value, "thinking")
    kind = thinking.get("type")
    if kind not in ("enabled", "adaptive", "disabled"):
        raise InvalidMessagesRequest("thinking.type must be enabled, adaptive or disabled", param="thinking.type")
    return kind != "disabled" and thinking.get("display") != "omitted"


def _positive(value: object, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidMessagesRequest(f"{what} must be a positive integer", param=what)
    return value


def parse_messages_request(body: object, headers: Mapping[str, str] | None = None) -> MessagesRequest:
    betas = check_headers(headers or {})
    request = _object(body, "body")
    ctx = _Parse()
    _check_options(ctx, request)
    model = request.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidMessagesRequest("model is required", param="model")
    raw_messages = request.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise InvalidMessagesRequest("messages must be a non-empty list", param="messages")
    history = _History(ctx, system=_system(ctx, request.get("system")))
    for item in cast("list[object]", raw_messages):
        history.add(_object(item, "messages"))
    history.done()
    if not history.messages:
        raise InvalidMessagesRequest("messages must hold a user or assistant message", param="messages")
    tools = _tools(ctx, request.get("tools"))
    effort = _effort(ctx, request.get("output_config"))
    ctx.dropped.update(n for n in request if n not in _READ and n not in REFUSED_PARAMETERS)
    return MessagesRequest(
        model=model,
        system="\n\n".join(history.system) or None,
        messages=tuple(history.messages),
        tools=tools,
        stream=request.get("stream") is True,
        reasoning_effort=effort,
        max_tokens=_positive(request.get("max_tokens"), "max_tokens"),
        show_thinking=_show_thinking(request.get("thinking")),
        betas=betas,
        reasoning_items=ctx.reasoning_items,
        display_reasoning=ctx.display_reasoning,
        dropped=tuple(sorted(ctx.dropped)),
    )


def messages_usage(usage: TokenUsage) -> Json:
    """Anthropic's ``input_tokens`` is the uncached input, with reads and writes beside it."""
    return {
        "input_tokens": usage.uncached_input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_write_tokens,
        "cache_read_input_tokens": usage.cache_read_tokens,
    }


def stop_reason(response: ModelResponse) -> str:
    if response.message.tool_calls and response.stop_reason != StopReason.MAX_TOKENS:
        return "tool_use"
    return _STOP_REASONS.get(response.stop_reason, "end_turn")


def _thinking_text(response: ModelResponse) -> str:
    return "".join(p.thinking for p in response.message.content if isinstance(p, ThinkingPart))


def _tool_use(call: ToolCall) -> Json:
    # The kernel's arguments, a malformed call's sentinel included: a Messages client needs an object.
    return {"type": "tool_use", "id": call.id, "name": call.name, "input": dict(call.arguments)}


def _signed_thinking(response: ModelResponse, call: WireCall) -> list[Json]:
    """The provider's signed thinking of this answer, for an engine to hand back; none without a seal tag."""
    return state_blocks(response.message, ANTHROPIC_MESSAGES) if call.state_tag else []


def message_body(request: MessagesRequest, response: ModelResponse, call: WireCall) -> Json:
    content: list[Json] = []
    signed = _signed_thinking(response, call)
    thinking = _thinking_text(response)
    if signed:
        content.extend(seal_anthropic_block(b, call.state_tag, shown=request.show_thinking) for b in signed)
    elif thinking and request.show_thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": ""})
    if response.message.text:
        content.append({"type": "text", "text": response.message.text})
    content.extend(_tool_use(c) for c in response.message.tool_calls or ())
    return {
        "id": call.response_id,
        "type": "message",
        "role": "assistant",
        "model": call.model_id,
        "content": content,
        "stop_reason": stop_reason(response),
        "stop_sequence": None,
        "usage": messages_usage(response.usage),
    }


def error_type(status: int, code: str) -> str:
    return _CODE_ERROR_TYPES.get(code) or _STATUS_ERROR_TYPES.get(status) or (
        "api_error" if status >= 500 else "invalid_request_error"
    )


def retryable(status: int, code: str) -> bool:
    return code not in _NEVER_RETRIED and (status in _RETRIED_STATUSES or status >= 500)


def messages_error(
    status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
) -> tuple[Json, dict[str, str]]:
    """Anthropic's error envelope; ``x-should-retry: false`` stops Claude Code's retries (budgets, quota, refusals)."""
    text = OVERFLOW_MESSAGE if code == "context_length_exceeded" else message
    if _GATEWAY_CODE.match(code):
        # Claude Code keeps only the status and this text of a refused call (no headers): the code travels here
        # so the run can stop for the gateway's reason, not "HTTP 403" (E9 FB-3).
        text = f"{text} [gateway:{code}]"
    body: Json = {"type": "error", "error": {"type": error_type(status, code), "message": text}}
    return body, ({} if retryable(status, code) else {"x-should-retry": "false"})


Block = Literal["text", "thinking"]


@dataclass(slots=True)
class MessageEncoder:
    """One message's events. A text or thinking block opens with its first delta and closes
    before the next block or at the end; tool calls go whole from the final response (an
    Anthropic client parses every ``input_json_delta``, and the final response is the truth
    about the calls in every family); then ``message_delta`` and ``message_stop``."""

    request: MessagesRequest
    call: WireCall
    _index: int = 0
    _open: Block | None = None
    _streamed_text: bool = False
    _streamed_thinking: bool = False

    def start(self) -> list[Json]:
        message: Json = {
            "id": self.call.response_id,
            "type": "message",
            "role": "assistant",
            "model": self.call.model_id,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        return [{"type": "message_start", "message": message}]

    def on_event(self, event: StreamEvent) -> list[Json]:
        match event:
            case TextDeltaEvent(delta=text) if text:
                self._streamed_text = True
                return self._delta("text", text)
            case ThinkingDeltaEvent(delta=text) if text:
                self._streamed_thinking = True
                return self._delta("thinking", text) if self.request.show_thinking else []
            case StreamCompleteEvent(response=response):
                return self.finish(response)
            case _:
                return []

    def _delta(self, kind: Block, text: str) -> list[Json]:
        events: list[Json] = []
        if self._open != kind:
            events += self._close()
            block: Json = {"type": "text", "text": ""} if kind == "text" else {"type": "thinking", "thinking": "", "signature": ""}
            events.append({"type": "content_block_start", "index": self._index, "content_block": block})
            self._open = kind
        delta: Json = {"type": "text_delta", "text": text} if kind == "text" else {"type": "thinking_delta", "thinking": text}
        events.append({"type": "content_block_delta", "index": self._index, "delta": delta})
        return events

    def _close(self) -> list[Json]:
        if self._open is None:
            return []
        event: Json = {"type": "content_block_stop", "index": self._index}
        self._index += 1
        self._open = None
        return [event]

    def _call(self, call: ToolCall) -> list[Json]:
        index, self._index = self._index, self._index + 1
        return [
            {"type": "content_block_start", "index": index,
             "content_block": {"type": "tool_use", "id": call.id, "name": call.name, "input": {}}},
            {"type": "content_block_delta", "index": index,
             "delta": {"type": "input_json_delta", "partial_json": json.dumps(call.arguments, ensure_ascii=False)}},
            {"type": "content_block_stop", "index": index},
        ]

    def finish(self, response: ModelResponse) -> list[Json]:
        events: list[Json] = []
        signed = _signed_thinking(response, self.call)
        thinking = _thinking_text(response)
        if thinking and self.request.show_thinking and not self._streamed_thinking and not signed:
            events += self._delta("thinking", thinking)
        if response.message.text and not self._streamed_text:
            events += self._delta("text", response.message.text)
        events += self._close()
        # After the text, as a whole answer's thinking comes when only the final response has it: the engine hands
        # the blocks back with the turn, and the gateway puts them first for the provider.
        shown = self.request.show_thinking and not self._streamed_thinking
        for block in signed:
            events += self._thinking_block(seal_anthropic_block(block, self.call.state_tag, shown=shown))
        for call in response.message.tool_calls or ():
            events += self._call(call)
        events.append(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason(response), "stop_sequence": None},
                "usage": messages_usage(response.usage),
            }
        )
        events.append({"type": "message_stop"})
        return events

    def _thinking_block(self, block: Json) -> list[Json]:
        index, self._index = self._index, self._index + 1
        if block["type"] == "redacted_thinking":
            return [
                {"type": "content_block_start", "index": index, "content_block": block},
                {"type": "content_block_stop", "index": index},
            ]
        events: list[Json] = [
            {"type": "content_block_start", "index": index,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        ]
        if block["thinking"]:
            events.append({"type": "content_block_delta", "index": index,
                           "delta": {"type": "thinking_delta", "thinking": block["thinking"]}})
        events += [
            {"type": "content_block_delta", "index": index,
             "delta": {"type": "signature_delta", "signature": block["signature"]}},
            {"type": "content_block_stop", "index": index},
        ]
        return events

    def failed(self, code: str, message: str) -> list[Json]:
        body, _ = messages_error(500, code, message)
        return [body]


class MessagesWire:
    """Anthropic Messages (Claude Code): typed ``event:``/``data:`` frames, no terminal sentinel."""

    api = ModelApi.ANTHROPIC_MESSAGES
    id_prefix = "msg"
    terminal = b""
    # The API's own keepalive event; every Anthropic client skips it.
    keepalive = b'event: ping\ndata: {"type": "ping"}\n\n'
    provider_state = ANTHROPIC_MESSAGES

    def parse(self, body: object, headers: Mapping[str, str] | None = None) -> MessagesRequest:
        return parse_messages_request(body, headers)

    def body(self, request: MessagesRequest, response: ModelResponse, call: WireCall) -> Json:
        return message_body(request, response, call)

    def encoder(self, request: MessagesRequest, call: WireCall) -> MessageEncoder:
        return MessageEncoder(request, call)

    def frame(self, event: Json) -> bytes:
        return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()

    def error(
        self, status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
    ) -> tuple[Json, dict[str, str]]:
        return messages_error(status, code, message, param=param, provider=provider)


MESSAGES_WIRE = MessagesWire()


__all__ = [
    "ACCEPTED_BETAS",
    "ANTHROPIC_VERSION",
    "BILLING_BLOCK_PREFIX",
    "CAPABILITY_REJECTED",
    "MESSAGES_WIRE",
    "OVERFLOW_MESSAGE",
    "REFUSED_PARAMETERS",
    "MessageEncoder",
    "MessagesRequest",
    "MessagesWire",
    "check_headers",
    "error_type",
    "message_body",
    "messages_error",
    "messages_usage",
    "parse_messages_request",
    "retryable",
    "stop_reason",
]
