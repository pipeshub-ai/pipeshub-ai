from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import Message, MessageRole, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

Summarizer = Callable[[list[Message]], Awaitable[str]]

logger = logging.getLogger(__name__)


def _naive_summary(messages: list[Message]) -> str:
    """Fallback used when no LLM summarizer is configured: a compact,
    lossy joining of message text so compaction still bounds tokens even
    without a model call available (e.g. in tests, or transport-less runs)."""
    from app.agent_loop_lib.core.messages import ToolMessage
    from app.agent_loop_lib.core.tokens import extract_text

    parts = []
    for m in messages:
        if isinstance(m, ToolMessage):
            meta = m.artifact_meta
            if meta is not None:
                parts.append(
                    f"[tool] artifact:{meta.artifact_id}"
                    f" ({meta.tool_name}) — {meta.summary[:100]}"
                )
                continue
            tc_id = m.tool_call_id or "?"
            preview = m.content[:150].strip() if isinstance(m.content, str) else ""
            parts.append(f"[tool:{tc_id}] {preview}")
        else:
            text = extract_text(m).strip()
            if text:
                parts.append(f"[{m.role.value}] {text[:200]}")
    return "\n".join(parts)[:3_000] or "(no content)"


def make_llm_summarizer(transport_registry, provider: str, model: str) -> Summarizer:
    """Build a ``Summarizer`` that calls the real LLM via the already-lazy
    ``TransportRegistry`` — resolving (and thus instantiating) the
    transport only the first time compaction actually triggers, not at
    wiring time.  Single source of truth for both ``ControlPlane`` and
    ``PipesHubAgentFactory``."""
    from app.agent_loop_lib.core.messages import ToolMessage
    from app.agent_loop_lib.core.tokens import extract_text

    def _format_message(m: Message) -> str:
        if isinstance(m, ToolMessage):
            meta = m.artifact_meta
            if meta is not None:
                return (
                    f"[tool] artifact:{meta.artifact_id} "
                    f"tool:{meta.tool_name} — {meta.summary[:150]}"
                )
            tc_id = m.tool_call_id or "?"
            preview = m.content[:200].strip() if isinstance(m.content, str) else ""
            return f"[tool:{tc_id}] {preview}"
        return f"[{m.role.value}] {extract_text(m)}"

    async def _summarize(messages: list[Message]) -> str:
        transport = transport_registry.resolve(provider)
        joined = "\n".join(_format_message(m) for m in messages)
        response = await transport.complete(
            messages=[UserMessage(
                content=(
                    "Summarize the following conversation history concisely, "
                    "preserving all facts, decisions, and open questions.\n\n"
                    "IMPORTANT: Preserve every artifact ID (artifact:xxx) and "
                    "tool name exactly as written — the system needs these to "
                    f"retrieve data later.\n\n{joined}"
                ),
            )],
            system=(
                "You are a context compaction summarizer. Be concise but "
                "lossless on facts and artifact references."
            ),
            model=model,
        )
        text = response.message.text
        return text if text else joined[:3_000]

    return _summarize


# ---------------------------------------------------------------------------
# Atomic message groups
# ---------------------------------------------------------------------------

def _build_groups(messages: list[Message], pin_first_n: int) -> list[list[int]]:
    """Partition *messages* into atomic groups that must stay together.

    A group is one of:
    - An ``AssistantMessage`` with ``tool_calls`` + its following
      ``ToolMessage``s (provider-mandated pairing).
    - A standalone ``UserMessage``, ``SystemMessage``, or
      ``AssistantMessage`` without tool_calls.
    - A standalone orphaned ``ToolMessage`` (shouldn't exist after
      earlier shapers, but handled defensively).

    Returns a list of groups where each group is a list of message
    indices.  Pinned messages (indices < pin_first_n) each form their own
    single-element group at the front.
    """
    groups: list[list[int]] = []
    i = 0

    while i < len(messages):
        msg = messages[i]

        if msg.role == MessageRole.ASSISTANT and getattr(msg, "tool_calls", None):
            group = [i]
            call_ids = {tc.id for tc in msg.tool_calls}
            j = i + 1
            while j < len(messages) and messages[j].role == MessageRole.TOOL:
                tc_id = getattr(messages[j], "tool_call_id", None)
                if tc_id in call_ids:
                    group.append(j)
                else:
                    break
                j += 1
            groups.append(group)
            i = j
        else:
            groups.append([i])
            i += 1

    return groups


def _group_tokens(messages: list[Message], group: list[int]) -> int:
    """Total tokens for a group of message indices."""
    return sum(count_message_tokens(messages[idx]) for idx in group)


# ---------------------------------------------------------------------------
# Budget-aware auto compact
# ---------------------------------------------------------------------------

