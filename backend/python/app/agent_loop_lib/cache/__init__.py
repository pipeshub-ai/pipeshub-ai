"""Prompt-cache injection seam for `agent_loop_lib` transports.

This subpackage is deliberately tiny: a `Protocol` plus the data types
it operates on, and a `NoopStrategy` default. `agent_loop_lib` stays
hermetic — it declares the shape a caching strategy must have, but the
real per-provider implementations (and the "should this call even be
cached" decision) live outside the library, in `app.llm.prompt_cache`,
so agent-loop can be used standalone with zero PipesHub knowledge and
without dragging in a caching implementation it doesn't need.

See the plan's "Why three layers instead of one package" for the full
rationale.
"""

from app.agent_loop_lib.cache.base import (
    ApplyResult,
    CacheableRequest,
    CachePlan,
    NoopStrategy,
    PromptCacheStrategy,
)

__all__ = [
    "ApplyResult",
    "CacheableRequest",
    "CachePlan",
    "NoopStrategy",
    "PromptCacheStrategy",
]
