"""Layer 2 PRE_MODEL shaper: turn-aware artifact compaction.

Replaces ``shape_offload`` with a smarter, two-phase design:

*  **Old-turn** tool results (older than ``keep_last_n_turns``) that
   carry ``artifact_meta`` are compacted to a compact reference once the
   context exceeds ``budget.trigger_tokens(trigger_ratio)``.
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

from app.agent_loop_lib.context.compaction_memo import (
    compacted_before,
    held_elsewhere,
    reapply_replacements,
)
from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.messages import ToolMessage
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


def _compact_reference(msg: ToolMessage) -> str:
    """Build the compact reference string that replaces full content."""
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


LAYER = "artifact_compaction"


def _compact(msg: ToolMessage) -> ToolMessage:
    return msg.model_copy(update={"content": _compact_reference(msg), "compacted_by": LAYER})


def shape_artifact_compaction(
    trigger_ratio: float = 0.5,
    keep_last_n_turns: int = 1,
    protected_tools: frozenset[str] = frozenset(),
):
    """PRE_MODEL middleware that compacts artifact-annotated tool messages.

    Runs as Layer 2, right after ``budget_reduction``. A result compacted on
    an earlier call of the run is compacted again on every later call
    (``context/compaction_memo.py``), so the sent prefix does not change as
    each result ages past the window; old-turn results are compacted only
    while the context is over the trigger, and in batches
    (``context/watermark.py``).

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
        ``budget.trigger_tokens(trigger_ratio)``.
    protected_tools:
        Results of these tools are never replaced by a reference: they carry
        what the answer cites (``context/protection.py``).
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        budget = ctx.budget.effective_max_tokens
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        initial_total = count_tokens(ctx.messages)
        # Recent artifacts are compacted only past the budget, which is never
        # below the trigger, so nothing here acts at or under the trigger.
        if initial_total <= trigger and not compacted_before(ctx.compaction_memo, LAYER):
            await next_fn()
            return

        memo = ctx.compaction_memo
        protected = protected_indices(ctx.messages, protected_tools=protected_tools)
        messages, reused = reapply_replacements(memo, LAYER, ctx.messages, keep=protected)
        total = count_tokens(messages)
        held = held_elsewhere(memo, LAYER, messages)
        prev_cutoff = ctx.turn_index - keep_last_n_turns

        prev_indices: list[int] = []
        curr_with_schema: list[int] = []
        curr_without_schema: list[int] = []
        for i, msg in enumerate(messages):
            if (
                not isinstance(msg, ToolMessage) or msg.artifact_meta is None
                or msg.compacted_by == LAYER or i in protected or i in held
            ):
                continue
            if msg.artifact_meta.turn_index < prev_cutoff:
                prev_indices.append(i)
            elif msg.artifact_meta.result_schema is not None:
                curr_with_schema.append(i)
            else:
                curr_without_schema.append(i)

        def swap(i: int, running_total: int) -> int:
            original = messages[i]
            messages[i] = _compact(original)
            if memo is not None:
                memo.remember(LAYER, [original], [messages[i]])
            return running_total - count_message_tokens(original) + count_message_tokens(messages[i])

        compacted_count = 0
        marks = watermarks(ctx.budget, trigger)
        prev_saving = sum(count_message_tokens(messages[i]) - count_message_tokens(_compact(messages[i]))
                          for i in prev_indices)
        if marks.worth_compacting(total, prev_saving):
            for i in prev_indices:
                total = swap(i, total)
                compacted_count += 1

        overflow = total > budget
        for tier in (curr_with_schema, curr_without_schema):
            if total <= budget:
                break
            tier.sort(key=lambda i: count_message_tokens(messages[i]), reverse=True)
            for i in tier:
                total = swap(i, total)
                compacted_count += 1
                if total <= budget:
                    break

        if compacted_count or reused:
            logger.info(
                "artifact_compaction: compacted %d %s (%d again from earlier calls), %d→%d tokens "
                "(low_water=%d, run=%s, turn=%d, budget=%d)",
                compacted_count,
                "artifact(s) total (incl. recent-turn overflow)" if overflow else "prev-turn artifact(s)",
                reused, initial_total, total, marks.low_water, ctx.run_id, ctx.turn_index, budget,
            )
        if memo is not None:
            memo.log_stats(LAYER, ctx.run_id, ctx.turn_index)
        ctx.messages = messages
        await next_fn()

    return _middleware
