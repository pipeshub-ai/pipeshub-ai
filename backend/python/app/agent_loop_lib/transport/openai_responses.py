"""Shape helpers for OpenAI's Responses API.

The Responses API is a different wire shape from Chat Completions, not a
variation on it: messages become `input` items, tools are flat rather than
nested under `"function"`, usage fields are renamed, and there is no
`finish_reason` at all. Reasoning models on Azure require it -- Chat Completions
rejects reasoning combined with bound tools for the gpt-5.x family -- which is
why `aimodels._reasoning_effort_kwargs` returns `use_responses_api=True` for
them, and why a transport that only speaks Chat Completions silently ran
without reasoning.

Everything here is a pure function over plain data so it can be tested without
a client or a network, and so `OpenAITransport` keeps one place to look for
"what does the other API shape look like".

Names verified against the installed `openai` SDK (2.45.0).
"""

from __future__ import annotations

import json
from typing import Any

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    MessageRole,
    ToolCall,
)
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.tool_schema import ToolSchema

# Stream event `type` literals we act on. Everything else in the union -- and
# there are 52 -- is ignored rather than matched, so an event Azure adds later
# cannot crash the loop.
EVT_TEXT_DELTA = "response.output_text.delta"
EVT_REFUSAL_DELTA = "response.refusal.delta"
EVT_REASONING_DELTA = "response.reasoning_text.delta"
EVT_REASONING_SUMMARY_DELTA = "response.reasoning_summary_text.delta"
EVT_OUTPUT_ITEM_ADDED = "response.output_item.added"
EVT_FUNCTION_ARGS_DELTA = "response.function_call_arguments.delta"
EVT_COMPLETED = "response.completed"
EVT_INCOMPLETE = "response.incomplete"
EVT_FAILED = "response.failed"
EVT_ERROR = "error"


class RawEvent:
    """Attribute-style read-only view over a decoded SSE payload.

    Exists so the stream can skip the SDK's per-event pydantic validation. The
    SDK casts every event to `ResponseStreamEvent`, a 53-member discriminated
    union, which sends `construct_type` down its `is_union` branch and runs a
    full `TypeAdapter.validate_python` per event -- measured at 56us against
    1.7us for `json.loads` plus this wrapper, and ~12% of query-service CPU. We
    read four fields per event, so none of that validation is load-bearing.

    Attribute access, not a model: there is no `.model_dump()`, no field
    coercion and no defaults. Anything added to the stream loop that needs real
    SDK model behaviour has to decode the payload itself.

    Missing keys raise `AttributeError` rather than returning None, so
    `getattr(event, "output_index", 0)` still yields its default -- a
    None-returning shim would silently defeat every default at the call sites.
    Nested dicts and lists are wrapped on access, never eagerly: the terminal
    `response.completed` payload carries the whole Response, and walking it up
    front would hand back the saving.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(name) from None
        return _wrap(value)

    def __repr__(self) -> str:
        return f"RawEvent({self._data!r})"


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return RawEvent(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def _responses_content_blocks(parts: list[Any]) -> list[dict[str, Any]]:
    """`list[Part]` into Responses input blocks.

    The Responses API names these differently from Chat Completions --
    `input_text` / `input_image`, and `image_url` is a bare string rather than
    an object -- so the two cannot share one formatter.
    """
    from app.agent_loop_lib.core.messages import image_data_url

    blocks: list[dict[str, Any]] = []
    for part in parts:
        if getattr(part, "type", None) == "image" and getattr(part, "source", None):
            blocks.append({
                "type": "input_image",
                "image_url": image_data_url(part.source),
            })
            continue
        text = getattr(part, "text", "")
        if text:
            blocks.append({"type": "input_text", "text": text})
    return blocks


def format_responses_input(
    messages: list[Message], system: str | None
) -> list[dict[str, Any]]:
    """Our messages into Responses `input` items.

    The system prompt is emitted as a leading `system` item rather than via the
    separate `instructions` field, so the prompt prefix stays byte-identical to
    the one the LangChain arm sends and prompt-cache behaviour is comparable.

    A `function_call` must precede its matching `function_call_output`, which
    our message ordering already guarantees.
    """
    items: list[dict[str, Any]] = []
    if system:
        items.append({"type": "message", "role": "system", "content": system})

    for msg in messages:
        if msg.role == MessageRole.TOOL:
            items.append({
                "type": "function_call_output",
                "call_id": msg.tool_call_id,
                "output": (msg.content or "") + getattr(msg, "step_footer", ""),
            })
            continue

        if msg.role == MessageRole.ASSISTANT:
            text = msg.text
            if text:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": text, "annotations": []}
                    ],
                })
            for tc in msg.tool_calls or []:
                items.append({
                    "type": "function_call",
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                    "call_id": tc.id,
                })
            continue

        content = msg.content
        if isinstance(content, list):
            blocks = _responses_content_blocks(content)
            if any(b["type"] != "input_text" for b in blocks):
                items.append({
                    "type": "message", "role": msg.role.value, "content": blocks,
                })
                continue
            content = " ".join(b["text"] for b in blocks)
        items.append({
            "type": "message",
            "role": msg.role.value,
            "content": content or "",
        })
    return items


def format_responses_tools(tools: list[ToolSchema] | None) -> list[dict] | None:
    """Flat tool shape -- `{"type","name","description","parameters"}`.

    Not the Chat Completions form nested under `"function"`; sending that is
    what produces the `tool_choice.function` rejection already tracked in
    `provider_conflicts.API_SHAPE_CONFLICT_MARKERS`.

    `strict` is sent explicitly false: our schemas are not guaranteed to satisfy
    strict mode (which requires `additionalProperties: false` and no optional
    fields), and a gateway defaulting it to true would start rejecting tools.
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema or {"type": "object", "properties": {}},
            "strict": False,
        }
        for t in tools
    ]


