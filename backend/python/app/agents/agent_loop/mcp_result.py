"""Converts a raw MCP `CallToolResult` into the agent loop's `ToolOutput`.

The result is read straight off the protocol object — `structuredContent` verbatim,
content blocks by type — and never passed through `clean_tool_result`: that helper
strips keys that are noise in Jira/Confluence payloads (`request`, `response`,
`history`, `properties`, anything starting with `_`), and for an arbitrary MCP server
those are routinely the fields the answer depends on.

Images follow the same admission path as `knowledge_graph/ops/fetch.py`, so the
request-wide image cap, dedupe and downscaling apply to MCP images too.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agent_loop_lib.tools.base import ToolOutput

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.agents.agent_loop.context import AgentContext

__all__ = ["mcp_result_to_tool_output"]

_EMPTY_ERROR_MESSAGE = "MCP tool returned an error without a message."
# Characters of tool output kept per result. The model sees at most `shape_budget_reduction`'s
# share of it anyway; this stops one huge result being held, logged and stored in full.
MAX_RESULT_CHARS = 200_000
_TEXT_ONLY_MODEL_NOTE = "[image returned by the tool not shown: this model does not accept images]"


def _capped(text: str) -> str:
    """`text` within `MAX_RESULT_CHARS`, keeping both ends: a result's head says what it is and
    its tail often says what to do next (more pages, a cursor)."""
    if len(text) <= MAX_RESULT_CHARS:
        return text
    note = f"\n\n[MCP tool output cut: {len(text) - MAX_RESULT_CHARS:,} of {len(text):,} characters not shown]\n\n"
    keep = MAX_RESULT_CHARS - len(note)
    head = int(keep * 0.8)
    return text[:head] + note + text[len(text) - (keep - head):]


def _field(obj: Any, protocol_name: str, snake_name: str) -> Any:  # noqa: ANN401
    """Protocol field, falling back to the snake_case name the mcp 2.x types use."""
    value = getattr(obj, protocol_name, None)
    return value if value is not None else getattr(obj, snake_name, None)


def _to_json(value: Any) -> str:  # noqa: ANN401
    return json.dumps(value, ensure_ascii=False, default=str)


def _is_serialization_of(text: str, value: Any) -> bool:  # noqa: ANN401
    try:
        return json.loads(text) == value
    except (ValueError, TypeError):
        return False


def _unwrap_fastmcp_result(result: Any, structured: Any) -> Any:  # noqa: ANN401
    """A fastmcp server returning a primitive sends `{"result": value}` and flags it in
    `_meta.fastmcp.wrap_result`; any other `{"result": ...}` dict is the tool's own shape."""
    meta = getattr(result, "meta", None)
    fastmcp_meta = meta.get("fastmcp") if isinstance(meta, dict) else None
    if (
        isinstance(fastmcp_meta, dict)
        and fastmcp_meta.get("wrap_result")
        and isinstance(structured, dict)
        and set(structured) == {"result"}
    ):
        return structured["result"]
    return structured


def _image_data_uri(mime_type: str | None, data: str | None) -> str | None:
    if not data:
        return None
    return f"data:{mime_type or 'image/png'};base64,{data}"


def _collect_embedded_resource(resource: Any, texts: list[str], image_uris: list[str]) -> None:  # noqa: ANN401
    uri = getattr(resource, "uri", "") or ""
    mime_type = _field(resource, "mimeType", "mime_type")
    text = getattr(resource, "text", None)
    if text is not None:
        texts.append(f"[resource {uri}]\n{text}")
        return
    blob = getattr(resource, "blob", None)
    if mime_type and mime_type.startswith("image/") and (image_uri := _image_data_uri(mime_type, blob)):
        image_uris.append(image_uri)
        return
    texts.append(f"[binary resource {uri} ({mime_type or 'unknown type'}) not shown]")


