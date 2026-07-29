"""Vendored from agent-loop's `tests/hooks/_helpers.py` (Phase 9 — "bring
test patterns, not the full suite"). Exercises a single middleware function
directly, without a full `HookRegistry`/`Pipeline`: every builtin middleware
has the shape `async def _middleware(ctx, next_fn) -> None`, so these
helpers just build the right context, call the middleware with a no-op
`next_fn`, and hand back the (possibly mutated) context for assertions.
"""

from __future__ import annotations

from uuid import uuid4

from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    GuardrailContext,
    ModelCallContext,
    ModelResponseContext,
    ToolCallContext,
    ToolResultContext,
    TurnContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision


async def _noop_next() -> None:
    return None


async def run_pre_tool(middleware, tool_path="/toolsets/builtin/bash", tool_input=None, **kwargs) -> ToolCallContext:
    ctx = ToolCallContext(tool_path=tool_path, tool_input=tool_input or {}, **kwargs)
    await middleware(ctx, _noop_next)
    return ctx


async def run_post_tool(middleware, tool_response, tool_path="/toolsets/builtin/bash", **kwargs) -> ToolResultContext:
    kwargs.setdefault("tool_use_id", uuid4())
    ctx = ToolResultContext(tool_path=tool_path, tool_response=tool_response, **kwargs)
    await middleware(ctx, _noop_next)
    return ctx


async def run_pre_model(middleware, messages, budget) -> list:
    ctx = ModelCallContext(messages=list(messages), budget=budget)
    await middleware(ctx, _noop_next)
    return ctx.messages


async def run_post_model(middleware, response, tool_calls=None, turn_index=0, **kwargs) -> ModelResponseContext:
    ctx = ModelResponseContext(
        response=response, tool_calls=list(tool_calls or []), turn_index=turn_index, **kwargs,
    )
    await middleware(ctx, _noop_next)
    return ctx


async def run_pre_agent(middleware, goal=None) -> AgentLifecycleContext:
    ctx = AgentLifecycleContext(goal=goal)
    await middleware(ctx, _noop_next)
    return ctx


async def run_post_agent(middleware, result=None) -> AgentLifecycleContext:
    ctx = AgentLifecycleContext(result=result)
    await middleware(ctx, _noop_next)
    return ctx


async def run_pre_turn(middleware, turn_index=0, **kwargs) -> TurnContext:
    ctx = TurnContext(turn_index=turn_index, **kwargs)
    await middleware(ctx, _noop_next)
    return ctx


async def run_post_turn(middleware, turn_index=0, turn=None, **kwargs) -> TurnContext:
    ctx = TurnContext(turn_index=turn_index, turn=turn, **kwargs)
    await middleware(ctx, _noop_next)
    return ctx


async def run_guardrail_input(middleware, messages) -> GuardrailContext:
    ctx = GuardrailContext(messages=messages)
    await middleware(ctx, _noop_next)
    return ctx


async def run_guardrail_output(middleware, output) -> GuardrailContext:
    ctx = GuardrailContext(output=output)
    await middleware(ctx, _noop_next)
    return ctx


def assert_denied(ctx) -> None:
    assert ctx.decision in (PreDecision.DENY, PostDecision.BLOCK)


def assert_allowed(ctx) -> None:
    assert ctx.decision not in (PreDecision.DENY, PostDecision.BLOCK)


__all__ = [
    "run_pre_tool",
    "run_post_tool",
    "run_pre_model",
    "run_post_model",
    "run_pre_agent",
    "run_post_agent",
    "run_pre_turn",
    "run_post_turn",
    "run_guardrail_input",
    "run_guardrail_output",
    "assert_denied",
    "assert_allowed",
]
