"""Layer 6 PRE_MODEL shaper: deterministic rule-based compaction.

Replaces the LLM-backed ``auto_compact`` summarizer with a pure
extractive compaction that produces identical output for identical
input.  Zero LLM calls, zero non-determinism.

Rules:
- ``SystemMessage``: always kept verbatim (pinned).
- ``UserMessage`` (human-typed): kept verbatim (typically small).
- ``UserMessage`` written by another compaction layer (``compacted_by``,
  e.g. L4's loop summary): kept verbatim — it already is a summary.
- ``UserMessage`` (other injected): compacted to first 200 chars.
  Prevents recovery nudges from piling up in the middle zone.
- ``AssistantMessage`` (recent N): kept verbatim.
- ``AssistantMessage`` (older): first sentence + tool_call names/IDs.
- ``ToolMessage`` with ``artifact_meta``: compact reference only.
- ``ToolMessage`` without ``artifact_meta``: tool_call_id + first
  ``preview_chars`` characters.

A message compacted on an earlier call of the run is compacted again on
every later call (``context/compaction_memo.py``); newly aged messages are
compacted only while the context is still over the trigger, so the sent
prefix does not change on every call as the recent tail moves.
"""

from __future__ import annotations

import json
import logging

from app.agent_loop_lib.context.compaction_memo import (
    CompactionMemo,
    reapply_replacements,
)
from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens, extract_text
from app.agent_loop_lib.core.types import Message
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    safe_tail_boundary,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    _compact_reference,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

LAYER = "deterministic_compact"


def _first_sentence(text: str, max_chars: int = 300) -> str:
    """Extract the first sentence, capped at *max_chars*."""
    for sep in (". ", ".\n", "!\n", "?\n"):
        idx = text.find(sep)
        if 0 <= idx < max_chars:
            return text[: idx + 1]
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _compact_assistant(msg: AssistantMessage) -> AssistantMessage:
    text = extract_text(msg)
    summary = _first_sentence(text)

    tool_call_info = ""
    if msg.tool_calls:
        names = ", ".join(f"{tc.name}({tc.id})" for tc in msg.tool_calls)
        tool_call_info = f"\n[tool_calls: {names}]"

    compacted_text = f"[compacted] {summary}{tool_call_info}"
    return msg.model_copy(update={"content": [TextPart(text=compacted_text)], "compacted_by": LAYER})


def _compact_tool(
    msg: ToolMessage,
    preview_chars: int,
    call_id_to_name: dict[str, str] | None = None,
    call_id_to_args: dict[str, dict] | None = None,
) -> ToolMessage:
    if msg.artifact_meta is not None:
        return msg.model_copy(update={"content": _compact_reference(msg), "compacted_by": LAYER})

    tc_id = msg.tool_call_id or "unknown"
    tool_name = (call_id_to_name or {}).get(tc_id, "")
    tool_args = (call_id_to_args or {}).get(tc_id, {})

    lines = [f"[tool_call_id: {tc_id}]"]
    if tool_name:
        lines.append(f"tool: {tool_name}")
    if tool_args:
        args_str = json.dumps(tool_args, default=str)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        lines.append(f"args: {args_str}")
    # `.text` drops any image parts — old-turn images are expendable, same
    # rule as text (see shape_tool_result_clearing / synthesis_guard).
    text_content = msg.text
    preview = text_content[:preview_chars].strip()
    if len(text_content) > preview_chars:
        preview += "..."
    lines.append(f"summary: {preview}")
    return msg.model_copy(update={"content": "\n".join(lines), "compacted_by": LAYER})


def _compact_user(msg: UserMessage, max_chars: int = 200) -> UserMessage:
    """Shorten an injected UserMessage (recovery nudge, phase note).

    Human-typed messages are always kept verbatim — only programmatic
    injections (``injected=True``) are truncated, so nudges cannot pile up
    in the middle of the context.
    """
    text = msg.content if isinstance(msg.content, str) else extract_text(msg)
    if len(text) <= max_chars:
        return msg
    return msg.model_copy(update={"content": text[:max_chars] + "...", "compacted_by": LAYER})


def shape_deterministic_compact(
    trigger_ratio: float = 0.85,
    keep_last_n_messages: int = 6,
    preview_chars: int = 100,
    protected_tools: frozenset[str] = frozenset(),
):
    """Deterministic replacement for ``shape_auto_compact``.

    Only fires when context exceeds ``budget.trigger_tokens(trigger_ratio)``.
    Messages ``context/protection.py`` protects (the current request, pinned
    messages, tool groups that called one of ``protected_tools``) and the
    last ``keep_last_n_messages`` stay verbatim.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        total = count_tokens(ctx.messages)
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        if total <= trigger:
            await next_fn()
            return

        protected = protected_indices(ctx.messages, protected_tools=protected_tools)
        memo = ctx.compaction_memo
        messages, reused = reapply_replacements(memo, LAYER, ctx.messages, keep=protected)
        compacted = 0
        if count_tokens(messages) > trigger:
            messages, compacted = _compact_older(messages, protected, keep_last_n_messages, preview_chars, memo)

        if memo is not None:
            memo.log_stats(LAYER, ctx.run_id, ctx.turn_index)
        if not compacted and not reused:
            await next_fn()
            return
        ctx.messages = messages
        logger.info(
            "deterministic_compact: compacted %d messages (%d again from earlier calls, %d protected), "
            "%d→%d tokens (run=%s, turn=%d, budget=%d)",
            compacted, reused, len(protected), total, count_tokens(messages),
            ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
        )
        await next_fn()

    return _middleware


def _compact_older(
    messages: list[Message],
    protected: frozenset[int],
    keep_last_n_messages: int,
    preview_chars: int,
    memo: CompactionMemo | None,
) -> tuple[list[Message], int]:
    """Compact every unprotected message before the recent tail that no
    layer has compacted yet; returns the list and how many were compacted."""
    raw_tail_start = len(messages) - keep_last_n_messages if keep_last_n_messages > 0 else len(messages)
    tail_start = safe_tail_boundary(messages, raw_tail_start, 0)

    call_id_to_name: dict[str, str] = {}
    call_id_to_args: dict[str, dict] = {}
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                call_id_to_name[tc.id] = tc.name
                call_id_to_args[tc.id] = getattr(tc, "arguments", {}) or {}

    shaped = list(messages)
    compacted = 0
    for i in range(tail_start):
        msg = messages[i]
        if i in protected or getattr(msg, "compacted_by", None) == LAYER:
            continue
        if isinstance(msg, UserMessage):
            # A summary another layer wrote (compacted_by) is already short.
            replacement = _compact_user(msg) if msg.injected and msg.compacted_by is None else msg
        elif isinstance(msg, AssistantMessage):
            replacement = _compact_assistant(msg)
        elif isinstance(msg, ToolMessage):
            replacement = _compact_tool(msg, preview_chars, call_id_to_name, call_id_to_args)
        else:
            replacement = msg
        if replacement is msg:
            continue
        shaped[i] = replacement
        compacted += 1
        if memo is not None:
            memo.remember(LAYER, [msg], [replacement])
    return shaped, compacted
