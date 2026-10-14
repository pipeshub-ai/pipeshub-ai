"""Pieces of a knowledge-search tool result, shared by every tool that returns one."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.agents.actions.util.tool_summaries import (
    as_text,
    bullet_list,
    parse_json_maybe,
)
from app.models.entities import RecordType
from app.utils.chat_helpers import image_dict_to_part

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.core.types import ToolResult

# Record types whose sub-items (or linked records) search can never surface —
# it returns matching blocks, not a record's children — so a hit on one is
# exactly when `knowledgegraph.navigate()` adds something search cannot.
_HIERARCHICAL_RECORD_TYPES = frozenset({
    RecordType.TICKET.value,
    RecordType.PROJECT.value,
    RecordType.CONFLUENCE_PAGE.value,
    RecordType.SHAREPOINT_LIST.value,
    RecordType.SHAREPOINT_DOCUMENT_LIBRARY.value,
})
NAVIGATE_TIP = (
    "\n\nTip: these are content excerpts. To see a record's sub-items (epic to "
    "stories to sub-tasks, page to child pages) or its linked records, call "
    'knowledgegraph.navigate(node_id="<Record ID>") — it returns structure and '
    "Record IDs, not content, so reading what any of those records SAY is "
    "still a fetch."
)


def _block_accumulation_key(entry: dict[str, Any]) -> str | None:
    """Stable dedup key for a retrieval block; None when key parts are missing."""
    virtual_record_id = entry.get("virtual_record_id")
    block_index = entry.get("block_index")
    if virtual_record_id is None or block_index is None:
        return None
    return f"{virtual_record_id}_{block_index}"


def dedupe_append_final_results(
    existing: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append ``new_blocks``, skipping duplicates by (virtual_record_id, block_index).

    Accumulated state of the wrong shape is dropped rather than failing every
    reader of it after this.
    """
    appended = [entry for entry in existing if isinstance(entry, dict)] if isinstance(existing, list) else []
    seen_keys = {
        key
        for entry in appended
        if (key := _block_accumulation_key(entry)) is not None
    }
    for entry in new_blocks:
        key = _block_accumulation_key(entry)
        if key is None or key not in seen_keys:
            if key is not None:
                seen_keys.add(key)
            appended.append(entry)
    return appended


def compose_result_tail(
    virtual_record_id_to_result: Mapping[str, Any], candidate_suffix: str,
) -> str:
    """Trailing guidance for a search result, in the order the model should read it.

    The navigate tip must come BEFORE the candidate list, never after. It names
    a different tool, so in the last position it reads as the answer to "how do
    I get more?" and beats the fetch call-to-action on exactly the records where
    both apply — tickets and Confluence pages.
    """
    has_hierarchy = any(
        isinstance(rec, dict) and rec.get("record_type") in _HIERARCHICAL_RECORD_TYPES
        for rec in virtual_record_id_to_result.values()
    )
    return (NAVIGATE_TIP if has_hierarchy else "") + candidate_suffix


def tool_output(
    text: str, images: list[dict[str, Any]], state: Mapping[str, Any] | dict[str, Any],
) -> str | list[Part]:
    """The tool's return value: plain text, or text plus images for a multimodal model.

    Transports that cannot carry images inside a tool result get them through
    ``pending_tool_images``, which ``shape_retrieved_image_injection`` delivers
    as a user message instead. The stash is filled only for those, since
    nothing else drains it.
    """
    if not images or not state.get("is_multimodal_llm"):
        return text
    if not state.get("supports_multipart_tool_result", True):
        state.setdefault("pending_tool_images", []).extend(images)
    from app.agent_loop_lib.core.messages import TextPart

    image_parts = [part for image in images if (part := image_dict_to_part(image)) is not None]
    return [TextPart(text=text), *image_parts]


def result_text(content: object) -> str | None:
    """The text of a tool result, whether a string or text and image parts."""
    if isinstance(content, list):
        texts = [
            text for part in content
            if isinstance(text := getattr(part, "text", None), str)
        ]
        return "\n".join(texts) if texts else None
    return as_text(content)


_RESULT_HEADER_RE = re.compile(r"^Top (\d+) blocks? from (\d+) records?", re.IGNORECASE | re.MULTILINE)
_RECORD_NAME_RE = re.compile(r"^Name\s*:\s*(.+)$", re.MULTILINE)


def search_result_summary(args: dict[str, Any], result: ToolResult) -> str | None:
    """One-line activity summary of a search result, plus the record names it showed."""
    text = result_text(result.content)
    if not text:
        return None
    parsed = parse_json_maybe(text)
    if isinstance(parsed, dict) and parsed.get("status") == "error":
        return f"Search failed: {parsed.get('message') or 'Unknown error'}"
    if isinstance(parsed, dict) and (
        parsed.get("result_count") == 0
        or (isinstance(parsed.get("results"), list) and not parsed["results"])
    ):
        return str(parsed.get("message") or "No results found")

    match = _RESULT_HEADER_RE.search(text)
    if not match:
        return None
    blocks, records = match.group(1), match.group(2)
    header = (
        f"Retrieved {blocks} block{'s' if blocks != '1' else ''} "
        f"from {records} record{'s' if records != '1' else ''}"
    )
    names = [name.strip() for name in _RECORD_NAME_RE.findall(text) if name.strip()]
    return header + "\n" + bullet_list(names) if names else header
