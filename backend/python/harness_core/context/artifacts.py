"""A large tool result's compact reference (legacy L2 artifact compaction, P4.5).

The loop registers a large result as an artifact and journals its ``artifact_meta``;
where the result no longer fits, the model sees this reference and can read the
artifact back with ``retrieve_artifact_content``. Legacy's PRE_MODEL shapers and the
compiler's cleared rendering share it, so both paths show the model the same text.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import ToolMessage

MAX_ARGS_CHARS = 200


def artifact_reference(msg: ToolMessage) -> str:
    meta = msg.artifact_meta
    if meta is None:
        return msg.text

    lines = [f"[artifact:{meta.artifact_id}]"]
    content_type = meta.tool_name.replace("__", ".") if meta.tool_name else "tool_result"
    lines.append(f"type: {content_type}")
    if meta.tool_name:
        lines.append(f"tool: {meta.tool_name}")
    if meta.tool_args:
        args_str = json.dumps(meta.tool_args, default=str)
        if len(args_str) > MAX_ARGS_CHARS:
            args_str = args_str[:MAX_ARGS_CHARS] + "..."
        lines.append(f"args: {args_str}")
    if msg.tool_call_id:
        lines.append(f"tool_call_id: {msg.tool_call_id}")
    if meta.summary:
        lines.append(f"summary: {meta.summary}")
    if meta.result_schema:
        lines.append(f"schema: {json.dumps(meta.result_schema)}")
    lines.append(f"original_tokens: {meta.original_token_count}")
    lines.append(
        f'hint: Use retrieve_artifact_content(artifact_id="{meta.artifact_id}") '
        "to read, filter, and curate this data before using it"
    )
    return "\n".join(lines)


def artifact_index(results: Sequence[ToolMessage]) -> str:
    """One line per artifact among ``results``, for a summary of the range they were in: the
    ids stay exact whatever the summariser wrote (legacy L4 keeps the same index)."""
    seen: dict[str, str] = {}
    for msg in results:
        meta = msg.artifact_meta
        if meta is not None and meta.artifact_id not in seen:
            seen[meta.artifact_id] = meta.summary
    if not seen:
        return ""
    lines = ["Artifacts from the summarised conversation (read one with retrieve_artifact_content):"]
    for artifact_id, summary in seen.items():
        label = f" {summary}" if summary else ""
        lines.append(f'- [artifact:{artifact_id}]{label} (retrieve_artifact_content(artifact_id="{artifact_id}"))')
    return "\n".join(lines)


__all__ = ["artifact_index", "artifact_reference"]
