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
        index_map = list(range(len(messages)))  # working[i] came from messages[index_map[i]]

        def total_tokens() -> int:
            return sum(count_message_tokens(m) for m in working)

        while total_tokens() > ctx.budget.max_tokens and len(working) > 1:
            evict_at = None
            for i in range(len(working)):
                if index_map[i] not in pinned:
                    evict_at = i
                    break
            if evict_at is None:
                break
            working.pop(evict_at)
            index_map.pop(evict_at)

        ctx.messages = working
        await next_fn()

    return _middleware
