"""`ApprovalHandler` port + bridge to the existing rich approval system.

The new tool pipeline only needs one thing from approval: "is this call
allowed to proceed?" (`request_approval(ctx) -> bool`). Everything else —
`RiskLevel` lookup, `ApprovalPolicy` resolution, session-scoped decision
caching, HIL submission — already exists and works
(`agent_loop.modules.stores.approval.hook.ApprovalHook` + `ApprovalStore`); this module
bridges the two rather than replacing the richer system with the simpler
port, per the migration plan's Phase 3b.

`require_approval` is the PRE_TOOL_USE middleware `ControlPlane` registers
when `"approval"` is in `cfg.hooks` — the direct replacement for the old
`ApprovalHookAdapter`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.modules.stores.approval.hook import ApprovalHook

__all__ = ["ApprovalHandler", "StorageBackedApprovalHandler", "require_approval"]


@runtime_checkable
class ApprovalHandler(Protocol):
    """Pluggable approval resolution port for `ToolCallContext`."""

    async def request_approval(self, ctx: ToolCallContext) -> bool:
        """Return True if the call may proceed."""
        ...


class StorageBackedApprovalHandler:
    """`ApprovalHandler` that delegates to the existing `ApprovalHook.check()`
    flow — `RiskLevel` comes from `Tool.risk_level` (itself a bridge over
    `Tag("risk", ...)`, see `agent_loop.tools.base.Tool.risk_level`), and
    `ApprovalPolicy`/session caching/HIL submission are all handled by the
    wrapped `ApprovalHook` unchanged.
    """

    def __init__(self, approval_hook: ApprovalHook) -> None:
        self._hook = approval_hook

    async def request_approval(self, ctx: ToolCallContext) -> bool:
        name = ctx.tool_path.rsplit("/", 1)[-1]
        call = ToolCall(id=str(ctx.tool_use_id), name=name, arguments=dict(ctx.tool_input))
        decision = await self._hook.check(call, session_id=ctx.session_id)
        if not decision.approved:
            ctx.metadata["approval_decision_id"] = decision.decision_id
        return decision.approved


def require_approval(handler: ApprovalHandler):
    """PRE_TOOL_USE middleware: deny the call unless `handler` approves it."""

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        approved = await handler.request_approval(ctx)
        if not approved:
            ctx.deny(f"tool call to {ctx.tool_path!r} was not approved")
            return
        await next_fn()

    return _middleware
