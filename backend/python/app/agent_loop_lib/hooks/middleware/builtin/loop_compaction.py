"""PRE_MODEL shaper: loop-level compaction of older messages.

Once the context passes its trigger, deterministically compresses each run
of unprotected messages before the recent tail into one summary message,
preserving artifact references and key decisions.

Key design choices for long conversations:

* **Stable summaries** — a run summarised on an earlier call of the run is
  sent as the same summary on every later call
  (``context/compaction_memo.py``); messages that age out after it get a
  summary of their own only while the context is still over the trigger.
  The sent prefix therefore stays the same from call to call, and a summary
  is never re-summarised.

* **Tiered extraction** — tool results with ``artifact_meta`` keep
  their artifact ID (always recoverable); tool results without it
  keep only first 100 chars; assistant messages keep first sentence +
  tool-call names; user messages keep first 150 chars.

* **Artifact index** — every artifact ID ever seen in the compacted
  region is collected into a compact index section at the top of the
  summary so the model can still call ``retrieve_artifact_content(…)``
  on data from 30 turns ago.
"""

from __future__ import annotations

import logging
import re

from app.agent_loop_lib.context.compaction_memo import assemble, plan_runs
from app.agent_loop_lib.context.protection import compactable_runs, protected_indices
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens, extract_text
from app.agent_loop_lib.core.types import Message, MessageRole
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    safe_tail_boundary,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

LAYER = "loop_compaction"
_COMPACTION_MARKER = "[Loop compaction:"


def _extract_artifact_ids(msg: Message) -> list[str]:
    """Collect artifact IDs referenced inside a message."""
    ids: list[str] = []
    if isinstance(msg, ToolMessage) and msg.artifact_meta is not None:
        ids.append(msg.artifact_meta.artifact_id)
    if isinstance(msg, UserMessage):
        text = msg.content if isinstance(msg.content, str) else extract_text(msg)
        ids.extend(re.findall(r"artifact:([a-zA-Z0-9_-]+)", text))
    return ids


def _extract_turn_summary(msg: Message) -> str | None:
    """One-line deterministic summary of a single message."""
    if msg.role == MessageRole.SYSTEM:
        return None
    if isinstance(msg, UserMessage):
        text = msg.content if isinstance(msg.content, str) else extract_text(msg)
        return f"[user] {text[:150]}" if text.strip() else None
    if isinstance(msg, AssistantMessage):
        text = extract_text(msg)
        first_line = text.split("\n", 1)[0][:150] if text else ""
        tool_info = ""
        if msg.tool_calls:
            names = ", ".join(tc.name for tc in msg.tool_calls)
            tool_info = f" (called: {names})"
        return f"[assistant] {first_line}{tool_info}" if first_line or tool_info else None
    if isinstance(msg, ToolMessage):
        meta = msg.artifact_meta
        if meta is not None:
            return f"[tool:{msg.tool_call_id}] artifact:{meta.artifact_id} — {meta.summary[:100]}"
        return f"[tool:{msg.tool_call_id}] {msg.text[:100]}"
    return None


_MAX_SUMMARY_LINES = 50


def _summary_message(
    run: list[Message], turn_index: int, max_summary_lines: int,
) -> UserMessage | None:
    """One injected summary for a run of messages, or None when nothing in
    the run is worth a line."""
    artifact_ids: list[str] = []
    summaries: list[str] = []
    for msg in run:
        artifact_ids.extend(_extract_artifact_ids(msg))
        line = _extract_turn_summary(msg)
        if line:
            summaries.append(line)
    if not summaries:
        return None

    if len(summaries) > max_summary_lines:
        omitted = len(summaries) - max_summary_lines
        summaries = [
            f"[...and {omitted} earlier turns omitted]",
            *summaries[-max_summary_lines:],
        ]

    unique_artifacts = list(dict.fromkeys(artifact_ids))
    parts: list[str] = [
        f"{_COMPACTION_MARKER} {len(run)} messages compressed at turn {turn_index}]",
    ]
    if unique_artifacts:
        parts.append(f"[artifacts: {', '.join(unique_artifacts)}]")
    parts.append("\n".join(summaries))
    return UserMessage(content="\n".join(parts), injected=True, compacted_by=LAYER)


def shape_loop_compaction(
    keep_recent: int = 6,
    trigger_ratio: float = 0.6,
    max_summary_lines: int = _MAX_SUMMARY_LINES,
    protected_tools: frozenset[str] = frozenset(),
):
    """PRE_MODEL middleware that compresses older messages once the context
    passes the trigger.

    Every run of unprotected messages before the recent tail becomes one
    summary in place; protected messages (the current request, pinned
    messages, protected tool groups, see ``context/protection.py``) stay
    verbatim between them. Runs summarised on an earlier call of the run are
    sent as the same summary again; the rest is summarised only while the
    context is still over the trigger.

    Parameters
    ----------
    keep_recent:
        Number of most-recent messages to keep verbatim.
    trigger_ratio:
        Only fires when context exceeds ``budget.trigger_tokens(trigger_ratio)``.
    max_summary_lines:
        Maximum number of lines in one summary; older lines are replaced
        with a count.
    protected_tools:
        Tool groups that called one of these are kept verbatim.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        total = count_tokens(ctx.messages)
        trigger = ctx.budget.trigger_tokens(trigger_ratio)
        if total <= trigger:
            await next_fn()
            return

        messages = ctx.messages
        raw_tail_start = len(messages) - keep_recent if keep_recent > 0 else len(messages)
        tail_start = safe_tail_boundary(messages, raw_tail_start, 0)
        runs = compactable_runs(protected_indices(messages, protected_tools=protected_tools), 0, tail_start)
        memo = ctx.compaction_memo
        plans = plan_runs(memo, LAYER, messages, runs)
        reused = sum(len(plan.hits) for plan in plans)
        shaped = assemble(messages, plans)

        new_summaries: dict[int, list[Message]] = {}
        compressed = 0
        if count_tokens(shaped) > trigger:
            for plan in plans:
                rest = list(messages[plan.rest.start:plan.rest.stop])
                summary = _summary_message(rest, ctx.turn_index, max_summary_lines) if rest else None
                if summary is None:
                    continue
                new_summaries[plan.rest.start] = [summary]
                compressed += len(rest)
                if memo is not None:
                    memo.remember(LAYER, rest, [summary])
            shaped = assemble(messages, plans, new_summaries)

        if memo is not None:
            memo.log_stats(LAYER, ctx.run_id, ctx.turn_index)
        if not new_summaries and not reused:
            await next_fn()
            return
        ctx.messages = shaped
        logger.info(
            "loop_compaction: compressed %d messages into %d new summaries (%d reused from earlier calls), "
            "%d→%d tokens (run=%s, turn=%d, budget=%d)",
            compressed, len(new_summaries), reused, total, count_tokens(shaped),
            ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
        )
        await next_fn()

    return _middleware
