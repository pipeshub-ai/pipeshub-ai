from __future__ import annotations

from app.agent_loop_lib.core.exceptions import BudgetExceeded
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.modules.providers.budget.base import BudgetManager

"""Blocks tool calls when budget is exceeded — the direct replacement for `BudgetGuardHook`."""


def require_budget(budget_manager: BudgetManager):
    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        try:
            await budget_manager.check()
        except BudgetExceeded as exc:
            ctx.deny(str(exc))
            return
        await next_fn()

    return _middleware
