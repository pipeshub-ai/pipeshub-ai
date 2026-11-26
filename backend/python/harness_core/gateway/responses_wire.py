"""OpenAI Responses API on the wire ↔ the kernel's model types (pure; E3b).

Codex speaks only this API to a custom provider. Inbound, ``instructions`` and
the ``input`` items (messages, ``function_call``, ``function_call_output``)
become the kernel ``Message``s every ``LLMTransport`` speaks, so the org's model
is reached the way E3 reaches it: through the Responses API where the
transport is configured for it (OpenAI, Azure OpenAI reasoning deployments),
through chat or the provider's own API otherwise. Outbound, a ``ModelResponse``
or its ``StreamEvent``s become a ``response`` object or its typed events.

Stateless: the gateway stores no response. A caller sends its whole history
with ``store: false`` (what Codex sends a custom provider); anything that
names a stored response or conversation is refused. The provider's reasoning
travels with that history instead (WP-62): a ``reasoning`` item with
``encrypted_content`` the gateway handed out is put back on the assistant turn
it belongs to as provider state, which a Responses transport replays to the
provider; with ``include: ["reasoning.encrypted_content"]`` the answer carries
the provider's reasoning items, sealed to the run's org and route
(``reasoning_state``). ``parallel_tool_calls`` and ``prompt_cache_key`` are
read and passed to a transport that can send them (``models.call_options``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from harness_core.access.types import ModelApi
from harness_core.gateway.chat_wire import image_source, openai_error, tool_arguments
from harness_core.gateway.errors import (
    InvalidResponsesRequest,
    ResponseStateUnsupported,
    UnsupportedParameter,
)
from harness_core.gateway.reasoning_state import seal
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
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
    ToolCallDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.provider_state import (
    OPENAI_RESPONSES,
    state_blocks,
    with_state,
)
from harness_core.models.transports.openai_responses import normalise_tool_call

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.gateway.wire import Json, WireCall
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.core.responses import ModelResponse, TokenUsage
    from harness_core.kernel.core.streaming import StreamEvent

# Parameters that need a response or conversation the gateway would have to keep.
STATE_PARAMETERS = frozenset({"previous_response_id", "conversation"})
_GATEWAY_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Parameters whose meaning the transports cannot honour: refused rather than ignored (principle 6).
REFUSED_PARAMETERS = frozenset({"top_logprobs", "prompt", "max_tool_calls"})
# Accepted but not forwarded: sampling is pinned by the org's model configuration, and the rest identify the
# caller to a vendor (``client_metadata``: Codex's installation, session and window ids, which no provider API
# documents and which would tell the vendor about the user's machine), pick a vendor's tier, or set a cache
# retention the org's cache policy decides (``prompt_cache.PromptCacheSettings``).
DROPPED_PARAMETERS = frozenset(
    {
        "temperature",
        "top_p",
        "user",
        "metadata",
        "client_metadata",
        "service_tier",
        "prompt_cache_retention",
        "safety_identifier",
        "stream_options",
    }
)
# What ``include`` may ask for: Codex asks for encrypted reasoning, answered where the transport carries it.
ACCEPTED_INCLUDES = frozenset({"reasoning.encrypted_content"})
_READ = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "stream",
        "store",
        "include",
        "reasoning",
        "text",
        "max_output_tokens",
        "truncation",
        "background",
        "parallel_tool_calls",
        "prompt_cache_key",
    }
)
# Joins a namespace and a tool name into one function name every provider accepts
# (``^[a-zA-Z0-9_-]{1,64}$``); the request's own tool table maps it back.
# The gateway's provider-failure codes in OpenAI's vocabulary, where a Responses client
# (Codex: ``codex-api/src/sse/responses_error.rs``) decides on them: quota is not retried,
# a rate limit is, a context overflow ends the turn. Other codes pass as they are, and Codex
# retries any code it does not know, so the run's budget (P6.5) is quota too.
OPENAI_FAILURE_CODES: Mapping[str, str] = {
    "rate_limited": "rate_limit_exceeded",
    "provider_quota": "insufficient_quota",
    "budget_exhausted": "insufficient_quota",
    "budget_insufficient": "insufficient_quota",
    "context_length_exceeded": "context_length_exceeded",
}
NAMESPACE_SEPARATOR = "__"
MAX_TOOL_NAME = 64


@dataclass(frozen=True, slots=True)
class WireTool:
    """A function the caller declared, as it named it: ``namespace`` when it was a namespace's member."""

    name: str
    namespace: str | None = None

    @property
    def flat(self) -> str:
        return f"{self.namespace}{NAMESPACE_SEPARATOR}{self.name}" if self.namespace else self.name


