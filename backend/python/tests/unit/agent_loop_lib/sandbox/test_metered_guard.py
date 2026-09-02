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

    async def test_cumulative_budget_exhausted(self) -> None:
        mw = metered_sandbox_guard(max_timeout=120, max_cumulative_s=100)

        ctx1 = FakeToolCallContext(tool_input={"timeout": 60})
        await mw(ctx1, _next)
        assert not ctx1.denied

        ctx2 = FakeToolCallContext(tool_input={"timeout": 60})
        await mw(ctx2, _next)
        assert not ctx2.denied

        ctx3 = FakeToolCallContext(tool_input={"timeout": 60})
        await mw(ctx3, _next)
        assert ctx3.denied
        assert "budget" in ctx3.deny_reason.lower()

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

    async def test_non_numeric_timeout_ignored(self) -> None:
        mw = metered_sandbox_guard(max_timeout=60, max_cumulative_s=100)
        ctx = FakeToolCallContext(tool_input={"timeout": "fast"})
        await mw(ctx, _next)
        assert not ctx.denied

        ctx2 = FakeToolCallContext(tool_input={"timeout": 50})
        await mw(ctx2, _next)
        assert not ctx2.denied
