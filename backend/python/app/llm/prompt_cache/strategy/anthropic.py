"""`AnthropicCacheStrategy` — the framework-neutral home for Anthropic
cache-breakpoint placement, hardened per Phase 3 of the plan.

Phase 1 shipped an EQUIVALENT relocation of the transport's legacy
inline logic: one message breakpoint, one system breakpoint, one tool
breakpoint, unconditionally. Phase 3 supersedes that with:

- A `BreakpointAllocator`-governed budget (never exceeds
  `capability.max_breakpoints`), counting any pre-existing
  `cache_control` markers (gateway/client) against the same cap.
- TWO advancing message breakpoints (last + second-to-last eligible
  boundary) instead of one, so turn N's cache read covers what turn
  N-1 wrote while the newest boundary extends coverage for turn N+1.
- A CUMULATIVE prefix-floor check (total chars from the start of the
  message list through a candidate boundary, not a single block's own
  size) against the model's `min_prefix_tokens` — a 300-char tool
  result is a perfectly valid breakpoint if everything before it
  already clears the floor; a 2,000-char first message is not, below
  Haiku's floor.
- Gateway stand-down: if a `cache_control` marker already exists
  anywhere in the payload, this strategy does nothing at all (see
  `standdown.should_stand_down`) rather than risk exceeding the
  provider's hard cap on top of markers it didn't place.
- TTL ordering validation on every apply, even though v1 places only
  5m breakpoints — see `ttl.validate_ttl_ordering`.

`apply()` never mutates `request.messages`/`.system`/`.tools` in
place — it returns deep copies. The transport is responsible for
building the plain (uncached) formatted payload before constructing a
`CacheableRequest`.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.agent_loop_lib.cache.base import ApplyResult, CacheableRequest, CachePlan
from app.llm.prompt_cache.allocator import BreakpointAllocator, estimate_tokens
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.standdown import should_stand_down
from app.llm.prompt_cache.ttl import validate_ttl_ordering

_EPHEMERAL = {"type": "ephemeral"}


def _block_text(block: dict[str, Any]) -> str:
    btype = block.get("type")
    raw = block.get("content", "") if btype == "tool_result" else block.get("text", "")
    return raw if isinstance(raw, str) else ""


def _message_char_length(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, list):
        return sum(len(_block_text(b)) for b in content if isinstance(b, dict))
    return len(content) if isinstance(content, str) else 0


def _serialized_char_length(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _tools_char_length(tools: list[dict[str, Any]] | None) -> int:
    return _serialized_char_length(tools) if tools else 0


class AnthropicCacheStrategy:
    """Implements `agent_loop_lib.cache.base.PromptCacheStrategy` via
    structural typing. Bound to one `CacheCapability` at construction
    (see `factory.resolve_strategy`, which resolves capability —
    and therefore the token floor and breakpoint cap — per model, not
    per provider)."""

    def __init__(self, capability: CacheCapability) -> None:
        self._capability = capability
        self._allocator = BreakpointAllocator(capability.max_breakpoints)

    def plan(self, request: CacheableRequest) -> CachePlan:
        if should_stand_down(request.messages, request.system, request.tools):
            return CachePlan(enabled=False, reason="gateway_standdown")
        return CachePlan(enabled=True, reason="anthropic_allocator_v3")

    def apply(self, plan: CachePlan, request: CacheableRequest) -> ApplyResult:
        floor_tokens = self._capability.min_prefix_tokens

        eligible_indices = self._eligible_message_boundaries(request.messages, floor_tokens)
        system_clears_floor = self._system_clears_floor(
            request.system, floor_tokens, tools=request.tools
        )

        allocation = self._allocator.allocate(
            has_tools=bool(request.tools),
            system_clears_floor=system_clears_floor,
            eligible_message_boundaries=len(eligible_indices),
        )

        ttls_in_order: list[str] = []

        tools = request.tools
        if allocation.tools:
            tools = self._mark_tool_breakpoint(request.tools)
            ttls_in_order.append(self._capability.default_ttl)

        system = request.system
        if allocation.system:
            system = self._mark_system_breakpoint(request.system)
            ttls_in_order.append(self._capability.default_ttl)

        chosen_indices = (
            eligible_indices[-allocation.message_breakpoints :]
            if allocation.message_breakpoints
            else []
        )
        messages = self._mark_message_breakpoints(request.messages, chosen_indices)
        ttls_in_order.extend([self._capability.default_ttl] * len(chosen_indices))

        validate_ttl_ordering(ttls_in_order)

        return ApplyResult(messages=messages, system=system, tools=tools)

    @staticmethod
    def _eligible_message_boundaries(
        messages: list[dict[str, Any]], floor_tokens: int
    ) -> list[int]:
        """Indices into `messages` (excluding the final two, which
        change every turn) whose CUMULATIVE prefix length — summed
        from the start of the list through and including this message
        — clears `floor_tokens`, and which contain an actual
        text/tool_result block to mark. Returned in ascending order;
        callers take the last N (closest to the end) via negative
        slicing."""
        if len(messages) <= 2:
            return []
        candidates = messages[:-2]
        cumulative_chars = 0
        eligible: list[int] = []
        for i, msg in enumerate(candidates):
            cumulative_chars += _message_char_length(msg)
            if estimate_tokens(cumulative_chars) < floor_tokens:
                continue
            content = msg.get("content")
            if isinstance(content, list) and any(
                isinstance(b, dict) and _block_text(b) for b in content
            ):
                eligible.append(i)
        return eligible

    @staticmethod
    def _system_clears_floor(
        system: list[dict[str, Any]] | None,
        floor_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Eligibility is the prefix Anthropic sees through the marker
        we actually place (serialized tools + `system[0]`), not the
        sum of every system block. Later system blocks are not in that
        prefix; a large tool schema is.
        """
        if not system:
            return False
        first = system[0]
        marked_chars = _tools_char_length(tools) + (
            len(_block_text(first)) if isinstance(first, dict) else 0
        )
        return estimate_tokens(marked_chars) >= floor_tokens

    @staticmethod
    def _mark_tool_breakpoint(
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """Marks the last tool schema — tool schemas are stable turn-
        to-turn except when `fetch_tools` changes the set."""
        if not tools:
            return tools
        copied = [dict(t) for t in tools]
        copied[-1] = {**copied[-1], "cache_control": dict(_EPHEMERAL)}
        return copied

    @staticmethod
    def _mark_system_breakpoint(
        system: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        """Marks the FIRST system block only — the stable band."""
        if not system:
            return system
        copied = copy.deepcopy(system)
        copied[0] = {**copied[0], "cache_control": dict(_EPHEMERAL)}
        return copied

    @staticmethod
    def _mark_message_breakpoints(
        messages: list[dict[str, Any]], indices: list[int]
    ) -> list[dict[str, Any]]:
        copied = copy.deepcopy(messages)
        for idx in indices:
            msg = copied[idx]
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in reversed(content):
                if isinstance(block, dict) and _block_text(block) and "cache_control" not in block:
                    block["cache_control"] = dict(_EPHEMERAL)
                    break
        return copied


__all__ = ["AnthropicCacheStrategy"]
