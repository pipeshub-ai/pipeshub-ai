from __future__ import annotations

from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

_MARKER = "\n[…truncated"
_OWN_SUFFIX = "by budget_reduction]"


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
            # Artifact-bearing messages have their full content safely stored
            # in the artifact store — truncate here like any other tool
            # result.  L2 artifact_compaction handles turn-aware replacement
            # with compact references on later turns; the model can call
            # retrieve_artifact_content for the full data.
            if len(msg.content) <= max_result_chars:
                shaped.append(msg)
                continue
            # Skip only messages already truncated by THIS shaper (avoid
            # double-truncation on re-runs).  Foreign truncation markers
            # (e.g. from retrieve_artifact_content) do NOT grant a pass —
            # those messages can still be far over max_result_chars.
            if _OWN_SUFFIX in msg.content:
                shaped.append(msg)
                continue
            truncated = (
                msg.content[:max_result_chars]
                + f"{_MARKER} {len(msg.content) - max_result_chars} chars {_OWN_SUFFIX}"
            )
            shaped.append(msg.model_copy(update={"content": truncated}))
        ctx.messages = shaped
        await next_fn()

    return _middleware
