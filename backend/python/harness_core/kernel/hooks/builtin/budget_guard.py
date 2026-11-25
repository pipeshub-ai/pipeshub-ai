# pyright: basic
from __future__ import annotations

from harness_core.kernel.budget.base import BudgetManager
from harness_core.kernel.core.exceptions import BudgetExceeded
from harness_core.kernel.hooks.middleware.context import ToolCallContext

"""Blocks tool calls when budget is exceeded — the direct replacement for `BudgetGuardHook`."""


def require_budget(budget_manager: BudgetManager):
    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        metadata = getattr(ctx, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("final_answer"):
            # The answer-only turn after the limit (BUDGETEND): it answers from what was gathered, never refused.
            await next_fn()
            return
        try:
            await budget_manager.check()
        except BudgetExceeded as exc:
            if isinstance(metadata, dict):
                metadata["budget_exhausted"] = True
            ctx.deny(str(exc))
            return
        await next_fn()

    return _middleware
