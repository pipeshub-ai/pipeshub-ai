"""Layer 7 PRE_MODEL shaper: hard budget enforcement after all other shapers.

If context still exceeds the budget after layers 1-6, this guard
aggressively strips content until it fits:

1. Strip ``ThinkingPart`` from all ``AssistantMessage``s.
2. Clear ALL non-recent tool results (regardless of artifact status).
3. Truncate remaining tool results from the tail.

If none of these steps bring the context under budget, a context that still
fits the model's window is sent with less room for the reply (logged); one
past the window raises ``ContextBudgetExceeded``, which the PRE_MODEL pipeline
lets through so the run fails loudly rather than sending a request the
provider rejects.
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.context.errors import ContextBudgetExceeded
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

        logger.warning(
            "synthesis_guard: context (%d tokens) exceeds budget (%d) after all "
            "prior shapers — applying last-resort compaction",
            total, budget,
        )
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
        if total > ctx.budget.hard_limit_tokens:
            logger.error(
                "synthesis_guard: context does not fit the model window "
                "(%d tokens > %d limit, budget %d, turn=%d)",
                total, ctx.budget.hard_limit_tokens, budget, ctx.turn_index,
            )
            raise ContextBudgetExceeded(total, ctx.budget.hard_limit_tokens)
        if total > budget:
            logger.warning(
                "synthesis_guard: context over budget but inside the window, sent "
                "with less room for the reply (%d tokens > %d budget, limit %d, turn=%d)",
                total, budget, ctx.budget.hard_limit_tokens, ctx.turn_index,
            )

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
        is_multipart = isinstance(msg, ToolMessage) and isinstance(msg.content, list)
        if isinstance(msg, ToolMessage) and (len(msg.text) > 200 or is_multipart):
            old_tokens = count_message_tokens(msg)
            # Last-resort truncation drops images along with excess text —
            # a multipart result becomes a plain truncated string even when
            # its text is short, since images don't count toward
            # count_message_tokens but still cost real request bytes/tokens.
            result[i] = msg.model_copy(
                update={"content": msg.text[:200] + "\n[…truncated by synthesis_guard]"}
            )
            total = total - old_tokens + count_message_tokens(result[i])
    return result, total
