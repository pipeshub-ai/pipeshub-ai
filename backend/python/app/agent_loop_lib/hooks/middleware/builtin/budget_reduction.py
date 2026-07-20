from __future__ import annotations

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_MARKER = "\n[…truncated"


def shape_budget_reduction(max_result_chars: int = 64_000):
    """Layer 1 (cheapest) context shaper: caps every individual TOOL message's
    content at `max_result_chars`.

    Registered on `HookRegistry.on(HookEvent.PRE_MODEL)` — a pure reducer,
    runs unconditionally regardless of total context size, because a single
    oversized tool result (e.g. a full page scrape) is worth capping on its
    own merits. Direct replacement for `BudgetReductionHook`.
    """

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        shaped = []
        for msg in ctx.messages:
            if msg.role != MessageRole.TOOL or not isinstance(msg.content, str):
                shaped.append(msg)
                continue
            # Already shaped by a previous pass (or another instance) —
            # leave as-is so re-running the pipeline is idempotent.
            if _MARKER in msg.content or len(msg.content) <= max_result_chars:
                shaped.append(msg)
                continue
            truncated = (
                msg.content[:max_result_chars]
                + f"{_MARKER} {len(msg.content) - max_result_chars} chars by budget_reduction]"
            )
            shaped.append(msg.model_copy(update={"content": truncated}))
        ctx.messages = shaped
        await next_fn()

    return _middleware
