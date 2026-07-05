"""Phase 8: bridges agent-loop's push-based event delivery (`EventEmitter.
emit()`/`EventSink.write()`, both plain `await`-and-return coroutines) to
FastAPI's `StreamingResponse`, which needs an async generator that YIELDS
SSE lines as they become available while `Agent.run()` is still executing.

`Agent.run(goal)` is one long-lived coroutine — it doesn't yield control
back to its caller between tool calls the way `graph.astream(...,
stream_mode="custom")` does for the legacy LangGraph path. `QueueEventSink`
gives hooks/`SSEEventEmitter` somewhere to push events to in real time;
`run_agent_loop_stream()` runs the whole agent-loop + respond-pipeline flow
as a background task and concurrently drains that queue into SSE-formatted
strings, so the two phases (tool orchestration, then response synthesis)
stream through the exact same connection the legacy `stream_response()`
generator writes to — same SSE format, same event names.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.types import Goal
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks import CitationCollector
from app.agents.agent_loop.respond import RespondPipeline

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

# Sentinel distinguishing "queue drained, producer done" from a real event
# dict — a private object identity check, not a value any real event could
# collide with.
_DONE = object()


class QueueEventSink:
    """`EventSink` (`app.modules.agents.event_sink`) backed by an
    `asyncio.Queue` — the agent-loop-path counterpart to `LangGraphEventSink`
    (which writes straight through a `StreamWriter` because LangGraph's own
    `astream(..., stream_mode="custom")` already provides the queueing/
    backpressure `Agent.run()` doesn't)."""

    def __init__(self, queue: "asyncio.Queue[Any]") -> None:
        self._queue = queue

    async def write(self, event: dict[str, Any]) -> bool:
        await self._queue.put(event)
        return True


async def run_agent_loop_stream(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    llm: "BaseChatModel",
    log: logging.Logger,
    retrieval_service: Any,
    graph_provider: Any,
    reranker_service: Any,
    config_service: Any,
    org_info: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_key: str | None = None,
    is_multimodal_llm: bool = False,
    client_name: str | None = None,
) -> "AsyncGenerator[str, None]":
    """agent-loop counterpart to `app.api.routes.agent.stream_response()` —
    same signature/SSE wire format, so `chat_stream`'s feature-flag branch
    (Phase 8b) can call either interchangeably. Builds the same `ChatState`
    dict the legacy path builds (`build_initial_state`, reused unchanged for
    byte-for-byte parity of every derived field), wraps it as `AgentContext`,
    then drives `PipesHubAgentFactory` + `Agent.run()` + `RespondPipeline`
    through one shared `QueueEventSink`.
    """
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.utils.execute_query import has_sql_connector_configured
    from app.utils.fetch_slack_thread import has_slack_connector_configured

    try:
        has_sql_connector = await has_sql_connector_configured(
            graph_provider, user_info["userId"], user_info["orgId"]
        )
        has_slack_connector = await has_slack_connector_configured(
            graph_provider, user_info["userId"], user_info["orgId"]
        )
        chat_state = build_initial_state(
            query_info, user_info, llm, log, retrieval_service, graph_provider,
            reranker_service, config_service, model_name, model_key, org_info,
            "react", has_sql_connector=has_sql_connector, is_multimodal_llm=is_multimodal_llm,
            has_slack_connector=has_slack_connector, client_name=client_name,
        )
    except Exception as exc:
        log.error("agent-loop stream: failed to build initial state: %s", exc, exc_info=True)
        error_code, user_message = classify_error(str(exc))
        yield f"event: error\ndata: {json.dumps({'message': user_message, 'type': error_code})}\n\n"
        return

    queue: asyncio.Queue[Any] = asyncio.Queue()
    context = AgentContext.from_chat_state(chat_state, event_sink=QueueEventSink(queue))

    async def _produce() -> None:
        try:
            factory = PipesHubAgentFactory()
            agent, _runtime = await factory.create(
                context, llm, query_info.get("chatMode", "auto"),
                query=query_info.get("query", ""),
                model_name=model_name or "",
            )
            goal = Goal(description=query_info.get("query", ""))
            result = await agent.run(goal)

            respond = RespondPipeline(context, CitationCollector(context))
            await respond.run(
                agent_success=result.success, agent_error=result.error,
                event_sink=context.event_sink,
            )
        except Exception as exc:
            log.error("agent-loop stream: run failed: %s", exc, exc_info=True)
            error_code, user_message = classify_error(str(exc))
            await context.event_sink.write({
                "event": "error", "data": {"message": user_message, "type": error_code},
            })
        finally:
            # Guarantees sandbox teardown on normal completion, agent
            # failure, AND client-disconnect cancellation alike (the outer
            # `finally` below cancels this task, and `finally` blocks still
            # run on `CancelledError`). Per-request manager, so a fresh
            # request always gets a fresh sandbox.
            if context.sandbox_manager is not None:
                try:
                    await context.sandbox_manager.destroy_all()
                except Exception:
                    log.warning("agent-loop stream: sandbox cleanup failed", exc_info=True)
            await queue.put(_DONE)

    producer = asyncio.create_task(_produce())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
    finally:
        # Whatever drove us out of the loop (normal completion, or the
        # generator being closed early by a disconnected client), make sure
        # the background run is either finished or cancelled — never leaked.
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


__all__ = ["QueueEventSink", "run_agent_loop_stream"]
