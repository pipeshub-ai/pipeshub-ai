"""Layer 7 PRE_MODEL shaper: hard budget enforcement after all other shapers.

If context still exceeds the budget after layers 1-6, this guard
aggressively strips content until it fits:

1. Strip ``ThinkingPart`` from all ``AssistantMessage``s.
2. Clear ALL non-recent tool results (regardless of artifact status).
3. Truncate remaining tool results from the tail.

If none of these steps bring the context under budget, the guard raises
``ContextBudgetExceeded`` so the caller fails loudly rather than sending
an oversized context to the provider.
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ThinkingPart,
    ToolMessage,
)
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


class ContextBudgetExceeded(Exception):
    """Raised when the synthesis guard cannot fit context under budget."""

    def __init__(self, token_count: int, budget: int) -> None:
        self.token_count = token_count
        self.budget = budget
        super().__init__(
            f"Context ({token_count} tokens) exceeds budget ({budget} tokens) "
            "after all compaction attempts"
        )


def shape_synthesis_guard(
    keep_last_n_tool_results: int = 2,
):
    """Last-resort shaper that guarantees context fits the budget.

    Parameters
    ----------
    keep_last_n_tool_results:
        Number of most-recent tool results to protect from aggressive
        clearing.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        budget = ctx.budget.effective_max_tokens
        total = count_tokens(ctx.messages)
        if total <= budget:
            await next_fn()
            return

        messages = list(ctx.messages)

        messages = _strip_thinking(messages)
        total = count_tokens(messages)
        if total <= budget:
            ctx.messages = messages
            await next_fn()
            return

        messages = _clear_old_tool_results(
            messages, keep_last_n=keep_last_n_tool_results
        )
        total = count_tokens(messages)
        if total <= budget:
            ctx.messages = messages
            await next_fn()
            return

        messages, total = _truncate_tool_results(messages, budget, total)
        if total > budget:
            logger.error(
                "synthesis_guard: could not fit context under budget "
                "(%d tokens > %d budget)",
                total,
                budget,
            )
            raise ContextBudgetExceeded(total, budget)

        ctx.messages = messages
        await next_fn()

    return _middleware


def _strip_thinking(messages: list) -> list:
    result = []
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.content:
            filtered = [p for p in msg.content if not isinstance(p, ThinkingPart)]
            if filtered != msg.content:
                msg = msg.model_copy(update={"content": filtered})
        result.append(msg)
    return result


_GUARD_CLEARED = "[cleared by synthesis_guard]"


def _clear_old_tool_results(messages: list, keep_last_n: int) -> list:
    from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
        _compact_reference,
    )

    tool_indices = [
        i for i, m in enumerate(messages)
        if m.role == MessageRole.TOOL and isinstance(m, ToolMessage)
    ]
    if len(tool_indices) <= keep_last_n:
        return messages

    clearable = set(tool_indices[: len(tool_indices) - keep_last_n])
    result = []
    for i, msg in enumerate(messages):
        if i in clearable and isinstance(msg, ToolMessage) and msg.content != _GUARD_CLEARED:
            if getattr(msg, "artifact_meta", None) is not None:
                result.append(msg.model_copy(update={"content": _compact_reference(msg)}))
            else:
                result.append(msg.model_copy(update={"content": _GUARD_CLEARED}))
        else:
            result.append(msg)
    return result


def _truncate_tool_results(
    messages: list, budget: int, running_total: int
) -> tuple[list, int]:
    """Truncate long tool results from the tail, tracking token delta."""
    result = list(messages)
    total = running_total
    for i in range(len(result) - 1, -1, -1):
        if total <= budget:
            break
        msg = result[i]
        if isinstance(msg, ToolMessage) and len(msg.content) > 200:
            old_tokens = count_message_tokens(msg)
            result[i] = msg.model_copy(
                update={"content": msg.content[:200] + "\n[…truncated by synthesis_guard]"}
            )
            total = total - old_tokens + count_message_tokens(result[i])
    return result, total
