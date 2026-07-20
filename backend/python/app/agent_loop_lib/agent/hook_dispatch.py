"""Thin bridge between `Agent.run()`'s exception-based control flow and the
new `HookRegistry` kernel's decision-based `Pipeline` dispatch.

`Agent.run()` predates the middleware kernel and is structured around
`HookBlocked` exceptions at a handful of well-defined points (pre_agent,
pre_turn, guardrails) plus a couple of pure transforms (pre_model,
wrap_model_call). Rather than restructure that already-intricate control
flow (which includes an asyncio.wait race between the LLM call and the
input guardrail), each function here dispatches through the appropriate
`kernel.on(event)` Pipeline (or `kernel.wrapper(event)` Wrapper) and
re-raises `HookBlocked` on a DENY/BLOCK decision — giving every call site in
`agent/__init__.py` the exact same shape it had against the old `HookChain`.

Every dispatch function accepts the relevant scope (`RunScope` for agent-
level events, `TurnScope` for turn/model/guardrail events) and populates it
on the decision context — including the `session_id` field, which used to
only ever get filled in for tool contexts by `ToolExecutor`. `scope`
defaults to `None` so callers that don't yet have one (or tests building a
context directly) are unaffected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.exceptions import HookBlocked, RunCancelled
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    GuardrailContext,
    ModelCallContext,
    ModelResponseContext,
    TurnContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision

if TYPE_CHECKING:
    from app.agent_loop_lib.context.base import ContextBudget
    from app.agent_loop_lib.core.responses import ModelResponse
    from app.agent_loop_lib.core.scope import RunScope, TurnScope
    from app.agent_loop_lib.core.types import (
        AgentResult,
        AgentTurn,
        Goal,
        Message,
        ToolCall,
    )
    from app.agent_loop_lib.hooks.registry import HookRegistry

__all__ = [
    "dispatch_pre_agent",
    "dispatch_post_agent",
    "dispatch_pre_turn",
    "dispatch_post_turn",
    "dispatch_pre_model",
    "dispatch_post_model",
    "dispatch_guardrail_input",
    "dispatch_guardrail_output",
    "call_model_wrapped",
]


async def dispatch_pre_agent(
    kernel: "HookRegistry", goal: "Goal", *, scope: "RunScope | None" = None
) -> None:
    ctx = AgentLifecycleContext(goal=goal, scope=scope, session_id=scope.session_id if scope else None)
    await kernel.on(HookEvent.PRE_AGENT).dispatch(ctx)
    if ctx.decision == PreDecision.DENY:
        raise HookBlocked(ctx.decision_reason or "blocked before start")


async def dispatch_post_agent(
    kernel: "HookRegistry", result: "AgentResult", *, scope: "RunScope | None" = None
) -> None:
    ctx = AgentLifecycleContext(result=result, scope=scope, session_id=scope.session_id if scope else None)
    await kernel.on(HookEvent.POST_AGENT).dispatch(ctx)


async def dispatch_pre_turn(
    kernel: "HookRegistry", turn_index: int, *, scope: "TurnScope | None" = None
) -> None:
    ctx = TurnContext(
        turn_index=turn_index, scope=scope, session_id=scope.run.session_id if scope else None,
    )
    await kernel.on(HookEvent.PRE_TURN).dispatch(ctx)
    if ctx.decision == PreDecision.DENY:
        if ctx.metadata.get("cancelled"):
            raise RunCancelled(ctx.decision_reason or "Cancelled")
        raise HookBlocked(ctx.decision_reason or f"blocked before turn {turn_index}")


async def dispatch_post_turn(
    kernel: "HookRegistry", turn_index: int, turn: "AgentTurn", *, scope: "TurnScope | None" = None
) -> None:
    ctx = TurnContext(
        turn_index=turn_index, turn=turn, scope=scope, session_id=scope.run.session_id if scope else None,
    )
    await kernel.on(HookEvent.POST_TURN).dispatch(ctx)


async def dispatch_pre_model(
    kernel: "HookRegistry",
    messages: "list[Message]",
    budget: "ContextBudget",
    *,
    turn_index: int = 0,
    max_turns: int | None = None,
    scope: "TurnScope | None" = None,
) -> "list[Message]":
    ctx = ModelCallContext(
        messages=list(messages), budget=budget, turn_index=turn_index, max_turns=max_turns,
        scope=scope, session_id=scope.run.session_id if scope else None,
    )
    await kernel.on(HookEvent.PRE_MODEL).dispatch(ctx)
    return ctx.messages


async def dispatch_post_model(
    kernel: "HookRegistry",
    response: "Message",
    tool_calls: "list[ToolCall]",
    turn_index: int,
    *,
    scope: "TurnScope | None" = None,
) -> ModelResponseContext:
    ctx = ModelResponseContext(
        response=response, tool_calls=list(tool_calls), turn_index=turn_index,
        scope=scope, session_id=scope.run.session_id if scope else None,
    )
    await kernel.on(HookEvent.POST_MODEL).dispatch(ctx)
    return ctx


async def dispatch_guardrail_input(
    kernel: "HookRegistry", messages: "list[Message]", *, scope: "TurnScope | None" = None
) -> None:
    ctx = GuardrailContext(messages=messages, scope=scope, session_id=scope.run.session_id if scope else None)
    await kernel.on(HookEvent.GUARDRAIL_INPUT).dispatch(ctx)
    if ctx.decision == PostDecision.BLOCK:
        raise HookBlocked(ctx.decision_reason or "input guardrail blocked")


async def dispatch_guardrail_output(
    kernel: "HookRegistry", output: str, *, scope: "TurnScope | None" = None
) -> None:
    ctx = GuardrailContext(output=output, scope=scope, session_id=scope.run.session_id if scope else None)
    await kernel.on(HookEvent.GUARDRAIL_OUTPUT).dispatch(ctx)
    if ctx.decision == PostDecision.BLOCK:
        raise HookBlocked(ctx.decision_reason or "output guardrail blocked")


async def call_model_wrapped(
    kernel: "HookRegistry", call_llm: Callable[[], Awaitable["ModelResponse"]]
) -> "ModelResponse":
    composed = kernel.wrapper(HookEvent.PRE_MODEL_CALL).compose(call_llm)
    return await composed()