def format_responses_tool_choice(name: str) -> dict[str, Any]:
    """Force one function. Flat, like the tool definitions."""
    return {"type": "function", "name": name}


def normalise_tool_call(call_id: str, name: str, raw_arguments: str | None) -> ToolCall:
    """One raw tool call into a `ToolCall`, repaired the way the LangChain path
    repairs its `invalid_tool_calls`.

    Silently substituting `{}` for unparseable arguments makes a malformed call
    look like a successful one with no arguments, so the tool runs with nothing
    instead of the loop being told to correct itself. `_recover_invalid_tool_call`
    repairs what it can and otherwise attaches the malformed-args sentinels the
    tool loop already knows how to turn into a corrective message. It also clamps
    the name, which must not reach history over-long -- history is re-sent every
    turn.

    Imported lazily: `converters` lives in the app layer and imports this
    package, so a module-level import would be circular. Falls back to a plain
    parse when the app layer is absent, keeping agent_loop_lib usable on its own.
    """
    try:
        from app.agents.agent_loop.converters import _recover_invalid_tool_call
    except Exception:
        try:
            args = json.loads(raw_arguments) if raw_arguments else {}
        except (json.JSONDecodeError, ValueError):
            args = {}
        return ToolCall(id=call_id, name=name, arguments=args if isinstance(args, dict) else {})

    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
        if isinstance(args, dict):
            return _recover_invalid_tool_call(
                {"id": call_id, "name": name, "args": json.dumps(args)}
            )
    except (json.JSONDecodeError, ValueError):
        pass
    return _recover_invalid_tool_call(
        {"id": call_id, "name": name, "args": raw_arguments or ""}
    )


def parse_responses_output(response: Any) -> AssistantMessage:
    """A `Response` into our `AssistantMessage`.

    Walks `output` explicitly rather than using the `output_text` convenience
    property, which has come and gone across SDK releases. `reasoning` items are
    skipped: we never request a reasoning summary, so they carry nothing.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "output_text":
                    text_parts.append(getattr(part, "text", "") or "")
        elif item_type == "function_call":
            tool_calls.append(
                normalise_tool_call(
                    # call_id is what a later function_call_output must echo;
                    # `id` is the separate item id and is not interchangeable.
                    getattr(item, "call_id", None) or getattr(item, "id", "") or "",
                    getattr(item, "name", "") or "",
                    getattr(item, "arguments", None),
                )
            )

    return AssistantMessage(
        content="".join(text_parts) or None,
        tool_calls=tool_calls or None,
    )


def responses_usage_fields(response: Any) -> tuple[int, int, int, int]:
    """(input, output, cache_read, cache_write) from a `Response`.

    Every field is named differently from Chat Completions -- `input_tokens`
    rather than `prompt_tokens`, and cached tokens hang off
    `input_tokens_details` rather than `prompt_tokens_details` -- so reusing the
    Chat Completions reader here would silently report zeros.
    """
    usage = getattr(response, "usage", None)

    def _int(obj: Any, name: str) -> int:
        value = getattr(obj, name, 0) if obj is not None else 0
        return value if isinstance(value, int) else 0

    details = getattr(usage, "input_tokens_details", None)
    return (
        _int(usage, "input_tokens"),
        _int(usage, "output_tokens"),
        _int(details, "cached_tokens"),
        _int(details, "cache_write_tokens"),
    )


def stop_reason_from_responses(response: Any, has_tool_calls: bool) -> StopReason:
    """Map a `Response` onto our stop reason.

    Truncation is checked before tool calls, matching
    `langchain_transport.py:484`: a response cut off mid-arguments leaves
    unparseable JSON, and reporting TOOL_USE would send the agent loop off to
    execute a garbage call instead of recovering from the truncation.
    """
    reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
    if reason == "max_output_tokens":
        return StopReason.MAX_TOKENS
    if has_tool_calls:
        return StopReason.TOOL_USE
    if reason or getattr(response, "status", None) == "incomplete":
        return StopReason.MAX_TOKENS
    return StopReason.END_TURN
