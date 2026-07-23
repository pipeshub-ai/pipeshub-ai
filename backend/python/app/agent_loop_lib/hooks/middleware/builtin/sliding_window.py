from __future__ import annotations

from app.agent_loop_lib.core.tokens import count_message_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def shape_sliding_window(pin_first_n: int = 1):
    """Layer 4 context shaper: drops the oldest non-pinned messages until the
    call fits under `budget.max_tokens`.

    This is the PRE_MODEL equivalent of `context/window.py`'s
    `SlidingWindowContext` — same eviction policy (oldest-first, SYSTEM
    always pinned), but expressed as a shaper so it composes with the other
    layers and only runs on the outgoing call rather than mutating stored
    history. `pin_first_n` additionally protects the goal/task-setup
    messages at the start of the conversation, which are usually small but
    important context the model shouldn't lose. Direct replacement for
    `SlidingWindowHook`.

    Evicts tool-call groups atomically: an ``AssistantMessage`` with
    ``tool_calls`` is always evicted together with its matching
    ``ToolMessage``s to keep the pairing that LLM providers require.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        messages = ctx.messages
        if not messages:
            await next_fn()
            return

        pinned = set(range(min(pin_first_n, len(messages))))
        for i, msg in enumerate(messages):
            if msg.role == MessageRole.SYSTEM or getattr(msg, "pinned", False):
                pinned.add(i)

        working = list(messages)
        index_map = list(range(len(messages)))

        def total_tokens() -> int:
            return sum(count_message_tokens(m) for m in working)

        while total_tokens() > ctx.budget.effective_max_tokens and len(working) > 1:
            evict_at = _find_evictable(working, index_map, pinned)
            if evict_at is None:
                break
            _evict_group(working, index_map, evict_at)

        ctx.messages = working
        await next_fn()

    return _middleware


def _find_evictable(
    working: list, index_map: list[int], pinned: set[int]
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


def _evict_group(working: list, index_map: list[int], evict_at: int) -> None:
    """Remove the message at *evict_at* and, if it is an AssistantMessage
    with tool_calls, also remove the immediately following ToolMessages
    whose ``tool_call_id`` matches one of its calls."""
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
        del working[evict_at:end]
        del index_map[evict_at:end]
    else:
        working.pop(evict_at)
        index_map.pop(evict_at)
