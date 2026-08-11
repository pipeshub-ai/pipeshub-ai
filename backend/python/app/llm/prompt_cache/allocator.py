"""`BreakpointAllocator` — providers with a hard cap on the number of
`cache_control` markers per request (Anthropic: 4) need the same "how
many slots, in what priority" decision regardless of which provider;
only WHERE inside the payload a slot lands is provider-specific (that
stays in `strategy/*.py`). Exceeding Anthropic's cap is a hard 400
("A maximum of 4 blocks with cache_control may be provided"), so the
cap is enforced by construction here, not by hoping a call site's
markers happen to add up to the limit.

Priority order (highest value per slot first), per the plan:
  1. tool schemas   - largest stable mass, changes only on fetch_tools
  2. stable system  - skipped when the cumulative prefix is below the
                       model's floor (see `estimate_tokens`)
  3. message prefix - up to two advancing boundaries (last +
                       second-to-last), so turn N reads what turn N-1
                       wrote while extending the cached prefix
"""

from __future__ import annotations

from dataclasses import dataclass

# chars/4 — an EXPLICITLY approximate token estimate (no tokenizer
# dependency), used only to compare against `CacheCapability.min_prefix_tokens`
# (measured in tokens). Never used for billing — `pricing.py` (Phase 7)
# uses the provider's own reported token counts for that.
CHARS_PER_TOKEN_APPROX = 4


def estimate_tokens(char_count: int) -> int:
    return char_count // CHARS_PER_TOKEN_APPROX


@dataclass(frozen=True)
class AllocationPlan:
    """Which slot TYPES the allocator granted and how many message
    breakpoints — never byte offsets themselves. Finding the actual
    eligible positions inside the message list needs the formatted
    content, which is provider-shape-specific and stays in the calling
    strategy.
    """

    tools: bool
    system: bool
    message_breakpoints: int


class BreakpointAllocator:
    """Hands out at most `max_breakpoints` slots, counting
    already-present (client/gateway-injected) breakpoints against the
    same budget so a strategy composing with a gateway never exceeds
    the provider's hard cap."""

    def __init__(self, max_breakpoints: int) -> None:
        self._max_breakpoints = max_breakpoints

    def allocate(
        self,
        *,
        existing_breakpoints: int = 0,
        has_tools: bool = False,
        system_clears_floor: bool = False,
        eligible_message_boundaries: int = 0,
        max_message_breakpoints: int = 2,
    ) -> AllocationPlan:
        budget = max(self._max_breakpoints - existing_breakpoints, 0)

        tools_granted = False
        if has_tools and budget > 0:
            tools_granted = True
            budget -= 1

        system_granted = False
        if system_clears_floor and budget > 0:
            system_granted = True
            budget -= 1

        message_breakpoints = max(
            min(eligible_message_boundaries, budget, max_message_breakpoints), 0
        )
        return AllocationPlan(
            tools=tools_granted, system=system_granted, message_breakpoints=message_breakpoints
        )


__all__ = ["AllocationPlan", "BreakpointAllocator", "CHARS_PER_TOKEN_APPROX", "estimate_tokens"]
