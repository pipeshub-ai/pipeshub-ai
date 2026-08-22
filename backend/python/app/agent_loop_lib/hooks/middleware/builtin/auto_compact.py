from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import Message, MessageRole, UserMessage
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

Summarizer = Callable[[list[Message]], Awaitable[str]]

logger = logging.getLogger(__name__)

_TOOL_RESULT_CHARS = 4_000
_TEXT_MSG_CHARS = 2_000
_NAIVE_TOOL_CHARS = 500
_NAIVE_TEXT_CHARS = 500
_NAIVE_TOTAL_CHARS = 8_000
_MAX_SUMMARIZER_INPUT_CHARS = 100_000

_SUMMARIZER_SYSTEM = (
    "You are a context compaction assistant. Produce a comprehensive summary "
    "that preserves enough factual detail for the conversation to continue "
    "without loss of information. Err on the side of preserving too much "
    "rather than too little."
)

_SUMMARIZER_PROMPT = (
    "Summarize the conversation history below. This summary will REPLACE "
    "the original messages, so it must preserve all information the "
    "assistant needs to continue working without losing context.\n\n"
    "MUST PRESERVE:\n"
    "- Record/entity names, IDs, titles, and statuses\n"
    "- Citation references (e.g. [ref1], [ref2], R-labels, artifact:xxx)\n"
    "- Key findings and data points from tool results\n"
    "- Decisions made and their rationale\n"
    "- Tool names and what they returned (key fields, not raw dumps)\n"
    "- Open questions and unresolved issues\n"
    "- Numerical data, dates, and specific values\n"
    "- The user's original query/request\n\n"
    "FORMAT: Use structured sections with bullet points. For search or "
    "retrieval results, list each record with its key fields rather than "
    "writing narrative prose. Faithfully represent what the tools returned.\n\n"
    "CONVERSATION TO SUMMARIZE:\n"
)


def _naive_summary(messages: list[Message]) -> str:
    """Fallback used when no LLM summarizer is configured: a compact,
    lossy joining of message text so compaction still bounds tokens even
    without a model call available (e.g. in tests, or transport-less runs)."""
    from app.agent_loop_lib.core.messages import ToolMessage
    from app.agent_loop_lib.core.tokens import extract_text

    parts: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            meta = m.artifact_meta
            if meta is not None:
                parts.append(
                    f"[tool] artifact:{meta.artifact_id}"
                    f" ({meta.tool_name}) — {meta.summary[:200]}"
                )
                continue
            tc_id = m.tool_call_id or "?"
            content = m.content if isinstance(m.content, str) else ""
            preview = content[:_NAIVE_TOOL_CHARS].strip()
            if len(content) > _NAIVE_TOOL_CHARS:
                preview += "...[truncated]"
            parts.append(f"[tool:{tc_id}] {preview}")
        else:
            text = extract_text(m).strip()
            if text:
                preview = text[:_NAIVE_TEXT_CHARS]
                if len(text) > _NAIVE_TEXT_CHARS:
                    preview += "...[truncated]"
                parts.append(f"[{m.role.value}] {preview}")
    _TRUNCATION_MARKER = "...[truncated]"
    joined = "\n".join(parts)
    if len(joined) > _NAIVE_TOTAL_CHARS:
        return joined[:_NAIVE_TOTAL_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return joined or "(no content)"


def make_llm_summarizer(transport_registry, provider: str, model: str) -> Summarizer:
    """Build a ``Summarizer`` that calls the real LLM via the already-lazy
    ``TransportRegistry`` — resolving (and thus instantiating) the
    transport only the first time compaction actually triggers, not at
    wiring time.  Single source of truth for both ``ControlPlane`` and
    ``PipesHubAgentFactory``."""
    from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage
    from app.agent_loop_lib.core.tokens import extract_text

    def _format_message(m: Message) -> str:
        if isinstance(m, ToolMessage):
            meta = m.artifact_meta
            if meta is not None:
                return (
                    f"[tool] artifact:{meta.artifact_id} "
                    f"tool:{meta.tool_name} — {meta.summary[:300]}"
                )
            tc_id = m.tool_call_id or "?"
            content = m.content if isinstance(m.content, str) else ""
            if len(content) > _TOOL_RESULT_CHARS:
                content = content[:_TOOL_RESULT_CHARS] + "\n...[truncated]"
            return f"[tool:{tc_id}]\n{content}"

        if isinstance(m, AssistantMessage) and m.tool_calls:
            names = ", ".join(tc.name for tc in m.tool_calls)
            text = extract_text(m).strip()
            prefix = f"[assistant → called: {names}]"
            if not text:
                return prefix
            if len(text) > _TEXT_MSG_CHARS:
                text = text[:_TEXT_MSG_CHARS] + "\n...[truncated]"
            return f"{prefix}\n{text}"

        text = extract_text(m)
        if len(text) > _TEXT_MSG_CHARS:
            text = text[:_TEXT_MSG_CHARS] + "\n...[truncated]"
        return f"[{m.role.value}] {text}"

    async def _summarize(messages: list[Message]) -> str:
        transport = transport_registry.resolve(provider)

        _SEPARATOR = "\n\n"
        _TRUNCATION_SUFFIX = "\n...[truncated]"
        _OMISSION_TEMPLATE = "[{} earlier message(s) omitted]"
        prompt_overhead = len(_SUMMARIZER_PROMPT) + 1  # +1 for the "\n" joining prompt and body
        budget = _MAX_SUMMARIZER_INPUT_CHARS - prompt_overhead

        parts: list[str] = []
        total_chars = 0
        for i, m in enumerate(messages):
            fmt = _format_message(m)
            sep_cost = len(_SEPARATOR) if parts else 0
            if total_chars + sep_cost + len(fmt) > budget:
                remaining = budget - total_chars - sep_cost - len(_TRUNCATION_SUFFIX)
                if remaining > 0:
                    parts.append(fmt[:remaining] + _TRUNCATION_SUFFIX)
                skipped = len(messages) - (i + 1)
                if skipped > 0:
                    parts.append(_OMISSION_TEMPLATE.format(skipped))
                break
            parts.append(fmt)
            total_chars += sep_cost + len(fmt)

        joined = _SEPARATOR.join(parts)

        try:
            response = await transport.complete(
                messages=[UserMessage(
                    content=f"{_SUMMARIZER_PROMPT}\n{joined}",
                )],
                system=_SUMMARIZER_SYSTEM,
                model=model,
            )
            text = response.message.text
            if not text or not text.strip():
                return _naive_summary(messages)
            return text
        except Exception:
            logger.warning(
                "auto_compact: LLM summarizer failed, falling back to naive",
                exc_info=True,
            )
            return _naive_summary(messages)

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
        logger.info(
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
