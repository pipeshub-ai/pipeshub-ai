"""How a tool's result becomes a ``ToolMessage``'s content, shared by the loops."""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel

from harness_core.kernel.core.messages import ImagePart, Part, TextPart, ThinkingPart

# What the model reads for a call to a tool the run does not have, on both paths.
UNKNOWN_TOOL_ERROR_PREFIX = "Unknown tool:"

_PART_TYPES = (TextPart, ImagePart, ThinkingPart)


def content_to_json(content: Any) -> str:
    """Serialize tool-result content to a JSON string.

    Handles Pydantic models, dicts, lists, and other non-string types.
    """
    if isinstance(content, BaseModel):
        return content.model_dump_json()
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return str(content)


def tool_message_content(content: Any) -> str | list[Part]:
    """Normalize a `ToolResult.content` into `ToolMessage.content`.

    A tool that wants to surface images to the LLM (search/fetch tools
    returning IMAGE blocks) returns `ToolOutput(data=[TextPart(...),
    ImagePart(...), ...])`; that list of `Part` objects must pass through
    untouched so `ToolMessage` keeps them as real multipart content instead
    of collapsing them into an opaque JSON string. Everything else
    (str, dict, Pydantic model, plain list/scalar) keeps the pre-existing
    JSON-serialization behavior.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        items = cast("list[object]", content)
        if items and all(isinstance(p, _PART_TYPES) for p in items):
            return cast("list[Part]", items)
    return content_to_json(content)


__all__ = ["UNKNOWN_TOOL_ERROR_PREFIX", "content_to_json", "tool_message_content"]
