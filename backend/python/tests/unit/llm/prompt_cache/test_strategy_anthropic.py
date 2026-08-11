"""`AnthropicCacheStrategy` (Phase 3, hardened): allocator-governed
breakpoint budget, a cumulative prefix-floor check (not a per-block
size check), two advancing message breakpoints, and gateway
stand-down.

Fixtures use a small `min_prefix_tokens` (10 tokens = 40 chars) rather
than a real model's floor (1,024+) so test content stays short and
readable; `test_capabilities.py` covers real per-model floor
resolution and `test_anthropic_cache_strategy_seam.py` proves the real
resolved capability wires correctly end to end through the transport.
"""

from __future__ import annotations

from app.agent_loop_lib.cache.base import CacheableRequest
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.strategy.anthropic import AnthropicCacheStrategy


def _capability(*, min_prefix_tokens: int = 10, max_breakpoints: int = 4) -> CacheCapability:
    return CacheCapability(
        mode="explicit",
        min_prefix_tokens=min_prefix_tokens,
        max_breakpoints=max_breakpoints,
        default_ttl="5m",
        extended_ttl="1h",
        write_multiplier=1.25,
        read_multiplier=0.10,
        can_cache_tools=True,
        can_cache_system=True,
    )


def _strategy(**kwargs: object) -> AnthropicCacheStrategy:
    return AnthropicCacheStrategy(_capability(**kwargs))


def _apply(strategy: AnthropicCacheStrategy, request: CacheableRequest):
    return strategy.apply(strategy.plan(request), request)


class TestPlan:
    def test_enabled_when_no_pre_existing_markers(self) -> None:
        plan = _strategy().plan(CacheableRequest(messages=[]))
        assert plan.enabled is True

    def test_disabled_on_gateway_standdown(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
            }
        ]
        plan = _strategy().plan(CacheableRequest(messages=messages))
        assert plan.enabled is False
        assert plan.reason == "gateway_standdown"


class TestMessageBreakpointsCumulativeFloor:
    def test_noop_when_two_or_fewer_messages(self) -> None:
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        request = CacheableRequest(messages=messages)
        result = _apply(_strategy(), request)
        assert "cache_control" not in result.messages[0]

    def test_final_two_messages_are_never_eligible_regardless_of_size(self) -> None:
        big = "x" * 200
        messages = [
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "assistant", "content": [{"type": "text", "text": big}]},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        for msg in result.messages:
            for block in msg["content"]:
                assert "cache_control" not in block

    def test_block_below_cumulative_floor_is_not_marked(self) -> None:
        # 10-char block, floor is 40 chars (10 tokens) — never clears.
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "content": "x" * 10}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert "cache_control" not in result.messages[0]["content"][0]

    def test_block_that_clears_cumulative_floor_is_marked(self) -> None:
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "content": "x" * 50}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert result.messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_small_block_becomes_eligible_once_cumulative_total_clears_floor(self) -> None:
        """A 10-char block alone never clears a 40-char floor, but once
        preceded by enough other content its CUMULATIVE prefix does —
        the whole point of the cumulative check over a per-block one."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "x" * 35}]},
            {"role": "user", "content": [{"type": "tool_result", "content": "y" * 10}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert result.messages[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_pre_existing_marker_on_any_block_makes_plan_stand_down(self) -> None:
        big = "x" * 50
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": big, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": big},
                ],
            },
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        plan = _strategy().plan(CacheableRequest(messages=messages))
        assert plan.enabled is False


class TestTwoAdvancingMessageBreakpoints:
    def test_marks_last_and_second_to_last_eligible_boundaries(self) -> None:
        big = "x" * 50
        messages = [
            {"role": "user", "content": [{"type": "text", "text": big}]},  # idx 0: eligible
            {"role": "user", "content": [{"type": "text", "text": big}]},  # idx 1: eligible
            {"role": "user", "content": [{"type": "text", "text": big}]},  # idx 2: eligible
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        # The two closest to the end (idx 1 and idx 2) are chosen, not idx 0.
        assert "cache_control" not in result.messages[0]["content"][0]
        assert result.messages[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert result.messages[2]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_only_one_eligible_boundary_marks_only_that_one(self) -> None:
        big = "x" * 50
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "tiny"}]},
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert "cache_control" not in result.messages[0]["content"][0]
        assert result.messages[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_does_not_mutate_the_input_messages(self) -> None:
        big = "x" * 50
        messages = [
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        _apply(_strategy(), CacheableRequest(messages=messages))
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    assert "cache_control" not in block


class TestBudgetExhaustionByHigherPriority:
    def test_tools_and_system_consuming_full_budget_leaves_no_message_breakpoints(self) -> None:
        big = "x" * 50
        messages = [
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "user", "content": [{"type": "text", "text": big}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        request = CacheableRequest(
            messages=messages,
            system=[{"type": "text", "text": big}],
            tools=[{"name": "a"}],
        )
        result = _apply(_strategy(max_breakpoints=2), request)
        assert result.tools[-1]["cache_control"] == {"type": "ephemeral"}
        assert result.system[0]["cache_control"] == {"type": "ephemeral"}
        for msg in result.messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    assert "cache_control" not in block


class TestSystemBreakpointFloor:
    def test_none_when_no_system(self) -> None:
        request = CacheableRequest(messages=[], system=None)
        result = _apply(_strategy(), request)
        assert result.system is None

    def test_below_floor_is_not_marked(self) -> None:
        system = [{"type": "text", "text": "short"}]
        result = _apply(_strategy(), CacheableRequest(messages=[], system=system))
        assert "cache_control" not in result.system[0]

    def test_above_floor_marks_first_block_only(self) -> None:
        system = [{"type": "text", "text": "x" * 50}, {"type": "text", "text": "volatile"}]
        result = _apply(_strategy(), CacheableRequest(messages=[], system=system))
        assert result.system[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in result.system[1]

    def test_does_not_mutate_input_system(self) -> None:
        system = [{"type": "text", "text": "x" * 50}]
        _apply(_strategy(), CacheableRequest(messages=[], system=system))
        assert "cache_control" not in system[0]


class TestToolBreakpoint:
    def test_none_when_no_tools(self) -> None:
        request = CacheableRequest(messages=[], tools=None)
        result = _apply(_strategy(), request)
        assert result.tools is None

    def test_marks_last_tool_without_mutating_original(self) -> None:
        tools = [{"name": "a"}, {"name": "b"}]
        request = CacheableRequest(messages=[], tools=tools)
        result = _apply(_strategy(), request)
        assert result.tools[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in tools[-1]
        assert "cache_control" not in result.tools[0]
