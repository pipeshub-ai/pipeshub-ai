from __future__ import annotations

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

"""Allowlist / denylist enforcement — the direct replacement for `PermissionHook`."""


def require_permission(allowlist: list[str] | None = None, denylist: list[str] | None = None):
    allowlist = list(allowlist) if allowlist is not None else None
    denylist = list(denylist) if denylist is not None else None

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        name = ctx.tool_path.rsplit("/", 1)[-1]
        if allowlist is not None:
            if name not in allowlist:
                ctx.deny(f"Tool '{name}' is not in the allowlist")
                return
        elif denylist is not None:
            if name in denylist:
                ctx.deny(f"Tool '{name}' is in the denylist")
                return
        await next_fn()

    return _middleware
