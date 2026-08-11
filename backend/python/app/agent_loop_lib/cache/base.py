"""The injection seam: a `Protocol` a transport depends on, never a
concrete implementation.

`CacheableRequest`/`CachePlan`/`ApplyResult` operate at the transport's
own wire-format boundary (provider-shaped `dict`s), not at agent-loop's
`Message` model, so a strategy never needs to know about agent-loop's
message types — only about the provider payload shape it already
builds today (see `AnthropicTransport._format_message` et al.).

Splitting `plan()` from `apply()` is deliberate: `plan()` is where
every rule lives (breakpoint budget, minimum-prefix check, TTL
ordering, gateway stand-down) and is testable as a pure function
against a payload description, with no provider SDK and no
formatted-message fixtures. `apply()` only ever executes a plan that
has already been decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CacheableRequest:
    """Provider-formatted view of one LLM call, as the transport is
    about to send it. `system`/`tools` are `None` when the call has
    none; `messages` is always the full formatted message list."""

    messages: list[dict[str, Any]]
    system: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class CachePlan:
    """The strategy's decision, opaque to the transport beyond
    `enabled`. Concrete strategies stash whatever internal state
    `apply()` needs (breakpoint indices, etc.) in `extra` — the
    transport never inspects it, only passes it back to the same
    strategy's `apply()`."""

    enabled: bool
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplyResult:
    """`apply()`'s return value: annotated COPIES of the request's
    payload (never a mutation of the caller's dicts — callers may
    reuse the same tool-schema list across calls) plus any
    request-level kwargs a provider needs alongside the payload
    (OpenAI's `prompt_cache_key`, for instance)."""

    messages: list[dict[str, Any]]
    system: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    request_kwargs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PromptCacheStrategy(Protocol):
    """Injection seam: `agent_loop_lib` transports depend on this
    Protocol, never on a concrete implementation — the real per-model
    strategies (`app.llm.prompt_cache.strategy.*`) and the up-front
    "should this call be cached at all" decision live outside this
    hermetic library.
    """

    def plan(self, request: CacheableRequest) -> CachePlan:
        """Pure function: given the request shape, decide where
        breakpoints go and what request-level kwargs to add. No I/O,
        no mutation of `request` or anything it references."""
        ...

    def apply(self, plan: CachePlan, request: CacheableRequest) -> ApplyResult:
        """Execute `plan` against `request`, returning annotated
        copies. Never called with a `plan` this strategy did not
        itself produce via `plan()`."""
        ...


class NoopStrategy:
    """The library's default: behaves as if caching does not exist.
    `plan()` always returns `enabled=False`; `apply()` returns the
    payload unchanged (identity, not a defensive copy — there is
    nothing to protect against mutating here) so a caller that
    invokes it unconditionally still gets a correct, inert result."""

    def plan(self, request: CacheableRequest) -> CachePlan:
        return CachePlan(enabled=False, reason="noop_strategy")

    def apply(self, plan: CachePlan, request: CacheableRequest) -> ApplyResult:
        return ApplyResult(messages=request.messages, system=request.system, tools=request.tools)


__all__ = [
    "CacheableRequest",
    "CachePlan",
    "ApplyResult",
    "PromptCacheStrategy",
    "NoopStrategy",
]
