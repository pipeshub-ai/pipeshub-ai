"""Layer 7 PRE_MODEL shaper: hard budget enforcement after all other shapers.

If context still exceeds the budget after layers 1-6, this guard
aggressively strips content until it fits:

1. Clear ALL non-recent tool results (regardless of artifact status).
2. Truncate remaining tool results from the tail.

Thinking is not stripped: no transport resends it and the estimator does not
count it (``core/tokens.py``), so removing it would not change the size.

Tool results ``context/protection.py`` protects (tool groups that called one
of ``protected_tools``) are never cleared or truncated here; the sliding
window before this guard is the only layer that may drop them.

If none of these steps bring the context under budget, a context that still
fits the model's window is sent with less room for the reply (logged); one
past the window raises ``ContextBudgetExceeded``, which the PRE_MODEL pipeline
lets through so the run fails loudly rather than sending a request the
provider rejects.
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.context.protection import protected_tool_indices
from app.agent_loop_lib.core.messages import ToolMessage
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

LAYER = "synthesis_guard"


def shape_synthesis_guard(
    keep_last_n_tool_results: int = 2,
    protected_tools: frozenset[str] = frozenset(),
):
    """Last-resort shaper that guarantees context fits the budget.

    Parameters
    ----------
    keep_last_n_tool_results:
        Number of most-recent tool results to protect from aggressive
        clearing.
    protected_tools:
        Results of these tools are never cleared or truncated.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        budget = ctx.budget.effective_max_tokens
        total = count_tokens(ctx.messages)
        if total <= budget:
            await next_fn()
            return

        logger.warning(
            "synthesis_guard: context (%d tokens) exceeds budget (%d) after all "
            "prior shapers — applying last-resort compaction (run=%s, turn=%d)",
            total, budget, ctx.run_id, ctx.turn_index,
        )
        messages = list(ctx.messages)
        keep = protected_tool_indices(messages, protected_tools)

        messages = _clear_old_tool_results(
            messages, keep_last_n=keep_last_n_tool_results, keep=keep,
        )
        total = count_tokens(messages)
        if total <= budget:
            ctx.messages = messages
            await next_fn()
            return

        messages, total = _truncate_tool_results(messages, budget, total, keep=keep)
        if total > ctx.budget.hard_limit_tokens:
            logger.error(
                "synthesis_guard: context does not fit the model window "
                "(%d tokens > %d limit, budget %d, run=%s, turn=%d)",
                total, ctx.budget.hard_limit_tokens, budget, ctx.run_id, ctx.turn_index,
            )
            raise ContextBudgetExceeded(total, ctx.budget.hard_limit_tokens)
        if total > budget:
            logger.warning(
                "synthesis_guard: context over budget but inside the window, sent "
                "with less room for the reply (%d tokens > %d budget, limit %d, run=%s, turn=%d)",
                total, budget, ctx.budget.hard_limit_tokens, ctx.run_id, ctx.turn_index,
            )

        ctx.messages = messages
        await next_fn()

    return _middleware


_GUARD_CLEARED = "[cleared by synthesis_guard]"


def _clear_old_tool_results(messages: list, keep_last_n: int, keep: frozenset[int] = frozenset()) -> list:
    from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
        _compact_reference,
    )

    tool_indices = [
        i for i, m in enumerate(messages)
        if m.role == MessageRole.TOOL and isinstance(m, ToolMessage)
    ]
    if len(tool_indices) <= keep_last_n:
        return messages

    clearable = set(tool_indices[: len(tool_indices) - keep_last_n]) - keep
    result = []
    for i, msg in enumerate(messages):
        if i in clearable and isinstance(msg, ToolMessage) and msg.compacted_by != LAYER:
            content = _compact_reference(msg) if msg.artifact_meta is not None else _GUARD_CLEARED
            result.append(msg.model_copy(update={"content": content, "compacted_by": LAYER}))
        else:
            result.append(msg)
    return result


def _truncate_tool_results(
    messages: list, budget: int, running_total: int, keep: frozenset[int] = frozenset(),
) -> tuple[list, int]:
    """Truncate long tool results from the tail, tracking token delta."""
    result = list(messages)
    total = running_total
    for i in range(len(result) - 1, -1, -1):
        if total <= budget:
            break
        msg = result[i]
        if i in keep:
            continue
        is_multipart = isinstance(msg, ToolMessage) and isinstance(msg.content, list)
        if isinstance(msg, ToolMessage) and (len(msg.text) > 200 or is_multipart):
            old_tokens = count_message_tokens(msg)
            # Last-resort truncation drops images along with excess text —
            # a multipart result becomes a plain truncated string even when
            # its text is short.
            result[i] = msg.model_copy(
                update={"content": msg.text[:200] + "\n[…truncated by synthesis_guard]", "compacted_by": LAYER}
            )
            total = total - old_tokens + count_message_tokens(result[i])
    return result, total