def shape_auto_compact(
    summarizer: Summarizer | None = None,
    trigger_ratio: float = 0.85,
    max_tail_ratio: float = 0.6,
    pin_first_n: int = 1,
):
    """PRE_MODEL middleware: budget-aware context compaction.

    Fires when total tokens exceed ``trigger_ratio × budget``.  Splits
    messages into three regions:

    1. **Head** (pinned, never compacted) — first ``pin_first_n`` messages.
    2. **Tail** (protected recent context) — determined by walking
       backwards from the end, accumulating atomic message groups until
       adding the next group would exceed ``max_tail_ratio × budget``
       tokens.  Groups are never split: an ``AssistantMessage`` and its
       ``ToolMessage``s move together.
    3. **Middle** (everything between head and tail) — replaced with a
       single summary message.

    This approach adapts to any message-size distribution: a conversation
    with 50 small turns keeps most of them; a conversation with 3 huge
    retrieve results protects only what fits the budget.

    Parameters
    ----------
    summarizer:
        Async callable that produces a summary string from a message
        list.  Defaults to a naive local join (no model call).
    trigger_ratio:
        Only fires when context exceeds this fraction of effective budget.
    max_tail_ratio:
        Maximum fraction of the budget the tail may consume.
    pin_first_n:
        Number of leading messages (goal/system) to never compact.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = ctx.messages
        budget = ctx.budget.effective_max_tokens
        total = count_tokens(messages)

        if total <= budget * trigger_ratio:
            await next_fn()
            return

        if len(messages) <= pin_first_n + 1:
            await next_fn()
            return

        groups = _build_groups(messages, pin_first_n)
        if len(groups) <= 1:
            await next_fn()
            return

        tail_start_group = _find_tail_start(
            messages, groups, pin_first_n, budget, max_tail_ratio,
        )

        # Flatten group indices into head / middle / tail message slices
        head_indices: list[int] = []
        middle_indices: list[int] = []
        tail_indices: list[int] = []

        for gi, group in enumerate(groups):
            if all(idx < pin_first_n for idx in group):
                head_indices.extend(group)
            elif gi >= tail_start_group:
                tail_indices.extend(group)
            else:
                middle_indices.extend(group)

        if not middle_indices:
            await next_fn()
            return

        head = [messages[i] for i in sorted(head_indices)]
        middle = [messages[i] for i in sorted(middle_indices)]
        tail = [messages[i] for i in sorted(tail_indices)]

        summary_text = (
            await summarizer(middle) if summarizer is not None else _naive_summary(middle)
        )
        summary_msg = UserMessage(
            content=f"[Auto-compacted summary of {len(middle)} earlier message(s)]\n{summary_text}",
        )

        ctx.messages = [*head, summary_msg, *tail]
        logger.debug(
            "auto_compact: summarized %d middle messages (%d groups), "
            "tail=%d messages, budget_used=%.0f%%",
            len(middle),
            tail_start_group - _first_non_pinned_group(groups, pin_first_n),
            len(tail),
            count_tokens(ctx.messages) / budget * 100,
        )
        await next_fn()

    return _middleware


def _first_non_pinned_group(groups: list[list[int]], pin_first_n: int) -> int:
    """Index of the first group that contains non-pinned messages."""
    for gi, group in enumerate(groups):
        if any(idx >= pin_first_n for idx in group):
            return gi
    return len(groups)


def _find_tail_start(
    messages: list[Message],
    groups: list[list[int]],
    pin_first_n: int,
    budget: int,
    max_tail_ratio: float,
) -> int:
    """Walk backwards through groups, accumulating tokens into the tail
    until the next group would exceed the tail budget.

    Returns the group index where the tail starts.  Guarantees:
    - At least one group is always in the tail (the most recent).
    - The tail never exceeds ``max_tail_ratio × budget`` tokens unless
      a single atomic group exceeds that limit (unavoidable — we never
      split a group).
    - Pinned groups are never included in the tail calculation.
    """
    max_tail_tokens = int(budget * max_tail_ratio)
    tail_tokens = 0
    tail_start = len(groups)

    for gi in range(len(groups) - 1, -1, -1):
        group = groups[gi]

        # Never include pinned messages in the tail — they're always head.
        if all(idx < pin_first_n for idx in group):
            break

        group_cost = _group_tokens(messages, group)

        # Always include at least one group in the tail.
        if tail_start == len(groups):
            tail_tokens += group_cost
            tail_start = gi
            continue

        # Would adding this group exceed the tail budget?
        if tail_tokens + group_cost > max_tail_tokens:
            break

        tail_tokens += group_cost
        tail_start = gi

    return tail_start
