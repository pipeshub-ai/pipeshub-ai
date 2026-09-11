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

import math

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

__all__ = ["metered_sandbox_guard"]


def metered_sandbox_guard(
    max_timeout: float = 120.0,
    max_cumulative_s: float | None = None,
    default_timeout: float = 30.0,
):
    """PRE_TOOL_USE middleware factory for the coding sandbox toolset,
    applicable to any metered backend (``SandboxCapabilities.is_metered``).

    Args:
        max_timeout: deny any ``timeout`` argument above this many seconds.
        max_cumulative_s: optional running budget (in seconds) of
            cumulative requested ``timeout`` across calls. A call is denied
            when it would take the total PAST the budget, not after it
            already has. ``None`` (default) means unlimited.
        default_timeout: what to charge a call that omits ``timeout`` or
            passes a non-numeric one. A numeric but INVALID timeout
            (negative, zero, NaN, infinite) is denied instead — it is a
            malformed argument the model should correct, not a missing one
            to substitute a default for. The tool substitutes its own default
            and the provider bills for that time, so charging zero here
            would let an agent that never sets a timeout run unbounded
            billed time against a configured budget.
    """
    cumulative = {"total": 0.0}

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        timeout = ctx.tool_input.get("timeout")
        if isinstance(timeout, (int, float)):
            # Validated BEFORE any comparison or accounting. The budget is a
            # running float the model can steer, and an unchecked value
            # breaks it permanently rather than just for this call: a
            # negative timeout SUBTRACTS, buying back time already spent,
            # and NaN/-inf compare False against everything, so they slip
            # past both gates and then make the total NaN/-inf forever —
            # every later check silently passes and the cap is gone. `+inf`
            # happens to be caught by `max_timeout` below, but relying on
            # that would leave the other two open.
            if not math.isfinite(timeout) or timeout <= 0:
                ctx.deny(
                    f"timeout must be a positive, finite number of seconds; "
                    f"got {timeout!r}"
                )
                return
            if timeout > max_timeout:
                ctx.deny(
                    f"timeout {timeout}s exceeds the configured max of "
                    f"{max_timeout}s for this metered sandbox backend"
                )
                return
            requested = float(timeout)
        else:
            # No usable timeout given: the tool falls back to its own
            # default and the sandbox runs (and bills) for that long.
            requested = default_timeout

        # Checked against what THIS call would add. Comparing only the
        # already-spent total lets the request that crosses the line
        # through, overshooting the cap by up to one full request.
        if max_cumulative_s is not None and cumulative["total"] + requested > max_cumulative_s:
            ctx.deny(
                f"this call would need {requested:g}s and only "
                f"{max(0.0, max_cumulative_s - cumulative['total']):g}s of the "
                f"{max_cumulative_s:g}s sandbox time budget is left for this session"
            )
            return

        # Charged only once the call is going to run — a denied request
        # never reaches the provider, so billing it would exhaust the
        # budget for calls that would have fit.
        cumulative["total"] += requested
        await next_fn()

    return _middleware
