"""`BreakpointAllocator` — pure budget/priority tests, independent of
any provider payload shape. Anthropic's 4-breakpoint hard cap is
exercised by direct `max_breakpoints` values rather than real
`CacheCapability` objects; `test_strategy_anthropic.py` covers the
provider-shaped integration.
"""

from __future__ import annotations

from app.llm.prompt_cache.allocator import BreakpointAllocator, estimate_tokens


class TestEstimateTokens:
    def test_chars_per_token_approx_is_four(self) -> None:
        assert estimate_tokens(400) == 100

    def test_floor_division_rounds_down(self) -> None:
        assert estimate_tokens(7) == 1


class TestNeverExceedsMaxBreakpoints:
    def test_full_request_within_budget(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            has_tools=True, system_clears_floor=True, eligible_message_boundaries=2,
        )
        total = int(allocation.tools) + int(allocation.system) + allocation.message_breakpoints
        assert total == 4

    def test_budget_of_one_grants_only_the_highest_priority_slot(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=1).allocate(
            has_tools=True, system_clears_floor=True, eligible_message_boundaries=2,
        )
        assert allocation.tools is True
        assert allocation.system is False
        assert allocation.message_breakpoints == 0

    def test_zero_budget_grants_nothing(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=0).allocate(
            has_tools=True, system_clears_floor=True, eligible_message_boundaries=2,
        )
        assert allocation.tools is False
        assert allocation.system is False
        assert allocation.message_breakpoints == 0


class TestExistingBreakpointsCountAgainstBudget:
    def test_pre_existing_markers_reduce_available_budget(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            existing_breakpoints=3, has_tools=True, system_clears_floor=True,
            eligible_message_boundaries=2,
        )
        total = int(allocation.tools) + int(allocation.system) + allocation.message_breakpoints
        assert total == 1

    def test_existing_breakpoints_at_or_above_cap_grants_nothing(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            existing_breakpoints=4, has_tools=True, system_clears_floor=True,
            eligible_message_boundaries=2,
        )
        assert allocation.tools is False
        assert allocation.system is False
        assert allocation.message_breakpoints == 0

    def test_existing_breakpoints_above_cap_does_not_go_negative(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            existing_breakpoints=10, has_tools=True, system_clears_floor=True,
            eligible_message_boundaries=2,
        )
        assert allocation.message_breakpoints == 0


class TestSystemSlotSkippedBelowFloor:
    def test_system_not_granted_when_it_does_not_clear_the_floor(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            has_tools=False, system_clears_floor=False, eligible_message_boundaries=2,
        )
        assert allocation.system is False
        # Budget freed up by skipping system flows to message breakpoints.
        assert allocation.message_breakpoints == 2


class TestPriorityOrder:
    def test_tools_beats_system_beats_messages_under_tight_budget(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=2).allocate(
            has_tools=True, system_clears_floor=True, eligible_message_boundaries=5,
        )
        assert allocation.tools is True
        assert allocation.system is True
        assert allocation.message_breakpoints == 0


class TestMessageBreakpointCapAtTwo:
    def test_never_grants_more_than_two_message_breakpoints_even_with_full_budget(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            has_tools=False, system_clears_floor=False, eligible_message_boundaries=10,
        )
        assert allocation.message_breakpoints == 2

    def test_caller_can_lower_the_message_cap(self) -> None:
        allocation = BreakpointAllocator(max_breakpoints=4).allocate(
            has_tools=False, system_clears_floor=False, eligible_message_boundaries=10,
            max_message_breakpoints=1,
        )
        assert allocation.message_breakpoints == 1
