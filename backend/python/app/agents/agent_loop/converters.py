"""Bidirectional conversion between agent-loop's provider-agnostic message /
tool-schema types and LangChain's `BaseMessage` / `StructuredTool` types.

Used exclusively by `LangChainTransport` (`langchain_transport.py`) — no
other module should need to reach into LangChain message shapes directly,
keeping the rest of the adapter layer decoupled from LangChain's wire
format the same way `AnthropicTransport`/`OpenAITransport` keep their SDKs
from leaking past `transport/`.

Tool-name note: `ToolSchema.name` values reaching this module are already
LLM-safe (agent-loop's `PipesHubToolAdapter.name`, Phase 3, joins
`{app_name}_{tool_name}` with underscores) — unlike PipesHub's legacy
LangGraph path, no dot-to-underscore sanitization is needed here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    MessageRole,
    TextPart,
    ThinkingPart,
    ToolCall,
)
from app.agent_loop_lib.core.responses import TokenUsage

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Message, Part
    from app.agent_loop_lib.core.tool_schema import ToolSchema

# response_metadata keys different LangChain chat model integrations use to
# signal the completion was cut off at the output-token cap.
_TRUNCATION_FINISH_REASONS = {"length", "max_tokens"}


def _image_part_to_block(part: ImagePart) -> dict[str, Any]:
    source = part.source
    if source.type == "url":
        url = source.data
    else:
        media_type = source.media_type or "image/png"
        url = f"data:{media_type};base64,{source.data}"
    return {"type": "image_url", "image_url": {"url": url}}


def _part_to_block(part: Part) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return _image_part_to_block(part)
    if isinstance(part, ThinkingPart):
        # Thinking content should never be replayed as ordinary text to a
        # provider — same rule AnthropicTransport follows internally — but a
        # user-authored ThinkingPart is not a real scenario, so a plain-text
        # fallback here is defensive rather than load-bearing.
        return {"type": "text", "text": part.thinking}
    return {"type": "text", "text": str(part)}


def convert_message_to_langchain(message: Message) -> BaseMessage:
    """Convert one agent-loop `Message` to its LangChain equivalent."""
    if message.role == MessageRole.SYSTEM:
        return SystemMessage(content=message.content)

    if message.role == MessageRole.USER:
        content = message.content
        if isinstance(content, str):
            return HumanMessage(content=content)
        return HumanMessage(content=[_part_to_block(p) for p in content])

    if message.role == MessageRole.ASSISTANT:
        tool_calls = [
            {"name": tc.name, "args": tc.arguments, "id": tc.id}
            for tc in (message.tool_calls or [])
        ]
        # Replaying thinking content back to the provider on the next turn
        # is neither required nor universally supported — only the text is
        # resent, matching AnthropicTransport's own outgoing formatting.
        return AIMessage(content=message.text, tool_calls=tool_calls)

    if message.role == MessageRole.TOOL:
        return LCToolMessage(
            content=message.content,
            tool_call_id=message.tool_call_id or "",
            status="error" if message.is_error else "success",
        )

    raise ValueError(f"Unsupported agent-loop message role: {message.role!r}")


def convert_messages_to_langchain(
    messages: list[Message], system: str | None = None
) -> list[BaseMessage]:
    """Convert a full agent-loop message list, prepending `system` as a
    LangChain `SystemMessage` when provided (mirrors `LLMTransport.complete`'s
    contract: `system` arrives as a separate kwarg, not inside `messages`)."""
    converted = [convert_message_to_langchain(m) for m in messages]
    if system:
        return [SystemMessage(content=system), *converted]
    return converted


def convert_tool_call_from_langchain(call: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=call.get("id") or "",
        name=call["name"],
        arguments=call.get("args") or {},
    )


def _is_truncated(ai_message: AIMessage) -> bool:
    metadata = ai_message.response_metadata or {}
    finish_reason = metadata.get("finish_reason") or metadata.get("stop_reason")
    return finish_reason in _TRUNCATION_FINISH_REASONS


def convert_assistant_message_from_langchain(ai_message: AIMessage) -> AssistantMessage:
    """Convert a LangChain `AIMessage` response into an agent-loop
    `AssistantMessage`, using LangChain's standardized `content_blocks`
    (text/reasoning/...) so this works uniformly across the ~18 providers
    PipesHub's `BaseChatModel` wrappers cover, rather than hand-parsing each
    provider's raw `content` shape."""
    parts: list[Part] = []
    for block in ai_message.content_blocks:
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                parts.append(TextPart(text=text))
        elif block_type == "reasoning":
            reasoning = block.get("reasoning", "")
            if reasoning:
                parts.append(ThinkingPart(thinking=reasoning))
        # Other block types (tool_call, image, ...) are intentionally
        # skipped here: tool calls are read from `ai_message.tool_calls`
        # below, and PipesHub's LLMs don't return image content blocks.

    tool_calls = [convert_tool_call_from_langchain(c) for c in (ai_message.tool_calls or [])]

    return AssistantMessage(
        content=parts,
        tool_calls=tool_calls or None,
        truncated=_is_truncated(ai_message),
    )


