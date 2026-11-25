# pyright: basic
"""Express-style middleware pipeline: contexts, dispatch engine, and path routing.

This package is the deterministic backbone the rest of agent-loop's hooks
system is built on (see `agent_loop.hooks`). It has no knowledge of tools,
agents, or turns — it only knows how to route a context object through an
ordered stack of `(matcher, middleware)` pairs and let each middleware decide
whether to continue the chain.
"""

from harness_core.kernel.hooks.middleware.context import (
    AgentLifecycleContext,
    GuardrailContext,
    ModelCallContext,
    ToolCallContext,
    ToolResultContext,
    TurnContext,
)
from harness_core.kernel.hooks.middleware.decisions import (
    POST_SEVERITY,
    PRE_SEVERITY,
    PostDecision,
    PreDecision,
)
from harness_core.kernel.hooks.middleware.pipeline import Middleware, Next, Pipeline
from harness_core.kernel.hooks.middleware.routing import (
    Matcher,
    by_tag,
    by_tags,
    match_all,
    path_match,
    prefixed,
    to_matcher,
)
from harness_core.kernel.hooks.middleware.wrapper import (
    WrapMiddleware,
    WrapNext,
    Wrapper,
)

__all__ = [
    "ToolCallContext",
    "ToolResultContext",
    "AgentLifecycleContext",
    "TurnContext",
    "ModelCallContext",
    "GuardrailContext",
    "PreDecision",
    "PostDecision",
    "PRE_SEVERITY",
    "POST_SEVERITY",
    "Pipeline",
    "Middleware",
    "Next",
    "Wrapper",
    "WrapMiddleware",
    "WrapNext",
    "Matcher",
    "match_all",
    "to_matcher",
    "path_match",
    "prefixed",
    "by_tag",
    "by_tags",
]
