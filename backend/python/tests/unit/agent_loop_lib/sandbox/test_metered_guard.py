"""Tests for metered_sandbox_guard and its e2b_sandbox_guard alias."""

from __future__ import annotations


from app.agent_loop_lib.hooks.middleware.builtin.e2b_sandbox_guard import (
    e2b_sandbox_guard,
)
from app.agent_loop_lib.hooks.middleware.builtin.metered_sandbox_guard import (
    metered_sandbox_guard,
)


class FakeToolCallContext:
    def __init__(self, tool_input=None):
        self.tool_input = tool_input or {}
        self.denied = False
        self.deny_reason = None
        self.metadata = {}

    def deny(self, reason: str):
        self.denied = True
        self.deny_reason = reason


async def _next():
    pass


class TestMeteredSandboxGuard:
    async def test_allows_normal_timeout(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120)
        ctx = FakeToolCallContext(tool_input={"timeout": 30})
        await mw(ctx, _next)
        assert not ctx.denied

    async def test_denies_excessive_timeout(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120)
        ctx = FakeToolCallContext(tool_input={"timeout": 300})
        await mw(ctx, _next)
        assert ctx.denied
        assert "300" in ctx.deny_reason
        assert "120" in ctx.deny_reason

    async def test_cumulative_budget_is_enforced_before_overspending(self) -> None:
        """The budget has to be checked against what THIS call would add.

        Comparing only the already-accepted total lets the request that
        crosses the line through, so a 100s budget bought 120s of billed
        sandbox time — the cap is exceeded by up to one full request.
        """
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)

        ctx1 = FakeToolCallContext(tool_input={"timeout": 60})
        await mw(ctx1, _next)
        assert not ctx1.denied

        ctx2 = FakeToolCallContext(tool_input={"timeout": 60})
        await mw(ctx2, _next)
        assert ctx2.denied, "60 + 60 exceeds the 100s budget"
        assert "budget" in ctx2.deny_reason.lower()

    async def test_a_request_that_still_fits_is_allowed(self) -> None:
        """Denying pre-emptively must not deny everything once the budget is
        partly spent — a smaller call that fits still runs."""
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)

        await mw(FakeToolCallContext(tool_input={"timeout": 60}), _next)
        ctx2 = FakeToolCallContext(tool_input={"timeout": 30})
        await mw(ctx2, _next)
        assert not ctx2.denied

    async def test_a_denied_request_does_not_consume_budget(self) -> None:
        """A call that never ran must not be charged, or one oversized
        request would exhaust the budget for everything after it."""
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)

        denied = FakeToolCallContext(tool_input={"timeout": 110})
        await mw(denied, _next)
        assert denied.denied

        ctx = FakeToolCallContext(tool_input={"timeout": 90})
        await mw(ctx, _next)
        assert not ctx.denied, "the rejected 110s must not have been billed"

    async def test_no_cumulative_limit_by_default(self) -> None:
        mw = metered_sandbox_guard(max_timeout=1000)
        for _ in range(50):
            ctx = FakeToolCallContext(tool_input={"timeout": 100})
            await mw(ctx, _next)
            assert not ctx.denied

    async def test_e2b_alias_works(self) -> None:
        mw = e2b_sandbox_guard(max_timeout=60)
        ctx_ok = FakeToolCallContext(tool_input={"timeout": 30})
        await mw(ctx_ok, _next)
        assert not ctx_ok.denied

        ctx_deny = FakeToolCallContext(tool_input={"timeout": 120})
        await mw(ctx_deny, _next)
        assert ctx_deny.denied

    async def test_non_numeric_timeout_is_not_a_free_pass(self) -> None:
        """An unusable `timeout` still runs the sandbox at its default, so it
        has to be charged at that rate rather than treated as free."""
        mw = metered_sandbox_guard(max_timeout=60, max_cumulative_s=100, default_timeout=30)
        ctx = FakeToolCallContext(tool_input={"timeout": "fast"})
        await mw(ctx, _next)
        assert not ctx.denied

        ctx2 = FakeToolCallContext(tool_input={"timeout": 50})
        await mw(ctx2, _next)
        assert not ctx2.denied  # 30 + 50 = 80, still within 100

        ctx3 = FakeToolCallContext(tool_input={"timeout": 50})
        await mw(ctx3, _next)
        assert ctx3.denied, "80 + 50 exceeds the budget"

    async def test_omitting_timeout_does_not_bypass_the_budget(self) -> None:
        """The hole this closes: a call with no `timeout` argument was
        charged 0, so an agent that never sets one could run unlimited
        billed sandbox time against a configured cap. The tool substitutes
        its default and the provider bills for it, so the guard must too.
        """
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100, default_timeout=30)

        allowed = 0
        for _ in range(20):
            ctx = FakeToolCallContext(tool_input={})
            await mw(ctx, _next)
            if ctx.denied:
                break
            allowed += 1

        assert allowed <= 4, f"{allowed} un-timed calls ran against a 100s budget"


