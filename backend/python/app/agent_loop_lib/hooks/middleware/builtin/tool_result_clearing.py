from __future__ import annotations

import json
import logging
from typing import Any

from app.agent_loop_lib.context.compaction_memo import (
    compacted_before,
    held_elsewhere,
    reapply_replacements,
)
from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import Message, MessageRole
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

    **Sticky and batched** — a result cleared on an earlier call of the run
    is cleared again on every later call (``context/compaction_memo.py``).
    New results are cleared, whole turns oldest first, only while the
    context is over the trigger, and then down to the low-water mark in one
    pass (``context/watermark.py``); a pass too small to be worth a batch
    waits. The next calls then only append, instead of each rewriting the
    one turn that just aged out.

    Only fires when context exceeds ``budget.trigger_tokens(trigger_ratio)``.
    Results ``context/protection.py`` protects (tool groups that called one
    of ``protected_tools``) are never cleared, regardless of age.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        initial_total = count_tokens(ctx.messages)
        marks = watermarks(ctx.budget, ctx.budget.trigger_tokens(trigger_ratio))
        if initial_total <= marks.trigger and not compacted_before(ctx.compaction_memo, LAYER):
            await next_fn()
            return

        protected = protected_indices(ctx.messages, protected_tools=protected_tools)
        memo = ctx.compaction_memo
        messages, reused = reapply_replacements(memo, LAYER, ctx.messages, keep=protected)
        total = count_tokens(messages)
        cleared = 0
        if total > marks.trigger:
            held = protected | held_elsewhere(memo, LAYER, messages)
            turns = _stale_turn_references(messages, keep_last_n_turns, held)
            if marks.worth_compacting(total, sum(_saving(messages, turn) for turn in turns)):
                for turn in turns:
                    for i, replacement in turn.items():
                        if memo is not None:
                            memo.remember(LAYER, [messages[i]], [replacement])
                        messages[i] = replacement
                        cleared += 1

        if cleared or reused:
            logger.info(
                "tool_result_clearing: cleared %d tool result(s) older than the last %d turn(s) "
                "(%d again from earlier calls), %d→%d tokens (low_water=%d, run=%s, turn=%d, budget=%d)",
                cleared, keep_last_n_turns, reused, initial_total, count_tokens(messages),
                marks.low_water, ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
            )
        if memo is not None:
            memo.log_stats(LAYER, ctx.run_id, ctx.turn_index)
        ctx.messages = messages
        await next_fn()

    return _middleware


def _saving(messages: list[Message], replacements: dict[int, Message]) -> int:
    return sum(
        count_message_tokens(messages[i]) - count_message_tokens(new) for i, new in replacements.items()
    )


def _stale_turn_references(
    messages: list[Message],
    keep_last_n_turns: int,
    protected: frozenset[int],
) -> list[dict[int, Message]]:
    """For each tool turn older than the last ``keep_last_n_turns``, oldest
    first, the reference each of its unprotected, not yet cleared results
    would be replaced with. Turns with nothing to clear are left out."""
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
        return []

    from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
        _compact_reference,
    )

    stale: list[dict[int, Message]] = []
    for turn_indices in turns[: len(turns) - keep_last_n_turns]:
        references: dict[int, Message] = {}
        for i in sorted(turn_indices):
            msg = messages[i]
            if i in protected or getattr(msg, "compacted_by", None) in _REFERENCE_LAYERS:
                continue
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
            references[i] = msg.model_copy(update={"content": content, "compacted_by": LAYER})
        if references:
            stale.append(references)
    return stale
