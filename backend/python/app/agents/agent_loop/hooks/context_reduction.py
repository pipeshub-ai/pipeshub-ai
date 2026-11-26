"""Tells the person when today's loop reduced a request's history (QAFIX4 B2).

The PRE_MODEL shapers after this one (L4 loop compaction, L5 sliding window, L6 deterministic compaction, L7 auto
compaction, L8, L9) fold, shorten or drop older messages to fit the window. Nothing said so: on QA-FP12 run 8cbf6950 a
request went out at a fifth of its size and the model's odd reply was the only sign. The durable loop journals the same
event as ``x.pipeshub.context.compacted``; here it is the same ``context_compacted`` frame live, the same part in the
saved transcript (so the row is there after a reload), and a log line.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.tokens import count_tokens
from app.agents.agent_loop.protocol.formatter import compacted_view

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)


def _tokens(count: int) -> str:
    return f"{round(count / 1000)}k" if count >= 1000 else str(count)


def report_context_reduction(context: AgentContext) -> Any:  # noqa: ANN401 - a PRE_MODEL middleware
    """A PRE_MODEL middleware registered before the history shapers: measures the request before and after them."""

    async def _middleware(ctx: ModelCallContext, next_fn: Any) -> None:  # noqa: ANN401
        before = count_tokens(ctx.messages)
        count = len(ctx.messages)
        await next_fn()
        after = count_tokens(ctx.messages)
        if after >= before:
            return
        logger.info(
            "context.reduced turn=%d beforeTokens=%d afterTokens=%d messagesBefore=%d messagesAfter=%d",
            ctx.turn_index, before, after, count, len(ctx.messages),
        )
        collector = context.transcript_collector
        if collector is not None:
            collector.record_context_compacted(compacted_view(before, after))
        sink = context.event_sink
        if sink is None:
            return
        message = f"Context compacted · {_tokens(before)} → {_tokens(after)} tokens"
        try:
            for frame in context.formatter.context_compacted(
                context, before_tokens=before, after_tokens=after, message=message,
            ):
                await sink.write(frame)
        except Exception:
            logger.debug("report_context_reduction: event_sink.write failed", exc_info=True)

    return _middleware


__all__ = ["report_context_reduction"]
