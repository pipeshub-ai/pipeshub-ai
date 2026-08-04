from __future__ import annotations

import logging

from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    ToolCallContext,
    ToolResultContext,
    TurnContext,
)

logger = logging.getLogger(__name__)

"""Structured logging middleware — the direct replacement for `LoggingHook`.
Split into one factory per lifecycle event (rather than one class
implementing four `Hook` methods) since each is now registered on its own
pipeline; `ControlPlane` registers whichever of these it wants, in whichever
combination, instead of one hook object bundling all four."""


def audit_log_pre_tool():
    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        logger.debug("pre_tool_use: path=%s input=%s", ctx.tool_path, ctx.tool_input)
        await next_fn()

    return _middleware


def audit_log_post_tool():
    async def _middleware(ctx: ToolResultContext, next_fn) -> None:
        result = ctx.tool_response
        content_summary = str(result.data if result.success else result.error)[:200]
        logger.debug(
            "post_tool_use: path=%s success=%s content=%s",
            ctx.tool_path, result.success, content_summary,
        )
        await next_fn()

    return _middleware


def audit_log_post_turn(level: int = logging.INFO):
    async def _middleware(ctx: TurnContext, next_fn) -> None:
        turn = ctx.turn
        if turn is not None:
            logger.log(
                level,
                "post_turn: turn=%d tool_calls=%d tool_results=%d messages=%d",
                ctx.turn_index, len(turn.tool_calls), len(turn.tool_results), len(turn.messages),
            )
        await next_fn()

    return _middleware


def audit_log_post_agent(level: int = logging.INFO):
    async def _middleware(ctx: AgentLifecycleContext, next_fn) -> None:
        result = ctx.result
        if result is not None:
            output_summary = str(result.output)[:200]
            logger.log(
                level, "post_agent: success=%s error=%s output=%s",
                result.success, result.error, output_summary,
            )
        await next_fn()

    return _middleware
