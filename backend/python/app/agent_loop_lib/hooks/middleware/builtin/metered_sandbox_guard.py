"""``metered_sandbox_guard``: a PRE_TOOL_USE middleware layer for
billing/timeout guards on ANY metered sandbox backend — registered on
``/toolsets/coding_sandbox/**``, auto-added by factories whose
``SandboxCapabilities.is_metered`` is True.

Generalisation of the former E2B-only ``e2b_sandbox_guard``: same logic
(cap ``timeout``, optional cumulative sandbox-second budget), but
provider-neutral — keyed off ``SandboxCapabilities.is_metered`` rather
than a hard ``backend == "e2b"`` check.

NOT a replacement for ``coding_sandbox_safety`` (destructive-code/package
pattern detection stays in effect for every backend) — this layers
metered-backend concerns on top via the same PRE_TOOL_USE pipeline.
"""

from __future__ import annotations

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

__all__ = ["metered_sandbox_guard"]


def metered_sandbox_guard(
    max_timeout: float = 120.0,
    max_cumulative_s: float | None = None,
):
    """PRE_TOOL_USE middleware factory for the coding sandbox toolset,
    applicable to any metered backend (``SandboxCapabilities.is_metered``).

    Args:
        max_timeout: deny any ``timeout`` argument above this many seconds.
        max_cumulative_s: optional running budget (in seconds) of
            cumulative requested ``timeout`` across calls; once exceeded,
            further coding-sandbox tool calls are denied. ``None`` (default)
            means unlimited.
    """
    cumulative = {"total": 0.0}

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        timeout = ctx.tool_input.get("timeout")
        if isinstance(timeout, (int, float)):
            if timeout > max_timeout:
                ctx.deny(
                    f"timeout {timeout}s exceeds the configured max of "
                    f"{max_timeout}s for this metered sandbox backend"
                )
                return
            requested = float(timeout)
        else:
            requested = 0.0

        if max_cumulative_s is not None and cumulative["total"] > max_cumulative_s:
            ctx.deny(
                "cumulative sandbox time budget exhausted for this session"
            )
            return

        cumulative["total"] += requested
        await next_fn()

    return _middleware
