from __future__ import annotations

import json
import logging
from itertools import accumulate
from typing import Any

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

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

    **Cleared in steps** — once the context exceeds
    ``budget.trigger_tokens(trigger_ratio)``, every turn but the last
    ``keep_last_n_turns`` is cleared, and that cut stays where it is until
    the context *as cleared* exceeds the trigger again (see
    ``_sticky_cut``). Each request in between extends the one before, so the
    provider's prompt cache keeps the whole history instead of falling back
    to the system prompt on every call.

    ``protected_tool_names`` prevents clearing regardless of age.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        from app.agent_loop_lib.core.tokens import count_tokens

        messages = ctx.messages
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        total = count_tokens(messages)
        if total <= trigger:
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

        cut = _sticky_cut(messages, turns, replacements, trigger, keep_last_n_turns)
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


def _sticky_cut(
    messages: list[Any], turns: list[set[int]], replacements: dict[int, Any], trigger: int, keep: int,
) -> int:
    """How many of the oldest turns to clear.

    Replays the decision every earlier call made: a call happened wherever an
    assistant message follows, on the history before it. The cut moves to
    "all but the last ``keep`` turns" only where that history, cleared up to
    the cut so far, was over the trigger. A pure function of the history, so
    every call agrees with the ones before it about every turn they share.
    """
    from app.agent_loop_lib.core.tokens import count_message_tokens

    sizes = [count_message_tokens(message) for message in messages]
    prefix = list(accumulate(sizes, initial=0))
    turn_ends = [max(turn_indices) for turn_indices in turns]
    calls = [i for i, message in enumerate(messages) if message.role == MessageRole.ASSISTANT]
    cut = done = saved = 0
    for call in [*calls, len(messages)]:
        while done < len(turn_ends) and turn_ends[done] < call:
            done += 1
        if prefix[call] - saved > trigger and done - keep > cut:
            saved += sum(
                sizes[i] - count_message_tokens(replacements[i])
                for turn_indices in turns[cut:done - keep] for i in turn_indices if i in replacements
            )
            cut = done - keep
    return cut
