from __future__ import annotations

import json
import logging
from typing import Any

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

_TOOL_REF_PREFIX = "tool: "

_LEGACY_CLEARED_PREFIX = "[cleared"

# Kept, not recomputed: the shapers before this one rewrite older results per
# call (artifact compaction shrinks an artifact once it ages), so a later call's
# input does not show what an earlier call saw.
_CUT: StateSlot[int] = StateSlot(key="tool_result_clearing.cut", default_factory=lambda: 0)


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

    **Cleared in steps** — once the context exceeds
    ``budget.trigger_tokens(trigger_ratio)``, every turn but the last
    ``keep_last_n_turns`` is cleared. The run keeps that cut, so later calls
    clear the same turns, and it moves only when the context *as cleared*
    exceeds the trigger again (see ``_next_cut``). Each request in between
    extends the one before, so the provider's prompt cache keeps the whole
    history instead of falling back to the system prompt on every call.
    Without a run scope there is no earlier cut, so each call clears from
    scratch.

    ``protected_tool_names`` prevents clearing regardless of age.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens

        run = ctx.scope.run if ctx.scope is not None else None
        cut = run.get(_CUT) if run is not None else 0
        messages = ctx.messages
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        total = count_tokens(messages)
        if not cut and total <= trigger:
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

        from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
            _compact_reference,
        )

        replacements: dict[int, Any] = {}
        for turn_indices in turns:
            for i in turn_indices:
                msg = messages[i]
                tc_id = getattr(msg, "tool_call_id", None)
                tool_name = call_id_to_name.get(tc_id, "") if tc_id else ""
                if protected_tool_names and tool_name in protected_tool_names:
                    continue
                text_content = msg.text
                if isinstance(msg.content, str) and _is_already_compact(text_content):
                    continue
                if getattr(msg, "artifact_meta", None) is not None:
                    replacements[i] = msg.model_copy(update={"content": _compact_reference(msg)})
                    continue
                # Any images the tool returned belong to the current turn's
                # context; once a result ages past `keep_last_n_turns` its
                # images are as expendable as its text (same rule L4/L5/L7
                # already apply) — the reference is plain text so the model
                # can re-call the tool if it needs the image again.
                ref = _build_tool_ref(
                    tool_name=tool_name,
                    tool_args=call_id_to_args.get(tc_id, {}) if tc_id else {},
                    tool_call_id=tc_id or "",
                    content=text_content,
                    is_error=getattr(msg, "is_error", False),
                )
                replacements[i] = msg.model_copy(update={"content": ref})

        gains = {
            i: count_message_tokens(messages[i]) - count_message_tokens(replacement)
            for i, replacement in replacements.items()
        }
        cut = _next_cut(
            turns, gains,
            total=total,
            trigger=trigger,
            # Waiting for a worthwhile step may use half the room the trigger
            # leaves below the budget; the rest stays free for the next results.
            ceiling=(trigger + ctx.budget.effective_max_tokens) // 2,
            keep=keep_last_n_turns,
            cut=min(cut, len(turns) - keep_last_n_turns),
        )
        if run is not None:
            run.set(_CUT, cut)
        clearable = {i for turn_indices in turns[:cut] for i in turn_indices if i in replacements}
        if not clearable:
            await next_fn()
            return

        shaped = [replacements[i] if i in clearable else msg for i, msg in enumerate(messages)]
        logger.info(
            "tool_result_clearing: cleared %d tool result(s) in the oldest %d of %d turn(s), "
            "%d→%d tokens (turn=%d, budget=%d)",
            len(clearable), cut, len(turns), total, count_tokens(shaped),
            ctx.turn_index, ctx.budget.effective_max_tokens,
        )
        ctx.messages = shaped
        await next_fn()

    return _middleware


def _next_cut(
    turns: list[set[int]], gains: dict[int, int], *,
    total: int, trigger: int, ceiling: int, keep: int, cut: int,
) -> int:
    """How many of the oldest turns to clear, given the ``cut`` the run's
    previous call cleared.

    The cut moves to "all but the last ``keep`` turns" once the context,
    cleared up to ``cut``, is over ``trigger``. Tokens no step removes
    (protected results, the kept turns) can hold it over the trigger by
    themselves, and a cut that moved on every call would rewrite the history
    on every call. So after the first step the cut also waits until the step
    would save at least as much as clearing the kept turns would, unless the
    context is past ``ceiling``.
    """
    target = len(turns) - keep

    def saved(start: int, stop: int) -> int:
        return sum(gains.get(i, 0) for turn_indices in turns[start:stop] for i in turn_indices)

    cleared_total = total - saved(0, cut)
    if target <= cut or cleared_total <= trigger:
        return cut
    if cut > 0 and cleared_total <= ceiling and saved(cut, target) < saved(target, len(turns)):
        return cut
    return target
