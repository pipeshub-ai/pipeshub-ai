from __future__ import annotations

import logging

from app.agent_loop_lib.context.protection import protected_indices
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)


def shape_sliding_window(protected_tools: frozenset[str] = frozenset()):
    """Context shaper of last resort before the synthesis guard: drops the
    oldest unprotected messages until the call fits under the budget.

    This is the PRE_MODEL equivalent of `context/window.py`'s
    `SlidingWindowContext` — same eviction policy (oldest-first), but expressed
    as a shaper so it composes with the other layers and only runs on the
    outgoing call rather than mutating stored history. It runs after the
    extractive and summarising layers, so it evicts only what they could not
    shrink.

    It never evicts the current request, pinned or system messages
    (`context/protection.py`). Tool groups that called one of
    `protected_tools` are evicted only when nothing unprotected is left and
    the call still does not fit, oldest first, so the budget stays reachable.

    Evicts tool-call groups atomically: an ``AssistantMessage`` with
    ``tool_calls`` is always evicted together with its matching
    ``ToolMessage``s to keep the pairing that LLM providers require.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = ctx.messages
        if not messages:
            await next_fn()
            return

        budget = ctx.budget.effective_max_tokens
        working = list(messages)
        index_map = list(range(len(messages)))
        before = total = count_tokens(working)
        total = _evict_until(working, index_map, total, budget,
                             protected_indices(messages, protected_tools=protected_tools))
        evicted_unprotected = len(messages) - len(working)
        if total > budget and protected_tools:
            total = _evict_until(working, index_map, total, budget, protected_indices(messages))
            evicted_protected = len(messages) - len(working) - evicted_unprotected
            if evicted_protected:
                logger.warning(
                    "sliding_window: evicted %d protected tool message(s) after every unprotected one "
                    "to fit the budget, %d tokens (run=%s, turn=%d, budget=%d)",
                    evicted_protected, total, ctx.run_id, ctx.turn_index, budget,
                )

        if len(working) < len(messages):
            logger.info(
                "sliding_window: evicted %d oldest message(s), %d→%d tokens (run=%s, turn=%d, budget=%d)",
                len(messages) - len(working), before, total, ctx.run_id, ctx.turn_index, budget,
            )
        ctx.messages = working
        await next_fn()

    return _middleware


def _evict_until(
    working: list, index_map: list[int], total: int, budget: int, pinned: frozenset[int],
) -> int:
    """Evict the oldest groups outside ``pinned`` from ``working`` (in place)
    until ``total`` fits ``budget``; returns the new total."""
    while total > budget and len(working) > 1:
        evict_at = _find_evictable(working, index_map, pinned)
        if evict_at is None:
            break
        total -= _evict_group(working, index_map, evict_at)
    return total


def _find_evictable(
    working: list, index_map: list[int], pinned: frozenset[int]
) -> int | None:
    """Find the oldest non-pinned, non-ToolMessage index to evict.

    ToolMessages are skipped here because they must be evicted together
    with their parent AssistantMessage (see ``_evict_group``).  If the
    only remaining non-pinned messages are orphaned ToolMessages, fall
    back to evicting the first one.
    """
    first_tool_fallback: int | None = None
    for i in range(len(working)):
        if index_map[i] in pinned:
            continue
        if working[i].role == MessageRole.TOOL:
            if first_tool_fallback is None:
                first_tool_fallback = i
            continue
        return i
    return first_tool_fallback


def _evict_group(working: list, index_map: list[int], evict_at: int) -> int:
    """Remove the message at *evict_at* and, if it is an AssistantMessage
    with tool_calls, also remove the immediately following ToolMessages
    whose ``tool_call_id`` matches one of its calls. Returns the tokens
    removed."""
    msg = working[evict_at]
    if (
        msg.role == MessageRole.ASSISTANT
        and getattr(msg, "tool_calls", None)
    ):
        call_ids = {tc.id for tc in msg.tool_calls}
        end = evict_at + 1
        while end < len(working):
            candidate = working[end]
            if candidate.role != MessageRole.TOOL:
                break
            if getattr(candidate, "tool_call_id", None) not in call_ids:
                break
            end += 1
        removed = count_tokens(working[evict_at:end])
        del working[evict_at:end]
        del index_map[evict_at:end]
        return removed
    index_map.pop(evict_at)
    return count_message_tokens(working.pop(evict_at))
