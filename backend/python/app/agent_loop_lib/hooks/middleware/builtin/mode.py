from __future__ import annotations

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.modules.stores.approval.base import RiskLevel

# Harness/meta tools stay callable in plan mode regardless of risk —
# they're how the model asks questions, finishes, or discovers/loads more
# tools, not how it mutates anything.
_ALWAYS_ALLOWED_IN_PLAN_MODE = {"clarify", "task_complete", "list_toolsets", "fetch_tools"}


def _risk_from_tags(tags: tuple) -> RiskLevel:
    for tag in tags:
        if tag.key == "risk":
            return RiskLevel(tag.value)
    return RiskLevel.LOW


def enforce_mode(mode: str):
    """Pairs `AgentConfig.mode` with real enforcement — the prompt section
    in roles/prompt_template.py only tells the model it's in plan mode; THIS
    middleware is the actual boundary, per the design tenet that mode never
    bypasses hooks. In "plan" mode, any tool call whose effective tags carry
    `Tag("risk", ...)` above LOW is blocked (see `ToolExecutor`, which
    attaches the resolved tool's effective tags onto `ctx.tags` before
    dispatch); "act" and "auto_approve" impose no extra restriction here
    (auto_approve's only difference is skipping human approval prompts,
    which is `require_approval`'s concern).

    Direct replacement for `ModeHook`.
    """

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        if mode != "plan":
            await next_fn()
            return
        name = ctx.tool_path.rsplit("/", 1)[-1]
        if name in _ALWAYS_ALLOWED_IN_PLAN_MODE:
            await next_fn()
            return
        risk = _risk_from_tags(ctx.tags)
        if risk != RiskLevel.LOW:
            ctx.deny(
                f"Tool '{name}' (risk={risk.value}) is blocked in plan mode — "
                "plan mode is read-only; switch to act mode to execute it."
            )
            return
        await next_fn()

    return _middleware
