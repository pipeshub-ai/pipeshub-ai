from __future__ import annotations

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

"""`e2b_sandbox_guard`: a PRE_TOOL_USE middleware layer of E2B-specific
billing/timeout guards — registered on `/toolsets/coding_sandbox/**`,
auto-added by `ControlPlane` only when `coding_sandbox.backend == "e2b"` so
`backend="local"` users pay zero overhead.

NOT a replacement for `coding_sandbox_safety` (destructive-code/package
pattern detection stays in effect for every backend) — this layers
E2B-only concerns on top via the same PRE_TOOL_USE pipeline:

    - E2B bills per sandbox-second, so an agent-supplied `timeout` far
      above what any real workload needs is a billing-spike risk (accidental
      `timeout=3600`, or an adversarial prompt trying to run up cost) —
      capped at `max_timeout`.
    - `max_cumulative_s`, when configured, tracks total sandbox-seconds
      requested across `run_code`/`install_packages` calls in this
      middleware's lifetime (i.e. one ControlPlane instance) and denies
      further calls once the budget is exhausted — a coarse, pre-emptive
      check (based on requested `timeout`, not actual measured duration,
      since POST_TOOL_USE doesn't have per-call wall-clock time available
      here) rather than a precise billing meter.
"""

__all__ = ["e2b_sandbox_guard"]


def e2b_sandbox_guard(max_timeout: float = 120.0, max_cumulative_s: float | None = None):
    """PRE_TOOL_USE middleware factory for the coding sandbox toolset,
    scoped to `backend="e2b"`.

    Args:
        max_timeout: deny any `timeout` argument above this many seconds.
        max_cumulative_s: optional running budget (in seconds) of
            cumulative requested `timeout` across calls; once exceeded,
            further coding-sandbox tool calls are denied. `None` (default)
            means unlimited.
    """
    cumulative = {"total": 0.0}

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        timeout = ctx.tool_input.get("timeout")
        if isinstance(timeout, (int, float)):
            if timeout > max_timeout:
                ctx.deny(f"timeout {timeout}s exceeds the configured E2B max of {max_timeout}s")
                return
            requested = float(timeout)
        else:
            requested = 0.0

        if max_cumulative_s is not None and cumulative["total"] > max_cumulative_s:
            ctx.deny("E2B cumulative sandbox time budget exhausted for this session")
            return

        cumulative["total"] += requested
        await next_fn()

    return _middleware
