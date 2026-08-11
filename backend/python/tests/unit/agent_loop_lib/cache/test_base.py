"""`agent_loop_lib.cache.base` is the injection seam itself: a Protocol
plus data types and the library's own inert default. These tests pin
`NoopStrategy`'s "adds exactly zero keys, never mutates" contract,
which every real strategy's stand-down path (Phase 3+) also has to
satisfy.
"""

from __future__ import annotations

from app.agent_loop_lib.cache.base import (
    ApplyResult,
    CacheableRequest,
    CachePlan,
    NoopStrategy,
    PromptCacheStrategy,
)


class TestNoopStrategy:
    def test_plan_is_always_disabled(self) -> None:
        strategy = NoopStrategy()
        request = CacheableRequest(messages=[{"role": "user", "content": "hi"}])
        plan = strategy.plan(request)
        assert plan.enabled is False

    def test_apply_returns_payload_unchanged(self) -> None:
        strategy = NoopStrategy()
        messages = [{"role": "user", "content": "hi"}]
        system = [{"type": "text", "text": "be helpful"}]
        tools = [{"name": "search"}]
        request = CacheableRequest(messages=messages, system=system, tools=tools)
        plan = strategy.plan(request)

        result = strategy.apply(plan, request)

        assert result.messages == messages
        assert result.system == system
        assert result.tools == tools
        assert result.request_kwargs == {}

    def test_satisfies_prompt_cache_strategy_protocol(self) -> None:
        strategy: PromptCacheStrategy = NoopStrategy()
        assert isinstance(strategy, PromptCacheStrategy)


class TestDataclasses:
    def test_cache_plan_defaults(self) -> None:
        plan = CachePlan(enabled=False)
        assert plan.reason == ""
        assert plan.extra == {}

    def test_apply_result_defaults(self) -> None:
        result = ApplyResult(messages=[])
        assert result.system is None
        assert result.tools is None
        assert result.request_kwargs == {}
