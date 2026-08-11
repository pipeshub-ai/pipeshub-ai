"""`OpenAICacheStrategy` — the framework-neutral home for OpenAI cache
placement on the native `OpenAITransport` (Chat Completions API).

Two capability modes resolve to very different behavior:

- **`automatic`** (pre-GPT-5.6): OpenAI caches any >1,024-token prefix
  server-side with ZERO request changes. `apply()` therefore restructures
  nothing — it only ever adds `prompt_cache_key` (a routing hint that
  improves match reliability even in automatic mode; see the plan's
  "tenant-scoped `prompt_cache_key`" requirement). No breakpoints exist
  to place, so `CacheCapability.max_breakpoints` is 0 for this mode and
  the allocator is never consulted.
- **`explicit`** (GPT-5.6+): `prompt_cache_options.mode="explicit"`
  disables the implicit last-message breakpoint so ONLY markers this
  strategy places are eligible for cache reads/writes.
  `prompt_cache_breakpoint` lands on a message CONTENT BLOCK, which
  Chat Completions requires to be a list-of-blocks — `OpenAITransport`
  always formats content as a plain string
  (`app.agent_loop_lib.transport.openai._format_message`), so `apply()`
  restructures only the messages it actually marks, leaving every
  other message's plain-string content untouched.

Tool-role messages (`role: "tool"`, i.e. `function_call_output`) are
deliberately never chosen as breakpoints: OpenAI's docs report a
breakpoint placed there accepts the request but never creates a cache
write. There is also no way to place a breakpoint on the `tools` array
itself (`CacheCapability.can_cache_tools` is `False` for OpenAI) — only
on a message's own content blocks.

Reuses the SAME `BreakpointAllocator`/cumulative-floor/stand-down
building blocks Anthropic's hardened strategy uses (`allocator.py`,
`standdown.py`, `ttl.py`) rather than reimplementing budget or floor
logic — the parts that differ between providers are WHERE a breakpoint
can land (message content blocks only, no tool/system slot) and WHAT
kwargs accompany it (`prompt_cache_key`/`prompt_cache_options` instead
of nothing).
"""

from __future__ import annotations

import copy
from typing import Any

from app.agent_loop_lib.cache.base import ApplyResult, CacheableRequest, CachePlan
from app.llm.prompt_cache.allocator import BreakpointAllocator, estimate_tokens
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.standdown import should_stand_down
from app.llm.prompt_cache.ttl import validate_ttl_ordering

_BREAKPOINT_MARKER = {"mode": "explicit"}

# `function_call_output` (role "tool") never creates a cache write when
# marked — see this module's docstring. `system`/`developer` and
# `user`/`assistant` turns are the only valid breakpoint targets.
_NON_CACHEABLE_ROLES = frozenset({"tool", "function"})

# Mirrors Anthropic's "last + second-to-last" advancing pattern (see
# `strategy/anthropic.py`) so a re-read happens on the very next turn
# instead of writing one fresh, never-reread breakpoint per turn.
_MAX_MESSAGE_BREAKPOINTS = 2


class OpenAICacheStrategy:
    """Implements `agent_loop_lib.cache.base.PromptCacheStrategy` via
    structural typing. Bound to one `CacheCapability` (model-specific:
    explicit vs automatic) and one `cache_key` at construction — the
    key must be tenant+user-scoped by the caller (see
    `factory.build_openai_cache_key`) so unrelated prefixes never route
    to the same backend and contend."""

    def __init__(self, capability: CacheCapability, cache_key: str | None = None) -> None:
        self._capability = capability
        self._cache_key = cache_key
        self._allocator = BreakpointAllocator(capability.max_breakpoints)

    def plan(self, request: CacheableRequest) -> CachePlan:
        if should_stand_down(request.messages, request.system, request.tools):
            return CachePlan(enabled=False, reason="gateway_standdown")
        return CachePlan(enabled=True, reason=f"openai_{self._capability.mode}")

    def apply(self, plan: CachePlan, request: CacheableRequest) -> ApplyResult:
        request_kwargs: dict[str, Any] = {}
        if self._cache_key:
            request_kwargs["prompt_cache_key"] = self._cache_key

        if self._capability.mode != "explicit":
            # Automatic: no restructuring, no breakpoints — ordering
            # alone (system/stable content first, which
            # `OpenAITransport._format_messages` already guarantees) is
            # what earns the server-side cache hit.
            return ApplyResult(
                messages=request.messages,
                system=request.system,
                tools=request.tools,
                request_kwargs=request_kwargs,
            )

        request_kwargs["prompt_cache_options"] = {
            "mode": "explicit",
            "ttl": self._capability.default_ttl,
        }

        eligible_indices = self._eligible_message_boundaries(
            request.messages, self._capability.min_prefix_tokens
        )
        allocation = self._allocator.allocate(
            has_tools=False,
            system_clears_floor=False,
            eligible_message_boundaries=len(eligible_indices),
            max_message_breakpoints=_MAX_MESSAGE_BREAKPOINTS,
        )
        chosen_indices = (
            eligible_indices[-allocation.message_breakpoints :]
            if allocation.message_breakpoints
            else []
        )
        validate_ttl_ordering([self._capability.default_ttl] * len(chosen_indices))

        messages = self._mark_message_breakpoints(request.messages, chosen_indices)
        return ApplyResult(
            messages=messages, system=request.system, tools=request.tools, request_kwargs=request_kwargs
        )

    @staticmethod
    def _eligible_message_boundaries(
        messages: list[dict[str, Any]], floor_tokens: int
    ) -> list[int]:
        """Indices into `messages` (excluding the final two, which
        change every turn) whose CUMULATIVE prefix length — summed
        from the start of the list through and including this
        message — clears `floor_tokens`, and whose own role/content is
        a valid breakpoint target (non-empty string content, not a
        tool-output message)."""
        if len(messages) <= 2:
            return []
        candidates = messages[:-2]
        cumulative_chars = 0
        eligible: list[int] = []
        for i, msg in enumerate(candidates):
            content = msg.get("content")
            cumulative_chars += len(content) if isinstance(content, str) else 0
            if estimate_tokens(cumulative_chars) < floor_tokens:
                continue
            if msg.get("role") in _NON_CACHEABLE_ROLES:
                continue
            if isinstance(content, str) and content:
                eligible.append(i)
        return eligible

    @staticmethod
    def _mark_message_breakpoints(
        messages: list[dict[str, Any]], indices: list[int]
    ) -> list[dict[str, Any]]:
        """Restructures ONLY the chosen messages' `content` from a
        plain string into a one-block list carrying
        `prompt_cache_breakpoint` — every other message is a shallow
        copy of the original, still plain-string content."""
        copied = copy.deepcopy(messages)
        for idx in indices:
            msg = copied[idx]
            text = msg.get("content")
            if not isinstance(text, str) or not text:
                continue
            msg["content"] = [
                {"type": "text", "text": text, "prompt_cache_breakpoint": dict(_BREAKPOINT_MARKER)}
            ]
        return copied


__all__ = ["OpenAICacheStrategy"]
