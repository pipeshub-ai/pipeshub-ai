"""Tool hooks that tag each tool result for the request's ledger.

PRE_TOOL_USE gives each call that reaches the executor its tag, by the tool's
registered path, hands the ledger the call's arguments, and makes it the
running call, so what its writers note is attributed to it. POST_TOOL_USE runs only for a call that executed: a tool on
the asker's own credential restricts the answer from then on, a refused or
held call does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.agents.privacy.ledger import (
    LabelLedger,
    enter_tool_call,
    leave_tool_call,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agent_loop_lib.hooks.registry import HookRegistry

_CALL_KEY = "_label_call_id"


def _call_of(ctx: ToolCallContext | ToolResultContext) -> tuple[str, str]:
    call = getattr(getattr(ctx, "scope", None), "call", None)
    call_id = getattr(call, "id", None) or str(ctx.tool_use_id)
    return call_id, str(getattr(call, "name", "") or "")


def install_label_hooks(hooks: HookRegistry, ledger: LabelLedger) -> None:
    """Register the tagging hooks on `hooks`. Nothing is registered for a ledger that labels nothing."""
    if not ledger.labels:
        return
    from app.agent_loop_lib.hooks.events import HookEvent

    async def _started(ctx: ToolCallContext, next_fn: Next) -> None:
        call_id, name = _call_of(ctx)
        ctx.metadata[_CALL_KEY] = call_id
        try:
            ledger.tool_started(call_id, ctx.tool_path, name)
            # As the model gave them: this hook runs before any middleware that rewrites them.
            ledger.tool_arguments(call_id, ctx.tool_input or {})
        except Exception:
            ledger.untagged("tool")
        enter_tool_call(call_id)
        await next_fn()

    async def _finished(ctx: ToolResultContext, next_fn: Next) -> None:
        call_id = ctx.metadata.get(_CALL_KEY) or _call_of(ctx)[0]
        try:
            ledger.tool_finished(call_id)
        except Exception:
            ledger.untagged("tool")
        await next_fn()
        leave_tool_call()

    hooks.on(HookEvent.PRE_TOOL_USE).use(_started)
    hooks.on(HookEvent.POST_TOOL_USE).use(_finished)


__all__ = ["install_label_hooks"]