def _collect_blocks(content: Any) -> tuple[list[str], list[str]]:  # noqa: ANN401
    """Text and image data URIs from `content`, in order. Blocks the loop cannot carry
    (audio, binary resources, unknown types) become a short note, never base64."""
    texts: list[str] = []
    image_uris: list[str] = []
    for block in content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            if text := getattr(block, "text", None):
                texts.append(text)
        elif block_type == "image":
            if image_uri := _image_data_uri(_field(block, "mimeType", "mime_type"), getattr(block, "data", None)):
                image_uris.append(image_uri)
        elif block_type == "resource":
            _collect_embedded_resource(getattr(block, "resource", None), texts, image_uris)
        elif block_type == "resource_link":
            link = f"<{getattr(block, 'uri', '')}>"
            if name := getattr(block, "name", None):
                link = f"{name} {link}"
            texts.append(f"[resource link: {link}]")
        elif block_type == "audio":
            texts.append(f"[audio returned by the tool ({_field(block, 'mimeType', 'mime_type') or 'unknown type'}) not shown]")
        else:
            texts.append(f"[unsupported MCP content block: {block_type}]")
    return texts, image_uris


async def _image_parts(image_uris: list[str], context: "AgentContext | None") -> list["Part"]:
    """One part per image, in order: an `ImagePart` when admitted, a text note otherwise."""
    if context is None or not context.is_multimodal_llm:
        return [TextPart(text=_TEXT_ONLY_MODEL_NOTE) for _ in image_uris]

    from app.utils.chat_helpers import image_dict_to_part
    from app.utils.image_admission import (
        ImageCandidate,
        ImageOrigin,
        admission_from_state,
    )

    admission = admission_from_state(context.tool_state)
    await admission.warm(image_uris)
    verdict = admission.admit([
        ImageCandidate(ref=str(index), data_uri=uri, origin=ImageOrigin.FETCHED_RECORD, block_index=index)
        for index, uri in enumerate(image_uris)
    ])

    parts: dict[int, "Part"] = {}
    admitted_uris: list[str] = []
    for candidate in verdict.admitted:
        part = image_dict_to_part({"image_url": {"url": candidate.data_uri}})
        if part is None:
            parts[int(candidate.ref)] = TextPart(text="[image returned by the tool not shown: unreadable image data]")
            continue
        parts[int(candidate.ref)] = part
        admitted_uris.append(candidate.data_uri)
    for degraded in verdict.degraded:
        parts[int(degraded.candidate.ref)] = TextPart(
            text=f"[image returned by the tool not shown: {degraded.reason.value.replace('_', ' ')}]",
        )

    # Providers that reject images inside a tool result get them re-delivered through
    # `shape_retrieved_image_injection`, same as the knowledge fetch tool.
    if admitted_uris and not context.tool_state.get("supports_multipart_tool_result", True):
        context.tool_state.setdefault("pending_tool_images", []).extend(
            {"image_url": {"url": uri}} for uri in admitted_uris
        )
    return [parts[index] for index in sorted(parts)]


async def mcp_result_to_tool_output(result: Any, context: "AgentContext | None" = None) -> ToolOutput:  # noqa: ANN401
    raw_structured = _field(result, "structuredContent", "structured_content")
    structured = _unwrap_fastmcp_result(result, raw_structured)
    texts, image_uris = _collect_blocks(getattr(result, "content", None))
    if raw_structured is not None:
        # The spec asks servers to mirror structured content as a JSON text block for
        # older clients; carrying both would send the model the same payload twice.
        texts = [
            text for text in texts
            if not (_is_serialization_of(text, raw_structured) or _is_serialization_of(text, structured))
        ]

    if _field(result, "isError", "is_error"):
        message = "\n".join(texts) or (_to_json(structured) if structured is not None else "")
        return ToolOutput(success=False, error=_capped(message) or _EMPTY_ERROR_MESSAGE)

    if structured is not None and not texts and not image_uris:
        serialized = _to_json(structured)
        # Kept as data when it fits, so the loop still sees the object itself.
        return ToolOutput(success=True, data=structured if len(serialized) <= MAX_RESULT_CHARS else _capped(serialized))

    text = "\n".join(texts)
    if structured is not None:
        text = f"{text}\n\n{_to_json(structured)}" if text else _to_json(structured)
    text = _capped(text)
    if not image_uris:
        return ToolOutput(success=True, data=text)

    image_parts = await _image_parts(image_uris, context)
    if not any(isinstance(part, ImagePart) for part in image_parts):
        notes = "\n".join(part.text for part in image_parts if isinstance(part, TextPart))
        return ToolOutput(success=True, data=f"{text}\n{notes}" if text else notes)
    return ToolOutput(success=True, data=[*([TextPart(text=text)] if text else []), *image_parts])