def token_usage_from_ai_message(ai_message: AIMessage) -> TokenUsage:
    """Best-effort `TokenUsage` extraction from `AIMessage.usage_metadata`.
    Not every provider integration populates every sub-field (or even
    `usage_metadata` itself), so every access defaults to 0 rather than
    raising — a missing usage field should never crash the turn loop."""
    usage = ai_message.usage_metadata
    if not usage:
        return TokenUsage()

    input_details = usage.get("input_token_details") or {}
    return TokenUsage(
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_read_tokens=input_details.get("cache_read", 0) or 0,
        cache_write_tokens=input_details.get("cache_creation", 0) or 0,
    )


# ---------------------------------------------------------------------------
# Tool schema conversion
# ---------------------------------------------------------------------------

_JSON_SCHEMA_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _json_schema_to_python_type(schema: dict[str, Any], model_name: str, field_name: str) -> Any:  # noqa: ANN401
    """Best-effort JSON-schema-fragment -> Python type mapping for the flat,
    hand-authored schemas agent-loop's planner/critic/intent modules pass to
    `complete_structured` (see `agent/goal.py::_GOAL_SCHEMA` for the shape:
    no `$ref`/`$defs`, at most one level of `array`/`object` nesting).
    Anything unrecognized degrades to `Any` rather than raising — a
    too-loose field type is far cheaper than failing to bind tools at all.
    """
    schema_type = schema.get("type")

    if schema_type in _JSON_SCHEMA_TYPE_MAP:
        return _JSON_SCHEMA_TYPE_MAP[schema_type]

    if schema_type == "array":
        item_schema = schema.get("items") or {}
        item_type = _json_schema_to_python_type(item_schema, model_name, f"{field_name}_item")
        return list[item_type]

    if schema_type == "object":
        nested_name = f"{model_name}_{field_name}".title().replace("_", "")
        return _json_schema_to_pydantic_model(nested_name, schema)

    if "enum" in schema:
        # Plain Python doesn't need a real Enum here — a Literal-free `Any`
        # keeps this helper simple; the value is still validated downstream
        # by the calling module's own post-processing, not by this schema.
        return Any

    return Any


def _json_schema_to_pydantic_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Dynamically build a Pydantic model from a JSON-schema `object`
    fragment, for LangChain integrations whose `with_structured_output()`
    doesn't accept a raw dict schema (see `LangChainTransport.complete_structured`'s
    docstring for when this fallback path is used)."""
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    fields: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        python_type = _json_schema_to_python_type(field_schema, name, field_name)
        description = field_schema.get("description", "")
        if field_name in required:
            fields[field_name] = (python_type, Field(description=description))
        else:
            fields[field_name] = (python_type | None, Field(default=None, description=description))

    return create_model(name, **fields)


def output_schema_to_pydantic_model(output_schema: dict[str, Any]) -> type[BaseModel]:
    """Public entry point for `LangChainTransport.complete_structured`'s
    dynamic-model fallback."""
    return _json_schema_to_pydantic_model("StructuredOutput", output_schema)


async def _unbound_tool_coroutine(**_kwargs: Any) -> Any:  # noqa: ANN401
    raise RuntimeError(
        "This LangChain tool object exists only to carry a schema for "
        "LLM function-calling (LangChainTransport.complete/stream). Actual "
        "execution happens through agent-loop's ToolExecutor calling the "
        "matching PipesHubToolAdapter, never through this StructuredTool."
    )


def convert_tool_schema_to_langchain(schema: ToolSchema) -> StructuredTool:
    args_model = _json_schema_to_pydantic_model(f"{schema.name}_Args", schema.input_schema)
    return StructuredTool.from_function(
        name=schema.name,
        description=schema.description,
        args_schema=args_model,
        coroutine=_unbound_tool_coroutine,
    )


def convert_tool_schemas_to_langchain(
    tools: list[ToolSchema] | None,
) -> list[StructuredTool]:
    if not tools:
        return []
    return [convert_tool_schema_to_langchain(t) for t in tools]


__all__ = [
    "convert_message_to_langchain",
    "convert_messages_to_langchain",
    "convert_tool_call_from_langchain",
    "convert_assistant_message_from_langchain",
    "token_usage_from_ai_message",
    "output_schema_to_pydantic_model",
    "convert_tool_schema_to_langchain",
    "convert_tool_schemas_to_langchain",
]
