"""Layer 2 PRE_MODEL shaper: turn-aware artifact compaction.

Replaces ``shape_offload`` with a smarter, two-phase design:

*  **Old-turn** tool results (older than ``keep_last_n_turns``) that
   carry ``artifact_meta`` are compacted to a compact reference once the
   context exceeds ``trigger_ratio × budget``.
*  **Recent-turn** results (within ``keep_last_n_turns``) are kept full
   when budget allows.  This ensures the model sees full data from its
   most recent tool calls at least once — the model that *called* the
   tool never sees the result inline (it's added after the model call),
   so the next turn is the first opportunity.  When the context still
   exceeds the absolute budget after compacting old-turn results,
   recent-turn results are compacted in priority order:
   1. Results WITH ``result_schema`` (model can use ``run_code`` later)
   2. Results WITHOUT ``result_schema`` (model's only synthesis path)

Within each priority tier, largest results are compacted first.
"""

from __future__ import annotations

import json
import logging

from app.agent_loop_lib.core.messages import ToolMessage
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


def _compact_reference(msg: ToolMessage) -> str:
    """Build the compact reference string that replaces full content."""
    meta = msg.artifact_meta
    if meta is None:
        return msg.content

    lines = [f"[artifact:{meta.artifact_id}]"]
    content_type = meta.tool_name.replace("__", ".") if meta.tool_name else "tool_result"
    lines.append(f"type: {content_type}")
    if meta.tool_name:
        lines.append(f"tool: {meta.tool_name}")
    if meta.tool_args:
        args_str = json.dumps(meta.tool_args, default=str)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
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


def _swap_compact(messages: list, i: int, running_total: int) -> int:
    """Replace ``messages[i]`` with its compact reference and return the
    updated running token total (O(1) — no full rescan)."""
    old_tokens = count_message_tokens(messages[i])
    messages[i] = messages[i].model_copy(
        update={"content": _compact_reference(messages[i])}
    )
    new_tokens = count_message_tokens(messages[i])
    return running_total - old_tokens + new_tokens


def shape_artifact_compaction(
    pin_first_n: int = 1,
    trigger_ratio: float = 0.5,
    keep_last_n_turns: int = 1,
):
    """PRE_MODEL middleware that compacts artifact-annotated tool messages.

    Runs as Layer 2, right after ``budget_reduction``.

    Parameters
    ----------
    keep_last_n_turns:
        Artifacts from the last *N* tool-call turns are treated as
        "recent" and kept inline (unless the absolute budget overflows).
        Default ``1`` — the model that called a tool never sees the
        result inline (it arrives after the model call), so the next
        turn must keep it to give the model its first look.
    trigger_ratio:
        Old-turn artifacts are only compacted when context exceeds
        ``trigger_ratio × budget``.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = list(ctx.messages)
        budget = ctx.budget.effective_max_tokens
        current_turn = ctx.turn_index

        prev_indices: list[int] = []
        curr_with_schema: list[int] = []
        curr_without_schema: list[int] = []

        prev_cutoff = current_turn - keep_last_n_turns

        for i, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage) or msg.artifact_meta is None:
                continue
            if msg.artifact_meta.turn_index < prev_cutoff:
                prev_indices.append(i)
            else:
                if msg.artifact_meta.result_schema is not None:
                    curr_with_schema.append(i)
                else:
                    curr_without_schema.append(i)

        total = count_tokens(messages)
        initial_total = total
        compacted_count = 0

        if total > budget * trigger_ratio:
            for i in prev_indices:
                total = _swap_compact(messages, i, total)
                compacted_count += 1

        if total <= budget:
            if compacted_count:
                logger.info(
                    "artifact_compaction: compacted %d prev-turn artifact(s), "
                    "%d→%d tokens (turn=%d, budget=%d)",
                    compacted_count, initial_total, total,
                    current_turn, budget,
                )
            ctx.messages = messages
            await next_fn()
            return

        curr_with_schema.sort(
            key=lambda i: count_message_tokens(messages[i]), reverse=True
        )
        for i in curr_with_schema:
            total = _swap_compact(messages, i, total)
            if total <= budget:
                break

        if total <= budget:
            ctx.messages = messages
            await next_fn()
            return

        curr_without_schema.sort(
            key=lambda i: count_message_tokens(messages[i]), reverse=True
        )
        for i in curr_without_schema:
            total = _swap_compact(messages, i, total)
            compacted_count += 1
            if total <= budget:
                break

        logger.info(
            "artifact_compaction: compacted %d artifact(s) total (incl. recent-turn overflow), "
            "%d→%d tokens (turn=%d, budget=%d)",
            compacted_count, initial_total, total,
            current_turn, budget,
        )
        ctx.messages = messages
        await next_fn()

    return _middleware
