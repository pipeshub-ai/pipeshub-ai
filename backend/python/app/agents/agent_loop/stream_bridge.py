"""Phase 8 (+ intent): bridges agent-loop's push-based event delivery
(`EventEmitter.emit()`/`EventSink.write()`, both plain `await`-and-return
coroutines) to FastAPI's `StreamingResponse`, which needs an async generator
that YIELDS SSE lines as they become available while `Agent.run()` is still
executing.

`Agent.run(goal)` is one long-lived coroutine — it doesn't yield control
back to its caller between tool calls the way `graph.astream(...,
stream_mode="custom")` does for the legacy LangGraph path. `QueueEventSink`
gives hooks/`SSEEventEmitter` somewhere to push events to in real time;
`run_agent_loop_stream()` runs the whole agent-loop + finalization flow as a
background task and concurrently drains that queue into SSE-formatted
strings, so the two phases (tool orchestration, then citation formatting of
the agent's own final answer — see `respond.py`'s module docstring for why
this is no longer a second LLM call) stream through the exact same
connection the legacy `stream_response()` generator writes to — same SSE
format, same event names.

The agent is driven via `agent.stream(goal)` rather than plain `agent.run(
goal)` so the terminal turn's answer text streams to the client AS IT
GENERATES (`TerminalAnswerStreamer`, `answer_streamer.py`) instead of only
after the whole run finishes — see that module's docstring for why a turn's
text isn't known to be the final answer until the turn ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.clarification import emit_pre_run_clarification
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks import CitationCollector
from app.agents.agent_loop.respond import AnswerFinalizer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

# Sentinel distinguishing "queue drained, producer done" from a real event
# dict — a private object identity check, not a value any real event could
# collide with.
_DONE = object()


def _pre_stream_error_frame(protocol: str, message: str, code: str) -> str:
    """Framed SSE string for failures that happen BEFORE `AgentContext`
    (and therefore `context.formatter`) exists — building the initial
    `ChatState` failed outright. Can't route through `ProtocolFormatter`
    (there's no `AgentContext` yet), so it duplicates just the two literal
    shapes `LegacyFormatter.error`/`AGUIFormatter.error` produce. A true
    stream-level build failure, never a graceful error answer — RUN_ERROR
    in AG-UI mode, same reasoning as `agent.py::_toolset_config_error_stream`."""
    if protocol == "agui":
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        evt = frame(AGUIEventType.RUN_ERROR, message=message, code=code)
        return f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
    return f"event: error\ndata: {json.dumps({'message': message, 'type': code})}\n\n"


async def _cancel_orphaned_agent_tasks(agent: Any) -> None:
    """Best-effort teardown for background work a run may have left
    behind. Cancelling `agent.run()` (see `agent/streaming.py`'s own
    disconnect-cancellation fix) cascades into whatever `_pending_
    spawn_tasks` entry the turn loop was directly awaiting at that
    instant, but NEVER into `_detached_tasks` — fire-and-forget
    `spawn_agent(detach=true)` children are deliberately not on the run's
    await chain — and not into a `_pending_spawn_tasks` entry that was
    scheduled but not yet reached in the await chain when cancellation
    landed. Both must be torn down before sandbox teardown, or a
    still-running child can keep executing against an already-destroyed
    sandbox / writing to a closed sink.

    Called on every exit path below — normal completion, agent failure,
    and disconnect cancellation alike — so on the common, clean-exit path
    this is a no-op (every task is already `done()`)."""
    if agent is None:
        return
    pending: list[asyncio.Task] = [t for t in getattr(agent, "_detached_tasks", ()) if not t.done()]
    pending.extend(
        t for t in (getattr(agent, "_pending_spawn_tasks", None) or {}).values() if not t.done()
    )
    if not pending:
        return
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def sse_queue_maxsize() -> int:
    raw = os.getenv("PIPESHUB_SSE_QUEUE_MAXSIZE", "1000")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid PIPESHUB_SSE_QUEUE_MAXSIZE=%r, falling back to 1000", raw)
        return 1000
    return value if value > 0 else 1000


# Event names for which two ADJACENT, not-yet-delivered instances carry no
# information the merged instance doesn't already convey — either a plain
# text/thinking delta (concatenating the two `delta`/`chunk` strings is
# byte-identical to delivering them as two separate frames) or a "replace
# the whole accumulated value" state snapshot (the newer one already
# supersedes the older). Every OTHER event name (tool lifecycle, errors,
# `complete`, ...) is never coalesced or dropped — losing/reordering one of
# those changes what the client can reconstruct, so the alternative there
# is real backpressure (this sink's `write()` blocks) rather than merging.
_COALESCABLE_DELTA_FIELD = {
    "answer_chunk": "chunk",
    "TEXT_MESSAGE_CONTENT": "delta",
    "REASONING_MESSAGE_CONTENT": "delta",
}
_COALESCABLE_SNAPSHOT_EVENTS = frozenset({"STATE_DELTA"})


def _coalesce_key(event: dict[str, Any]) -> tuple[str, str] | None:
    """`None` means "never coalesce this event". Otherwise a key that must
    match an adjacent pending event's key for the two to merge — for the
    per-message delta events this includes `messageId`, so a reasoning
    stream and the main answer stream (or two different messages) never
    get merged into each other even though both use the same event name."""
    name = event.get("event")
    if name in _COALESCABLE_DELTA_FIELD:
        data = event.get("data") or {}
        return (name, data.get("messageId", ""))
    if name in _COALESCABLE_SNAPSHOT_EVENTS:
        return (name, "")
    return None


def _merge_coalesced(pending: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    name = incoming.get("event")
    field = _COALESCABLE_DELTA_FIELD.get(name)
    if field is None:
        # Snapshot-style (STATE_DELTA): the newer accumulated snapshot
        # already supersedes the older one entirely.
        return incoming
    merged_data = dict(incoming.get("data") or {})
    merged_data[field] = (pending.get("data") or {}).get(field, "") + merged_data.get(field, "")
    return {**incoming, "data": merged_data}


class QueueEventSink:
    """`EventSink` (`app.modules.agents.event_sink`) backed by a BOUNDED
    `asyncio.Queue` — the agent-loop-path counterpart to `LangGraphEventSink`
    (which writes straight through a `StreamWriter` because LangGraph's own
    `astream(..., stream_mode="custom")` already provides the queueing/
    backpressure `Agent.run()` doesn't).

    Bounding the queue alone would just turn "unbounded memory growth" into
    "the agent run stalls on `write()` the moment the SSE consumer falls
    behind" — fine for tool/lifecycle events (there SHOULD be backpressure
    there), but token-level text/thinking deltas arrive far faster than any
    SSE consumer needs to render them at, so a burst that fills the queue
    would otherwise stall the whole run on I/O nobody is waiting on. Instead,
    consecutive coalescable events (see `_coalesce_key`) are held in a single
    one-event `_pending` slot and merged as they arrive; the slot is flushed
    to the real queue as soon as `write()` sees room, or immediately ahead of
    any non-coalescable event (which must never be reordered or dropped).
    """

    def __init__(self, queue: "asyncio.Queue[Any]") -> None:
        self._queue = queue
        self._pending: tuple[tuple[str, str], dict[str, Any]] | None = None

    async def write(self, event: dict[str, Any]) -> bool:
        key = _coalesce_key(event)
        if key is None:
            await self._flush_pending()
            await self._queue.put(event)
            return True

        if self._pending is not None and self._pending[0] == key:
            event = _merge_coalesced(self._pending[1], event)
        else:
            await self._flush_pending()
        self._pending = (key, event)
        if not self._queue.full():
            await self._flush_pending()
        return True

    async def flush(self) -> None:
        """Must be called before the queue's `_DONE` sentinel is enqueued —
        otherwise a coalesced delta still sitting in `_pending` when the run
        ends (nothing left to call `write()` and trigger the opportunistic
        flush above) would never reach the client at all."""
        await self._flush_pending()

    async def _flush_pending(self) -> None:
        if self._pending is None:
            return
        _, event = self._pending
        self._pending = None
        await self._queue.put(event)


async def _heartbeat(queue: "asyncio.Queue[Any]", interval: float = 15.0) -> None:
    """Keeps the SSE connection alive during long sub-agent tool executions
    (10-30s each, several in a row) where no real events flow — without
    this, Cloudflare's ~100s idle timeout kills the connection mid-run."""
    from app.agents.agent_loop.protocol.agui import AGUIEventType, frame
    try:
        while True:
            await asyncio.sleep(interval)
            await queue.put(frame(AGUIEventType.HEARTBEAT))
    except asyncio.CancelledError:
        pass


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
    protocol: str = "legacy",
) -> "AsyncGenerator[str, None]":
    """agent-loop counterpart to `app.api.routes.agent.stream_response()` —
    same signature/SSE wire format, so `chat_stream`'s feature-flag branch
    (Phase 8b) can call either interchangeably. Builds the same `ChatState`
    dict the legacy path builds (`build_initial_state`, reused unchanged for
    byte-for-byte parity of every derived field), wraps it as `AgentContext`,
    then drives `PipesHubAgentFactory` + `Agent.stream()` + `AnswerFinalizer`
    (which formats `Agent.run()`'s own final answer rather than making a
    second LLM call — see `respond.py`) through one shared `QueueEventSink`.
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
        yield _pre_stream_error_frame(protocol, user_message, error_code)
        return

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=sse_queue_maxsize())
    event_sink = QueueEventSink(queue)
    context = AgentContext.from_chat_state(chat_state, event_sink=event_sink, protocol=protocol)

    async def _produce() -> None:
        agent: Any = None
        try:
            factory = PipesHubAgentFactory()
            agent, _runtime, goal, clarifying_questions = await factory.create(
                context, llm, query_info.get("chatMode", "auto"),
                query=query_info.get("query", ""),
                model_name=model_name or "",
            )

            if clarifying_questions:
                # Too ambiguous to safely reorganize into a Goal — skip
                # Agent.run()/AnswerFinalizer entirely and end the turn
                # with the same ask_user_question SSE contract the main
                # agent's own tool call would produce mid-run.
                await emit_pre_run_clarification(
                    context, goal.description, clarifying_questions,
                    event_sink=context.event_sink,
                )
            else:
                collector = CitationCollector(context)
                streamer = TerminalAnswerStreamer(context, collector, context.event_sink)
                async for event in agent.stream(goal):
                    await streamer.on_event(event)
                result = agent.last_stream_result

                finalizer = AnswerFinalizer(context, collector)
                await finalizer.run(
                    agent_success=result.success, agent_error=result.error,
                    agent_output=result.output, event_sink=context.event_sink,
                    streamed_answer=streamer.streamed_answer,
                    reasoning_turns=streamer.reasoning_turns,
                )
        except Exception as exc:
            log.error("agent-loop stream: run failed: %s", exc, exc_info=True)
            error_code, user_message = classify_error(str(exc))
            # Genuine unhandled failure (never reached AnswerFinalizer's
            # graceful error-answer path) -- RUN_ERROR in AG-UI mode, same
            # as the pre-stream build-failure yields above.
            for evt in context.formatter.error(context, message=user_message, code=error_code):
                await context.event_sink.write(evt)
        finally:
            # Guarantees sandbox teardown on normal completion, agent
            # failure, AND client-disconnect cancellation alike (the outer
            # `finally` below cancels this task, and `finally` blocks still
            # run on `CancelledError`). Per-request manager, so a fresh
            # request always gets a fresh sandbox.
            #
            # Detached/orphaned children must be torn down BEFORE that
            # sandbox teardown — see `_cancel_orphaned_agent_tasks`.
            await _cancel_orphaned_agent_tasks(agent)
            if context.sandbox_manager is not None:
                try:
                    await context.sandbox_manager.destroy_all()
                except Exception:
                    log.warning("agent-loop stream: sandbox cleanup failed", exc_info=True)
            await event_sink.flush()
            await queue.put(_DONE)

    producer = asyncio.create_task(_produce())
    heartbeat = asyncio.create_task(_heartbeat(queue)) if protocol == "agui" else None
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
        if heartbeat and not heartbeat.done():
            heartbeat.cancel()
        if not producer.done():
            producer.cancel()
        tasks = [producer]
        if heartbeat:
            tasks.append(heartbeat)
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["QueueEventSink", "run_agent_loop_stream", "sse_queue_maxsize"]
