"""Layer 6 PRE_MODEL shaper: deterministic rule-based compaction.

Replaces the LLM-backed ``auto_compact`` summarizer with a pure
extractive compaction that produces identical output for identical
input.  Zero LLM calls, zero non-determinism.

Rules:
- ``SystemMessage``: always kept verbatim (pinned).
- ``UserMessage`` (human-typed): kept verbatim (typically small).
- ``UserMessage`` (loop-compaction summary): kept verbatim — these
  are produced by L4 ``shape_loop_compaction`` and must survive intact.
- ``UserMessage`` (other injected): compacted to first 200 chars.
  Prevents recovery nudges from piling up in the middle zone.
- ``AssistantMessage`` (recent N): kept verbatim.
- ``AssistantMessage`` (older): first sentence + tool_call names/IDs.
- ``ToolMessage`` with ``artifact_meta``: compact reference only.
- ``ToolMessage`` without ``artifact_meta``: tool_call_id + first
  ``preview_chars`` characters.
"""

from __future__ import annotations

import json
import logging

from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens, extract_text
from app.agent_loop_lib.core.types import Message, MessageRole
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    safe_tail_boundary,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    _compact_reference,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    _is_compaction_summary,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


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
    return msg.model_copy(
        update={
            "content": [TextPart(text=compacted_text)],
            "tool_calls": msg.tool_calls,
        }
    )


def _compact_tool(
    msg: ToolMessage,
    preview_chars: int,
    call_id_to_name: dict[str, str] | None = None,
    call_id_to_args: dict[str, dict] | None = None,
) -> ToolMessage:
    if msg.artifact_meta is not None:
        return msg.model_copy(update={"content": _compact_reference(msg)})

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
    return msg.model_copy(update={"content": "\n".join(lines)})


def _compact_user(msg: UserMessage, max_chars: int = 200) -> UserMessage:
    """Shorten an injected UserMessage (compaction summary, recovery nudge).

    Human-typed messages are always kept verbatim — only programmatic
    injections (``injected=True``) are truncated, because they can grow
    large (e.g. a loop-compaction summary accumulated over 50 turns).
    """
    text = msg.content if isinstance(msg.content, str) else extract_text(msg)
    if len(text) <= max_chars:
        return msg
    return msg.model_copy(update={"content": text[:max_chars] + "..."})


def shape_deterministic_compact(
    trigger_ratio: float = 0.85,
    keep_last_n_messages: int = 6,
    preview_chars: int = 100,
):
    """Deterministic replacement for ``shape_auto_compact``.

    Only fires when context exceeds ``budget.trigger_tokens(trigger_ratio)``.
    Messages ``context/protection.py`` protects (the current request, pinned
    messages) and the last ``keep_last_n_messages`` stay verbatim.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = ctx.messages
        total = count_tokens(messages)
        if total <= ctx.budget.trigger_tokens(trigger_ratio):
            await next_fn()
            return

        raw_tail_start = len(messages) - keep_last_n_messages if keep_last_n_messages > 0 else len(messages)
        tail_start = safe_tail_boundary(messages, raw_tail_start, 0)
        protected = protected_indices(messages)
        compactable = {i for i in range(tail_start) if i not in protected}
        if not compactable:
            await next_fn()
            return

        call_id_to_name: dict[str, str] = {}
        call_id_to_args: dict[str, dict] = {}
        for msg in messages:
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    call_id_to_name[tc.id] = tc.name
                    call_id_to_args[tc.id] = getattr(tc, "arguments", {}) or {}

        shaped: list[Message] = []
        for i, msg in enumerate(messages):
            if i not in compactable or msg.role == MessageRole.SYSTEM:
                shaped.append(msg)
            elif isinstance(msg, UserMessage):
                if _is_compaction_summary(msg):
                    shaped.append(msg)
                elif getattr(msg, "injected", False):
                    shaped.append(_compact_user(msg))
                else:
                    shaped.append(msg)
            elif isinstance(msg, AssistantMessage):
                shaped.append(_compact_assistant(msg))
            elif isinstance(msg, ToolMessage):
                shaped.append(_compact_tool(msg, preview_chars, call_id_to_name, call_id_to_args))
            else:
                shaped.append(msg)

        ctx.messages = shaped
        logger.info(
            "deterministic_compact: compacted %d messages (%d protected, tail=%d), "
            "%d→%d tokens (turn=%d, budget=%d)",
            len(compactable), len(protected), len(messages) - tail_start, total, count_tokens(ctx.messages),
            ctx.turn_index, ctx.budget.effective_max_tokens,
        )
        await next_fn()

    return _middleware