class TestMalformedTimeoutCannotCorruptTheBudget:
    """The budget is a running float the model can influence. Anything that
    reaches the accumulator unvalidated can break it permanently:

    - a NEGATIVE timeout subtracts, so repeated calls drive the total down
      and buy back unlimited time;
    - NaN compares False against everything, so it passes both gates and then
      makes `total` NaN forever — every later comparison is False and the cap
      is silently gone;
    - -inf does the same, permanently.

    `json.loads` accepts `NaN`/`-Infinity` literals, so these arrive through
    ordinary tool arguments rather than requiring anything exotic.
    """

    async def test_negative_timeout_is_rejected(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": -1000})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_negative_timeout_does_not_refund_the_budget(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)

        await mw(FakeToolCallContext(tool_input={"timeout": 90}), _next)
        for _ in range(3):
            await mw(FakeToolCallContext(tool_input={"timeout": -1000}), _next)

        # 90s is spent; a 90s call must not fit in the remaining 10s.
        ctx = FakeToolCallContext(tool_input={"timeout": 90})
        await mw(ctx, _next)
        assert ctx.denied, "negative timeouts bought back budget"

    async def test_zero_timeout_is_rejected(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": 0})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_nan_timeout_is_rejected(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": float("nan")})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_nan_does_not_disable_the_budget_for_later_calls(self) -> None:
        """The severe one: NaN in the accumulator is permanent, so every
        later check silently passes."""
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        await mw(FakeToolCallContext(tool_input={"timeout": float("nan")}), _next)

        allowed = 0
        for _ in range(10):
            ctx = FakeToolCallContext(tool_input={"timeout": 120})
            await mw(ctx, _next)
            if not ctx.denied:
                allowed += 1

        assert allowed == 0, f"{allowed} of 10 over-budget calls ran after a NaN"

    async def test_negative_infinity_is_rejected(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": float("-inf")})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_negative_infinity_does_not_disable_the_budget(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        await mw(FakeToolCallContext(tool_input={"timeout": float("-inf")}), _next)

        ctx = FakeToolCallContext(tool_input={"timeout": 120})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_positive_infinity_is_still_rejected(self) -> None:
        """Already caught by max_timeout; pinned so it stays caught."""
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": float("inf")})
        await mw(ctx, _next)
        assert ctx.denied

    async def test_the_deny_reason_says_what_is_wrong(self) -> None:
        """The model has to be able to correct the call from the message."""
        mw = metered_sandbox_guard(max_timeout=120)
        ctx = FakeToolCallContext(tool_input={"timeout": -5})
        await mw(ctx, _next)
        assert "positive" in ctx.deny_reason.lower()

    async def test_a_rejected_value_never_reaches_the_accumulator(self) -> None:
        """Even with no budget configured, a bad value must not run."""
        ran = {"n": 0}

        async def _counting_next() -> None:
            ran["n"] += 1

        mw = metered_sandbox_guard(max_timeout=120)
        for bad in (-1, 0, float("nan"), float("-inf"), float("inf")):
            ctx = FakeToolCallContext(tool_input={"timeout": bad})
            await mw(ctx, _counting_next)
            assert ctx.denied, bad
        assert ran["n"] == 0