@dataclass(frozen=True, slots=True)
class ResponsesRequest:
    model: str
    system: str | None
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    stream: bool
    reasoning_effort: str | None
    max_output_tokens: int | None
    instructions: str | None = None
    wire_tools: Mapping[str, WireTool] = field(default_factory=dict[str, WireTool])
    tools_echo: tuple[Json, ...] = ()
    reasoning_items: int = 0
    # Reasoning items with no encrypted content: the caller's copy of a summary, nothing a provider can read back.
    display_reasoning: int = 0
    encrypted_reasoning: bool = False
    dropped: tuple[str, ...] = ()
    # What the caller asked about its tools and its prompt cache; the gateway passes them to a transport that
    # sends them (WP-62).
    parallel_tool_calls: bool | None = None
    cache_key: str | None = None

    def wire_tool(self, flat_name: str) -> WireTool:
        return self.wire_tools.get(flat_name) or WireTool(flat_name)

    @property
    def output_limit(self) -> int | None:
        return self.max_output_tokens


def _object(value: object, what: str) -> Json:
    if not isinstance(value, dict):
        raise InvalidResponsesRequest(f"{what} must be an object", param=what)
    return cast("Json", value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidResponsesRequest(f"{what} must be a list", param=what)
    return cast("list[object]", value)


def _string(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidResponsesRequest(f"{what} must be a non-empty string", param=what)
    return value


def _parts(raw: object, what: str) -> list[Part]:
    """Content parts of an input message or a tool output: text and images only."""
    if isinstance(raw, str):
        return [TextPart(text=raw)]
    parts: list[Part] = []
    for item in _list(raw, what):
        part = _object(item, what)
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text"):
            parts.append(TextPart(text=str(part.get("text") or "")))
        elif kind == "refusal":
            parts.append(TextPart(text=str(part.get("refusal") or "")))
        elif kind == "input_image":
            url = part.get("image_url")
            if not isinstance(url, str) or not url:
                # A ``file_id`` names a file uploaded to the vendor, which the gateway does not hold.
                raise UnsupportedParameter(f"{what}.input_image.file_id")
            parts.append(ImagePart(source=image_source(url, f"{what}.input_image.image_url")))
        else:
            raise UnsupportedParameter(f"{what}.type={kind}")
    return parts


def _content(parts: list[Part]) -> str | list[Part]:
    if any(isinstance(p, ImagePart) for p in parts):
        return parts
    return "".join(p.text for p in parts if isinstance(p, TextPart))


def _flat_call_name(item: Json, what: str) -> str:
    name = _string(item.get("name"), f"{what}.name")
    namespace = item.get("namespace")
    return WireTool(name, namespace if isinstance(namespace, str) and namespace else None).flat


@dataclass(slots=True)
class _History:
    """Folds input items into kernel messages: consecutive assistant items (text and
    function calls) are one assistant turn, as every transport's history expects."""

    system: list[str] = field(default_factory=list[str])
    messages: list[Message] = field(default_factory=list["Message"])
    reasoning_items: int = 0
    display_reasoning: int = 0
    _text: list[str] = field(default_factory=list[str])
    _calls: list[ToolCall] = field(default_factory=list[ToolCall])
    _state: list[dict[str, object]] = field(default_factory=list[dict[str, object]])

    def _flush(self) -> None:
        if self._text or self._calls:
            message = AssistantMessage(
                content="".join(self._text) or None,  # pyright: ignore[reportArgumentType]
                tool_calls=list(self._calls) or None,
            )
            # Every reasoning item of an assistant run belongs to that turn, wherever the answer put it (the
            # gateway sends a streamed answer's encrypted items after its text and calls); the transport replays
            # them before the turn's items, as the provider produced them.
            self.messages.append(with_state(message, OPENAI_RESPONSES, self._state))  # pyright: ignore[reportArgumentType]
        self._text, self._calls, self._state = [], [], []

    def add(self, item: Json) -> None:
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            self._message(item)
        elif kind == "function_call":
            raw = item.get("arguments")
            arguments = raw if isinstance(raw, str) else json.dumps(raw if raw is not None else {})
            call_id = _string(item.get("call_id"), "input.function_call.call_id")
            self._calls.append(normalise_tool_call(call_id, _flat_call_name(item, "input.function_call"), arguments))
        elif kind == "function_call_output":
            self._flush()
            call_id = _string(item.get("call_id"), "input.function_call_output.call_id")
            output = _content(_parts(item.get("output"), "input.function_call_output.output"))
            self.messages.append(ToolMessage(content=output, tool_call_id=call_id))
        elif kind == "reasoning":
            self.reasoning_items += 1
            self._reasoning(item)
        elif kind == "item_reference":
            raise ResponseStateUnsupported("an item reference names a stored item", param="input.item_reference")
        else:
            raise UnsupportedParameter(f"input.type={kind}")

    def _reasoning(self, item: Json) -> None:
        """Only an item with encrypted content can go back: under ``store: false`` the provider has nothing else to
        read (an item without it, the summary the gateway streamed, is the caller's display copy)."""
        encrypted = item.get("encrypted_content")
        if not isinstance(encrypted, str) or not encrypted:
            self.display_reasoning += 1
            return
        summary = item.get("summary")
        self._state.append({
            "type": "reasoning",
            "id": str(item.get("id") or ""),
            "encrypted_content": encrypted,
            "summary": summary if isinstance(summary, list) else [],
        })

    def _message(self, item: Json) -> None:
        role = item.get("role")
        parts = _parts(item.get("content") if item.get("content") is not None else "", "input.content")
        if role == "assistant":
            self._text.append("".join(p.text for p in parts if isinstance(p, TextPart)))
            return
        self._flush()
        if role in ("system", "developer"):
            text = "".join(p.text for p in parts if isinstance(p, TextPart))
            if self.messages:
                self.messages.append(SystemMessage(content=text))
            else:
                self.system.append(text)
        elif role == "user":
            self.messages.append(UserMessage(content=_content(parts)))
        else:
            raise InvalidResponsesRequest(f"unknown message role {role!r}", param="input.role")

    def done(self) -> None:
        self._flush()


def _function(tool: Json, namespace: str | None) -> tuple[WireTool, ToolSchema]:
    name = _string(tool.get("name"), "tools.name")
    wire = WireTool(name, namespace)
    if len(wire.flat) > MAX_TOOL_NAME:
        raise InvalidResponsesRequest("a tool's name is longer than providers accept", param="tools.name")
    parameters = tool.get("parameters")
    description = str(tool.get("description") or "")
    return wire, ToolSchema(
        name=wire.flat,
        description=description,
        input_schema=cast("dict[str, object]", parameters) if isinstance(parameters, dict) else {},
    )


def _tools(value: object) -> tuple[tuple[ToolSchema, ...], dict[str, WireTool], tuple[Json, ...]]:
    if value is None:
        return (), {}, ()
    schemas: list[ToolSchema] = []
    table: dict[str, WireTool] = {}
    echo: list[Json] = []
    for item in _list(value, "tools"):
        tool = _object(item, "tools")
        kind = tool.get("type")
        if kind == "function":
            members = [_function(tool, None)]
        elif kind == "namespace":
            namespace = _string(tool.get("name"), "tools.namespace.name")
            members: list[tuple[WireTool, ToolSchema]] = []
            for member in _list(tool.get("tools"), "tools.namespace.tools"):
                inner = _object(member, "tools.namespace.tools")
                if inner.get("type") != "function":
                    raise UnsupportedParameter(f"tools.namespace.tools.type={inner.get('type')}")
                members.append(_function(inner, namespace))
        else:
            raise UnsupportedParameter(f"tools.type={kind}")
        for wire, schema in members:
            if schema.name in table:
                raise InvalidResponsesRequest(f"two tools are named {schema.name!r}", param="tools.name")
            table[schema.name] = wire
            schemas.append(schema)
        echo.append(tool)
    return tuple(schemas), table, tuple(echo)


def _positive(value: object, what: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidResponsesRequest(f"{what} must be a positive integer", param=what)
    return value


def _check_state(request: Json) -> None:
    for name in sorted(STATE_PARAMETERS & request.keys()):
        if request[name] is not None:
            raise ResponseStateUnsupported("this gateway keeps no responses; send the whole input", param=name)
    if request.get("store") is True:
        raise ResponseStateUnsupported("this gateway stores no responses; send store: false", param="store")
    if request.get("background") is True:
        raise ResponseStateUnsupported("background responses are stored responses", param="background")


def _check_options(request: Json) -> None:
    for name in sorted(REFUSED_PARAMETERS & request.keys()):
        if request[name] is not None:
            raise UnsupportedParameter(name)
    if request.get("tool_choice") not in (None, "auto"):
        raise UnsupportedParameter("tool_choice")
    if request.get("truncation") not in (None, "disabled"):
        raise UnsupportedParameter("truncation")
    text = request.get("text")
    if text is not None:
        fmt = _object(text, "text").get("format")
        if fmt is not None and _object(fmt, "text.format").get("type") != "text":
            raise UnsupportedParameter("text.format")
    include = request.get("include")
    if include is not None:
        for value in _list(include, "include"):
            if value not in ACCEPTED_INCLUDES:
                raise UnsupportedParameter(f"include={value}")


def parse_responses_request(body: object) -> ResponsesRequest:
    request = _object(body, "body")
    _check_state(request)
    _check_options(request)
    model = request.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidResponsesRequest("model is required", param="model")
    instructions = request.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise UnsupportedParameter("instructions")
    raw_input = request.get("input")
    history = _History()
    if isinstance(raw_input, str) and raw_input:
        history.messages.append(UserMessage(content=raw_input))
    else:
        for item in _list(raw_input, "input"):
            history.add(_object(item, "input"))
        history.done()
    if not history.messages:
        raise InvalidResponsesRequest("input must hold a message", param="input")
    tools, table, echo = _tools(request.get("tools"))
    reasoning = request.get("reasoning")
    effort = _object(reasoning, "reasoning").get("effort") if reasoning is not None else None
    system = [instructions] if instructions else []
    include = request.get("include")
    return ResponsesRequest(
        model=model,
        system="\n\n".join(s for s in [*system, *history.system] if s) or None,
        messages=tuple(history.messages),
        tools=tools,
        stream=request.get("stream") is True,
        reasoning_effort=effort if isinstance(effort, str) and effort else None,
        max_output_tokens=_positive(request.get("max_output_tokens"), "max_output_tokens"),
        instructions=instructions,
        wire_tools=table,
        tools_echo=echo,
        reasoning_items=history.reasoning_items,
        display_reasoning=history.display_reasoning,
        encrypted_reasoning=isinstance(include, list) and "reasoning.encrypted_content" in include,
        dropped=tuple(
            sorted(n for n in request if n not in _READ and n not in STATE_PARAMETERS and n not in REFUSED_PARAMETERS)
        ),
        parallel_tool_calls=_flag(request.get("parallel_tool_calls"), "parallel_tool_calls"),
        cache_key=_cache_key(request.get("prompt_cache_key")),
    )


def _flag(value: object, what: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise InvalidResponsesRequest(f"{what} must be a boolean", param=what)


def _cache_key(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidResponsesRequest("prompt_cache_key must be a string", param="prompt_cache_key")
    return value


def responses_usage(usage: TokenUsage) -> Json:
    return {
        "input_tokens": usage.input_tokens,
        "input_tokens_details": {"cached_tokens": usage.cache_read_tokens},
        "output_tokens": usage.output_tokens,
        "output_tokens_details": {"reasoning_tokens": usage.reasoning_tokens},
        "total_tokens": usage.input_tokens + usage.output_tokens,
    }


def _same_json(a: str, b: str) -> bool:
    try:
        return a == b or json.loads(a) == json.loads(b)
    except ValueError:
        return False


def _reasoning_text(response: ModelResponse) -> str:
    return "".join(p.thinking for p in response.message.content if isinstance(p, ThinkingPart))


@dataclass(slots=True)
class _OpenCall:
    key: int
    output_index: int | None = None
    item_id: str = ""
    call_id: str | None = None
    name: str | None = None
    pending: str = ""
    arguments: str = ""
    closed: bool = False


@dataclass(slots=True)
class _OpenText:
    output_index: int
    item_id: str
    text: str = ""


@dataclass(slots=True)
class ResponseEncoder:
    """One response's events. An item is opened (``output_item.added``) when its first
    delta arrives and closed (``output_item.done``, the whole item) before the next kind
    starts or at the end; a function call opens once its id and name are known. What a
    transport reports only on its final response is sent whole, then ``response.completed``
    (``response.incomplete`` when the output was cut) carries every item and the usage."""

    request: ResponsesRequest
    call: WireCall
    _seq: int = 0
    _items: dict[int, Json] = field(default_factory=dict[int, "Json"])
    _next_index: int = 0
    _text: _OpenText | None = None
    _reasoning: _OpenText | None = None
    _calls: dict[int, _OpenCall] = field(default_factory=dict[int, _OpenCall])
    _open: _OpenCall | None = None
    _streamed_text: bool = False
    _streamed_reasoning: bool = False

    def _event(self, kind: str, **fields: object) -> Json:
        event: Json = {"type": kind, "sequence_number": self._seq, **fields}
        self._seq += 1
        return event

    def _item_id(self, prefix: str, index: int) -> str:
        return f"{prefix}_{self.call.response_id.removeprefix('resp_')}_{index}"

    def _open_index(self) -> int:
        index, self._next_index = self._next_index, self._next_index + 1
        return index

    def response_object(self, status: str, *, usage: Json | None = None, error: Json | None = None,
                        incomplete: Json | None = None) -> Json:
        return {
            "id": self.call.response_id,
            "object": "response",
            "created_at": self.call.created,
            "status": status,
            "background": False,
            "error": error,
            "incomplete_details": incomplete,
            "instructions": self.request.instructions,
            "max_output_tokens": self.request.max_output_tokens,
            "model": self.call.model_id,
            "output": [self._items[i] for i in sorted(self._items)],
            "parallel_tool_calls": self.request.parallel_tool_calls is not False,
            "previous_response_id": None,
            "reasoning": {"effort": self.request.reasoning_effort, "summary": None},
            "store": False,
            "temperature": None,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": list(self.request.tools_echo),
            "top_p": None,
            "truncation": "disabled",
            "usage": usage,
            "user": None,
            "metadata": {},
        }

    def start(self) -> list[Json]:
        return [
            self._event("response.created", response=self.response_object("in_progress")),
            self._event("response.in_progress", response=self.response_object("in_progress")),
        ]

    def on_event(self, event: StreamEvent) -> list[Json]:
        match event:
            case TextDeltaEvent(delta=text) if text:
                self._streamed_text = True
                return self._text_delta(text)
            case ThinkingDeltaEvent(delta=text) if text:
                self._streamed_reasoning = True
                return self._reasoning_delta(text)
            case ToolCallDeltaEvent():
                return self._tool_fragment(event)
            case StreamCompleteEvent(response=response):
                return self.finish(response)
            case _:
                return []


    def _message_item(self, opened: _OpenText, status: str) -> Json:
        content: list[Json] = (
            [{"type": "output_text", "text": opened.text, "annotations": []}] if status == "completed" else []
        )
        return {"id": opened.item_id, "type": "message", "status": status, "role": "assistant", "content": content}

    def _text_delta(self, text: str) -> list[Json]:
        events: list[Json] = []
        if self._text is None:
            events += self._close_current()
            index = self._open_index()
            self._text = _OpenText(index, self._item_id("msg", index))
            events += [
                self._event("response.output_item.added", output_index=index,
                            item=self._message_item(self._text, "in_progress")),
                self._event("response.content_part.added", item_id=self._text.item_id, output_index=index,
                            content_index=0, part={"type": "output_text", "text": "", "annotations": []}),
            ]
        self._text.text += text
        events.append(self._event("response.output_text.delta", item_id=self._text.item_id,
                                  output_index=self._text.output_index, content_index=0, delta=text, logprobs=[]))
        return events

    def _close_text(self) -> list[Json]:
        opened, self._text = self._text, None
        if opened is None:
            return []
        item = self._message_item(opened, "completed")
        self._items[opened.output_index] = item
        part: Json = {"type": "output_text", "text": opened.text, "annotations": []}
        return [
            self._event("response.output_text.done", item_id=opened.item_id, output_index=opened.output_index,
                        content_index=0, text=opened.text, logprobs=[]),
            self._event("response.content_part.done", item_id=opened.item_id, output_index=opened.output_index,
                        content_index=0, part=part),
            self._event("response.output_item.done", output_index=opened.output_index, item=item),
        ]


    def _reasoning_item(self, opened: _OpenText, *, done: bool) -> Json:
        summary: list[Json] = [{"type": "summary_text", "text": opened.text}] if done else []
        return {"id": opened.item_id, "type": "reasoning", "summary": summary}

    def _reasoning_delta(self, text: str) -> list[Json]:
        events: list[Json] = []
        if self._reasoning is None:
            events += self._close_current()
            index = self._open_index()
            self._reasoning = _OpenText(index, self._item_id("rs", index))
            events += [
                self._event("response.output_item.added", output_index=index,
                            item=self._reasoning_item(self._reasoning, done=False)),
                self._event("response.reasoning_summary_part.added", item_id=self._reasoning.item_id,
                            output_index=index, summary_index=0, part={"type": "summary_text", "text": ""}),
            ]
        self._reasoning.text += text
        events.append(self._event("response.reasoning_summary_text.delta", item_id=self._reasoning.item_id,
                                  output_index=self._reasoning.output_index, summary_index=0, delta=text))
        return events

    def _close_reasoning(self) -> list[Json]:
        opened, self._reasoning = self._reasoning, None
        if opened is None:
            return []
        item = self._reasoning_item(opened, done=True)
        self._items[opened.output_index] = item
        return [
            self._event("response.reasoning_summary_text.done", item_id=opened.item_id,
                        output_index=opened.output_index, summary_index=0, text=opened.text),
            self._event("response.reasoning_summary_part.done", item_id=opened.item_id,
                        output_index=opened.output_index, summary_index=0,
                        part={"type": "summary_text", "text": opened.text}),
            self._event("response.output_item.done", output_index=opened.output_index, item=item),
        ]

    def _call_item(self, call: _OpenCall, status: str) -> Json:
        wire = self.request.wire_tool(call.name or "")
        item: Json = {
            "id": call.item_id,
            "type": "function_call",
            "status": status,
            "arguments": call.arguments if status == "completed" else "",
            "call_id": call.call_id,
            "name": wire.name,
        }
        if wire.namespace:
            item["namespace"] = wire.namespace
        return item

    def _open_call(self, call: _OpenCall) -> list[Json]:
        events = self._close_current()
        call.output_index = index = self._open_index()
        call.item_id = self._item_id("fc", index)
        self._open = call
        events.append(self._event("response.output_item.added", output_index=index,
                                  item=self._call_item(call, "in_progress")))
        return events

    def _arguments(self, call: _OpenCall, delta: str) -> Json:
        call.arguments += delta
        return self._event("response.function_call_arguments.delta", item_id=call.item_id,
                           output_index=call.output_index, delta=delta)

    def _tool_fragment(self, event: ToolCallDeltaEvent) -> list[Json]:
        call = self._calls.get(event.index)
        if call is None:
            call = self._calls[event.index] = _OpenCall(key=event.index)
        call.call_id = call.call_id or event.id
        call.name = call.name or event.name
        call.pending += event.arguments_delta
        events: list[Json] = []
        if call.output_index is None and call.call_id and call.name:
            events += self._open_call(call)
        if call is self._open and call.pending:
            delta, call.pending = call.pending, ""
            events.append(self._arguments(call, delta))
        return events

    def _close_call(self, call: _OpenCall, final: str | None) -> list[Json]:
        """``final``: the arguments on the final response, which win over what was streamed
        unless both are the same JSON (the model's own text is kept then)."""
        events: list[Json] = []
        if call.output_index is None:
            events += self._open_call(call)
        if final is not None and not _same_json(final, call.arguments):
            if call.arguments and final.startswith(call.arguments):
                events.append(self._arguments(call, final[len(call.arguments):]))
            elif not call.arguments:
                events.append(self._arguments(call, final))
            else:
                call.arguments = final
        if self._open is call:
            self._open = None
        call.closed = True
        item = self._call_item(call, "completed")
        assert call.output_index is not None
        self._items[call.output_index] = item
        events += [
            self._event("response.function_call_arguments.done", item_id=call.item_id,
                        output_index=call.output_index, name=item["name"], arguments=call.arguments),
            self._event("response.output_item.done", output_index=call.output_index, item=item),
        ]
        return events

    def _close_current(self) -> list[Json]:
        """Items go out one after another, as OpenAI streams them: whatever is open closes
        before another item opens (a call with what streamed of it so far)."""
        events = [*self._close_reasoning(), *self._close_text()]
        if self._open is not None:
            events += self._close_call(self._open, None)
        return events

    def _reconcile(self, final: Sequence[ToolCall]) -> list[Json]:
        """The final response is the truth about the calls; streamed fragments are matched to it
        by id, then by name, and a call it alone reports is sent whole."""
        streamed = [self._calls[k] for k in sorted(self._calls)]
        matched: set[int] = set()
        events: list[Json] = []
        for position, tool_call in enumerate(final):
            mine = next((s for s in streamed if s.key not in matched and s.call_id == tool_call.id), None)
            if mine is None:
                mine = next((s for s in streamed if s.key not in matched and s.name == tool_call.name), None)
            if mine is None:
                mine = _OpenCall(key=-1 - position)
            else:
                matched.add(mine.key)
            if mine.closed:
                continue
            mine.call_id = mine.call_id or tool_call.id
            mine.name = mine.name or tool_call.name
            events += self._close_call(mine, tool_arguments(tool_call))
        if self._open is not None:
            events += self._close_call(self._open, None)
        return events

    def _state_items(self, blocks: list[dict[str, object]]) -> list[Json]:
        """The provider's reasoning items, each sent whole, its encrypted content sealed to the call's org and route.
        A summary that already streamed as its own item is not repeated."""
        events = self._close_current()
        for block in blocks:
            index = self._open_index()
            encrypted = block.get("encrypted_content")
            item: Json = {
                "id": str(block.get("id") or self._item_id("rs", index)),
                "type": "reasoning",
                "summary": [] if self._streamed_reasoning else block.get("summary") or [],
            }
            if isinstance(encrypted, str) and encrypted:
                item["encrypted_content"] = seal(encrypted, self.call.state_tag)
            self._items[index] = item
            events += [
                self._event("response.output_item.added", output_index=index, item={**item, "summary": []}),
                self._event("response.output_item.done", output_index=index, item=item),
            ]
        return events

    def finish(self, response: ModelResponse) -> list[Json]:
        events: list[Json] = []
        blocks = (
            state_blocks(response.message, OPENAI_RESPONSES)
            if self.request.encrypted_reasoning and self.call.state_tag
            else []
        )
        # The provider's own order (reasoning first) when nothing has gone out yet; after the turn's items once a
        # streamed item is open, which must close with what the final response says first.
        lead = bool(blocks) and self._next_index == 0
        if lead:
            events += self._state_items(blocks)
        reasoning = _reasoning_text(response)
        if reasoning and not self._streamed_reasoning and not blocks:
            events += self._reasoning_delta(reasoning)
        text = response.message.text
        if text and not self._streamed_text:
            events += self._text_delta(text)
        events += self._close_reasoning()
        events += self._close_text()
        events += self._reconcile(response.message.tool_calls or ())
        if blocks and not lead:
            events += self._state_items(blocks)
        usage = responses_usage(response.usage)
        if response.stop_reason == StopReason.MAX_TOKENS:
            body = self.response_object("incomplete", usage=usage, incomplete={"reason": "max_output_tokens"})
            events.append(self._event("response.incomplete", response=body))
        else:
            events.append(self._event("response.completed", response=self.response_object("completed", usage=usage)))
        return events

    def failed(self, code: str, message: str) -> list[Json]:
        error: Json = {"code": OPENAI_FAILURE_CODES.get(code, code), "message": message}
        body = self.response_object("failed", error=error)
        return [self._event("response.failed", response=body)]


class ResponsesWire:
    """OpenAI Responses (Codex): typed ``event:``/``data:`` frames, no ``[DONE]``."""

    api = ModelApi.RESPONSES
    id_prefix = "resp"
    terminal = b""
    # An SSE comment: Codex's reader and the OpenAI SDKs skip it.
    keepalive = b": keepalive\n\n"
    provider_state = OPENAI_RESPONSES

    def parse(self, body: object, headers: Mapping[str, str] | None = None) -> ResponsesRequest:
        return parse_responses_request(body)

    def body(self, request: ResponsesRequest, response: ModelResponse, call: WireCall) -> Json:
        events = ResponseEncoder(request, call).finish(response)
        final = events[-1]["response"]
        assert isinstance(final, dict)
        return cast("Json", final)

    def encoder(self, request: ResponsesRequest, call: WireCall) -> ResponseEncoder:
        return ResponseEncoder(request, call)

    def frame(self, event: Json) -> bytes:
        return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()

    def error(
        self, status: int, code: str, message: str, *, param: str | None = None, provider: bool = False
    ) -> tuple[Json, dict[str, str]]:
        # Codex keeps only the status and the body's message of a refused call: the code travels in the message so
        # the run stops for the gateway's reason, not "http_403" (RL-3 on the real Codex lane), as Claude Code's does.
        tagged = f"{message} [gateway:{code}]" if _GATEWAY_CODE.match(code) else message
        return openai_error(status, code, tagged, param=param, provider=provider)


RESPONSES_WIRE = ResponsesWire()


__all__ = [
    "ACCEPTED_INCLUDES",
    "DROPPED_PARAMETERS",
    "NAMESPACE_SEPARATOR",
    "OPENAI_FAILURE_CODES",
    "REFUSED_PARAMETERS",
    "RESPONSES_WIRE",
    "STATE_PARAMETERS",
    "ResponseEncoder",
    "ResponsesRequest",
    "ResponsesWire",
    "WireTool",
    "parse_responses_request",
    "responses_usage",
]
