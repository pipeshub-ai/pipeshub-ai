from __future__ import annotations

import json
import logging
from typing import Any

from app.agent_loop_lib.context.compaction_memo import (
    CompactionMemo,
    reapply_replacements,
)
from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

LAYER = "tool_result_clearing"
# Results these layers already replaced with a reference are not cleared again.
_REFERENCE_LAYERS = frozenset({LAYER, "artifact_compaction"})


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
    protected_tools: frozenset[str] = frozenset(),
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

    **Sticky** — a result cleared on an earlier call of the run is cleared
    again on every later call (``context/compaction_memo.py``), and new
    results are cleared only while the context is still over the trigger,
    so the sent prefix does not change on every call as one more turn ages
    out.

    Only fires when context exceeds ``budget.trigger_tokens(trigger_ratio)``.
    Results ``context/protection.py`` protects (tool groups that called one
    of ``protected_tools``) are never cleared, regardless of age.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        from app.agent_loop_lib.core.tokens import count_tokens

        initial_total = count_tokens(ctx.messages)
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        if initial_total <= trigger:
            await next_fn()
            return

        protected = protected_indices(ctx.messages, protected_tools=protected_tools)
        memo = ctx.compaction_memo
        messages, reused = reapply_replacements(memo, LAYER, ctx.messages, keep=protected)
        cleared = 0
        if count_tokens(messages) > trigger:
            messages, cleared = _clear_stale_turns(messages, keep_last_n_turns, protected, memo)

        if cleared or reused:
            logger.info(
                "tool_result_clearing: cleared %d tool result(s) older than the last %d turn(s) "
                "(%d again from earlier calls), %d→%d tokens (run=%s, turn=%d, budget=%d)",
                cleared, keep_last_n_turns, reused, initial_total, count_tokens(messages),
                ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
            )
        if memo is not None:
            memo.log_stats(LAYER, ctx.run_id, ctx.turn_index)
        ctx.messages = messages
        await next_fn()

    return _middleware


def _clear_stale_turns(
    messages: list,
    keep_last_n_turns: int,
    protected: frozenset[int],
    memo: CompactionMemo | None,
) -> tuple[list, int]:
    """Replace every unprotected result older than the last
    ``keep_last_n_turns`` tool turns with a reference; returns the list and
    how many results were replaced."""
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
        return messages, 0

    clearable = {
        i for turn_indices in turns[: len(turns) - keep_last_n_turns] for i in turn_indices
        if i not in protected and getattr(messages[i], "compacted_by", None) not in _REFERENCE_LAYERS
    }
    if not clearable:
        return messages, 0

    from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
        _compact_reference,
    )

    shaped = list(messages)
    for i in sorted(clearable):
        msg = messages[i]
        if getattr(msg, "artifact_meta", None) is not None:
            content = _compact_reference(msg)
        else:
            tc_id = getattr(msg, "tool_call_id", None)
            # Any images the tool returned belong to the current turn's
            # context; once a result ages past `keep_last_n_turns` its
            # images are as expendable as its text (same rule L4/L5/L7
            # already apply) — the reference is plain text so the model
            # can re-call the tool if it needs the image again.
            content = _build_tool_ref(
                tool_name=call_id_to_name.get(tc_id, "") if tc_id else "",
                tool_args=call_id_to_args.get(tc_id, {}) if tc_id else {},
                tool_call_id=tc_id or "",
                content=msg.text,
                is_error=getattr(msg, "is_error", False),
            )
        shaped[i] = msg.model_copy(update={"content": content, "compacted_by": LAYER})
        if memo is not None:
            memo.remember(LAYER, [msg], [shaped[i]])
    return shaped, len(clearable)
