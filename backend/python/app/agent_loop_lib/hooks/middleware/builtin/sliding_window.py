from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.context.protection import current_request_index, protected_indices
from app.agent_loop_lib.context.watermark import watermarks
from app.agent_loop_lib.core.tokens import count_message_tokens, count_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

if TYPE_CHECKING:
    from app.agent_loop_lib.context.compaction_memo import CompactionMemo
    from app.agent_loop_lib.core.types import Message

logger = logging.getLogger(__name__)

LAYER = "sliding_window"


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
    The newest tool-call group of the current request goes last of all,
    protected or not: it holds the result the model is waiting for, and
    budget reduction sized it to fit on its own under the low-water mark.
    Ranking it with the other unprotected groups evicted a search made after
    a fetch while the older fetch was sent.

    Once it has to evict, it evicts down to the low-water mark below the
    budget in one pass (``context/watermark.py``), and every message it
    evicted stays evicted on the later calls of the run
    (``context/compaction_memo.py``), so those calls only append instead of
    each dropping one more message from the front.

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
        memo = ctx.compaction_memo
        never = protected_indices(messages)
        newest = _newest_call_group(messages)
        working, index_map = _replay_evictions(memo, messages, never)
        replayed = len(messages) - len(working)
        before = count_tokens(messages)
        total = count_tokens(working)
        low_water = watermarks(ctx.budget, budget).low_water
        if total > budget:
            total = _evict_until(working, index_map, total, low_water,
                                 protected_indices(messages, protected_tools=protected_tools) | newest)
            evicted_unprotected = len(messages) - len(working) - replayed
            if total > budget and protected_tools:
                total = _evict_until(working, index_map, total, low_water, never | newest)
                evicted_protected = len(messages) - len(working) - replayed - evicted_unprotected
                if evicted_protected:
                    logger.warning(
                        "sliding_window: evicted %d protected tool message(s) after every unprotected one "
                        "to fit the budget, %d tokens (run=%s, turn=%d, budget=%d)",
                        evicted_protected, total, ctx.run_id, ctx.turn_index, budget,
                    )
            if total > budget and newest:
                total = _evict_until(working, index_map, total, low_water, never)
            if memo is not None:
                kept = set(index_map)
                for i, msg in enumerate(messages):
                    if i not in kept and memo.recall(LAYER, msg) is None:
                        memo.remember(LAYER, [msg], [])

        if len(working) < len(messages):
            logger.info(
                "sliding_window: evicted %d oldest message(s) (%d again from earlier calls), %d→%d tokens "
                "(low_water=%d, run=%s, turn=%d, budget=%d)",
                len(messages) - len(working), replayed, before, total, low_water,
                ctx.run_id, ctx.turn_index, budget,
            )
        ctx.messages = working
        await next_fn()

    return _middleware


def _newest_call_group(messages: list[Message]) -> frozenset[int]:
    """Indices of the last tool-call group after the current request: the
    assistant message that made the calls and their results."""
    current = current_request_index(messages)
    stop = current if current is not None else -1
    for i in range(len(messages) - 1, stop, -1):
        calls = getattr(messages[i], "tool_calls", None)
        if messages[i].role != MessageRole.ASSISTANT or not calls:
            continue
        call_ids = {call.id for call in calls}
        group = {i}
        j = i + 1
        while j < len(messages) and messages[j].role == MessageRole.TOOL:
            if getattr(messages[j], "tool_call_id", None) in call_ids:
                group.add(j)
            j += 1
        return frozenset(group)
    return frozenset()


def _replay_evictions(
    memo: CompactionMemo | None, messages: list[Message], never: frozenset[int],
) -> tuple[list[Message], list[int]]:
    """``messages`` without those an earlier call of the run evicted, and the
    original index of each message kept. An evicted message stays evicted,
    so the calls after a batch eviction only append.

    Replayed by group: an evicted tool call takes its results with it, and a
    result is dropped on its own only when its call is gone, so a result a
    layer above rewrote since never loses its call."""
    working = list(messages)
    index_map = list(range(len(messages)))
    if memo is None or not memo.has_compacted(LAYER):
        return working, index_map
    i = 0
    while i < len(working):
        msg = working[i]
        if index_map[i] in never or memo.recall(LAYER, msg) != ():
            i += 1
            continue
        if msg.role == MessageRole.TOOL and _has_call(working, i):
            i += 1
            continue
        _evict_group(working, index_map, i)
    return working, index_map


def _has_call(working: list[Message], index: int) -> bool:
    """Whether the tool result at ``index`` follows the call that asked for it."""
    call_id = getattr(working[index], "tool_call_id", None)
    j = index - 1
    while j >= 0 and working[j].role == MessageRole.TOOL:
        j -= 1
    calls = getattr(working[j], "tool_calls", None) if j >= 0 else None
    return bool(calls) and any(call.id == call_id for call in calls)


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
