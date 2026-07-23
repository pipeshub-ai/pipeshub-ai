"""PRE_MODEL shaper: loop-level turn-boundary compaction.

Fires every ``compact_every_n_turns`` turns and deterministically
compresses older messages into a **single merged summary** message,
preserving artifact references and key decisions.

Key design choices for long conversations:

* **Summary merging** — previous compaction summaries (recognised by the
  ``_COMPACTION_MARKER`` prefix) are absorbed into the new summary
  rather than being recursively summarised.  This prevents the lossy
  cascade where a summary-of-a-summary-of-a-summary loses all signal.

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

_COMPACTION_MARKER = "[Loop compaction:"


def _is_compaction_summary(msg: Message) -> bool:
    """True when *msg* was produced by a previous loop compaction pass."""
    if not isinstance(msg, UserMessage) or not getattr(msg, "injected", False):
        return False
    text = msg.content if isinstance(msg.content, str) else extract_text(msg)
    return text.startswith(_COMPACTION_MARKER)


_OMITTED_MARKER = "[...and "


def _extract_prior_summary_lines(msg: UserMessage) -> list[str]:
    """Pull the body lines out of a previous compaction summary.

    Returns the individual ``[user] …`` / ``[assistant] …`` lines so
    they can be merged directly into the new summary instead of being
    recursively compressed (which would truncate them to 150 chars and
    lose signal on every pass).

    Structural lines (compaction header, artifact index, omission
    counts) are filtered out — the new summary produces its own.
    """
    text = msg.content if isinstance(msg.content, str) else extract_text(msg)
    lines = text.split("\n")
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(_COMPACTION_MARKER):
            continue
        if stripped.startswith("[artifacts:"):
            continue
        if stripped.startswith(_OMITTED_MARKER):
            continue
        if stripped:
            body.append(stripped)
    return body


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
        return f"[tool:{msg.tool_call_id}] {msg.content[:100]}"
    return None


_MAX_SUMMARY_LINES = 50


def shape_loop_compaction(
    compact_every_n_turns: int = 5,
    keep_recent: int = 6,
    pin_first_n: int = 1,
    trigger_ratio: float = 0.6,
    max_summary_lines: int = _MAX_SUMMARY_LINES,
):
    """PRE_MODEL middleware that compresses older turns at regular
    boundaries.

    Parameters
    ----------
    compact_every_n_turns:
        Only fires when ``turn_index`` is a multiple of this value.
    keep_recent:
        Number of most-recent messages to keep verbatim.
    pin_first_n:
        Number of leading messages (system/goal) to never compact.
    trigger_ratio:
        Only fires when context exceeds this fraction of budget.
    max_summary_lines:
        Maximum number of summary lines to keep.  When merging prior
        summaries, only the most recent lines are retained; older ones
        are replaced with a count.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        if ctx.turn_index == 0 or ctx.turn_index % compact_every_n_turns != 0:
            await next_fn()
            return

        budget = ctx.budget.effective_max_tokens
        if count_tokens(ctx.messages) <= budget * trigger_ratio:
            await next_fn()
            return

        messages = ctx.messages
        if len(messages) <= pin_first_n + keep_recent:
            await next_fn()
            return

        head = messages[:pin_first_n]
        raw_tail_start = len(messages) - keep_recent if keep_recent > 0 else len(messages)
        tail_start = safe_tail_boundary(messages, raw_tail_start, pin_first_n)
        tail = messages[tail_start:]
        middle = messages[pin_first_n:tail_start]
        if not middle:
            await next_fn()
            return

        all_artifact_ids: list[str] = []
        summaries: list[str] = []

        for msg in middle:
            all_artifact_ids.extend(_extract_artifact_ids(msg))

            if _is_compaction_summary(msg):
                summaries.extend(_extract_prior_summary_lines(msg))
                continue

            line = _extract_turn_summary(msg)
            if line:
                summaries.append(line)

        if not summaries:
            await next_fn()
            return

        if len(summaries) > max_summary_lines:
            omitted = len(summaries) - max_summary_lines
            summaries = [
                f"[...and {omitted} earlier turns omitted]",
                *summaries[-max_summary_lines:],
            ]

        unique_artifacts = list(dict.fromkeys(all_artifact_ids))
        parts: list[str] = [
            f"{_COMPACTION_MARKER} {len(middle)} messages compressed "
            f"at turn {ctx.turn_index}]",
        ]
        if unique_artifacts:
            parts.append(f"[artifacts: {', '.join(unique_artifacts)}]")
        parts.append("\n".join(summaries))

        summary_msg = UserMessage(
            content="\n".join(parts),
            injected=True,
        )
        ctx.messages = [*head, summary_msg, *tail]
        logger.debug(
            "loop_compaction: compressed %d messages into summary "
            "(%d summary lines, %d artifacts) at turn %d",
            len(middle), len(summaries), len(unique_artifacts), ctx.turn_index,
        )
        await next_fn()

    return _middleware
