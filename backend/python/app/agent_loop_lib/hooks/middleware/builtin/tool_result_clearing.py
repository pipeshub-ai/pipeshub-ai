from __future__ import annotations

import json
from typing import Any

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_TOOL_REF_PREFIX = "tool: "

_LEGACY_CLEARED_PREFIX = "[cleared"


def _is_already_compact(content: str) -> bool:
    return content.startswith(_TOOL_REF_PREFIX) or content.startswith(_LEGACY_CLEARED_PREFIX)


def _build_tool_ref(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_call_id: str,
    content: str,
    is_error: bool = False,
) -> str:
    """Build a metadata-only reference for a compacted tool result.

    Format mirrors ``_compact_reference`` (artifact_compaction.py) so the
    LLM can decide whether to re-call the tool without an extra
    round-trip.  No ``[cleared]`` label — just the facts."""
    lines: list[str] = []
    lines.append(f"tool: {tool_name or 'unknown'}")
    if tool_args:
        args_str = json.dumps(tool_args, default=str)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        lines.append(f"args: {args_str}")
    if tool_call_id:
        lines.append(f"tool_call_id: {tool_call_id}")
    preview = content[:200].strip()
    if preview:
        if len(content) > 200:
            preview += "..."
        label = "error" if is_error else "summary"
        lines.append(f"{label}: {preview}")
    if tool_name:
        lines.append(
            f"hint: call {tool_name} again with the same arguments if you need this data"
        )
    return "\n".join(lines)


def shape_tool_result_clearing(
    keep_last_n_turns: int = 3,
    trigger_ratio: float = 0.5,
    protected_tool_names: frozenset[str] | None = None,
):
    """Layer 3 context shaper: replaces stale TOOL message payloads once
    they age past ``keep_last_n_turns``.

    **Turn-based counting** — a "turn" is one ``AssistantMessage`` and all
    ``ToolMessage``s whose ``tool_call_id`` matches one of its
    ``tool_calls``.  Five parallel tool calls in the same assistant turn
    are ONE logical unit; clearing 2 of 5 from the same turn loses
    coherent context the model needs to cross-reference.

    **Artifact-aware** — ``ToolMessage``s that carry ``artifact_meta``
    are replaced with a compact reference (artifact ID, summary, schema,
    retrieval hint).  Non-artifact results get a metadata-only reference
    (tool name, args, summary, hint) so the model retains enough context
    to decide whether to re-call.

    Only fires when context exceeds ``trigger_ratio × budget``.
    ``protected_tool_names`` prevents clearing regardless of age.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        from app.agent_loop_lib.core.tokens import count_tokens

        messages = ctx.messages
        if count_tokens(messages) <= ctx.budget.max_tokens * trigger_ratio:
            await next_fn()
            return

        call_id_to_turn: dict[str, int] = {}
        call_id_to_name: dict[str, str] = {}
        call_id_to_args: dict[str, dict[str, Any]] = {}
        turns: list[set[int]] = []

        for msg in messages:
            if msg.role == MessageRole.ASSISTANT and getattr(msg, "tool_calls", None):
                turn_idx = len(turns)
                turns.append(set())
                for tc in msg.tool_calls:
                    call_id_to_turn[tc.id] = turn_idx
                    call_id_to_name[tc.id] = tc.name
                    call_id_to_args[tc.id] = getattr(tc, "arguments", {}) or {}

        for i, msg in enumerate(messages):
            if msg.role != MessageRole.TOOL:
                continue
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id and tc_id in call_id_to_turn:
                turns[call_id_to_turn[tc_id]].add(i)
            else:
                turns.append({i})

        turns = [t for t in turns if t]
        turns.sort(key=lambda indices: min(indices))

        if len(turns) <= keep_last_n_turns:
            await next_fn()
            return

        clearable: set[int] = set()
        for turn_indices in turns[: len(turns) - keep_last_n_turns]:
            clearable.update(turn_indices)

        if protected_tool_names and clearable:
            protected_indices = set()
            for i in clearable:
                tc_id = getattr(messages[i], "tool_call_id", None)
                if tc_id and call_id_to_name.get(tc_id) in protected_tool_names:
                    protected_indices.add(i)
            clearable -= protected_indices

        if not clearable:
            await next_fn()
            return

        from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
            _compact_reference,
        )

        shaped = []
        for i, msg in enumerate(messages):
            if i not in clearable:
                shaped.append(msg)
                continue
            if not isinstance(msg.content, str) or _is_already_compact(msg.content):
                shaped.append(msg)
                continue
            if getattr(msg, "artifact_meta", None) is not None:
                shaped.append(msg.model_copy(update={"content": _compact_reference(msg)}))
            else:
                tc_id = getattr(msg, "tool_call_id", None)
                tool_name = call_id_to_name.get(tc_id, "") if tc_id else ""
                tool_args = call_id_to_args.get(tc_id, {}) if tc_id else {}
                ref = _build_tool_ref(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_call_id=tc_id or "",
                    content=msg.content,
                    is_error=getattr(msg, "is_error", False),
                )
                shaped.append(msg.model_copy(update={"content": ref}))
        ctx.messages = shaped
        await next_fn()

    return _middleware
