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
import contextlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from app.agent_loop_lib.core.context import CancellationToken
from app.agents.agent_loop import parked_turn, tool_approvals
from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.cancellation.registry import RunOwner
from app.agents.agent_loop.clarification import (
    emit_already_answered,
    emit_pre_run_clarification,
)
from app.agents.agent_loop.confidence import normalize as normalize_confidence
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.error_classification import (
    classify_exception,
    run_error_text,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory, card_already_answered
from app.agents.agent_loop.hooks import CitationCollector
from app.agents.agent_loop.protocol.run_usage import emit_run_usage
from app.agents.agent_loop.respond import AnswerFinalizer
from app.agents.agent_loop.retrieval_ledger import emit_prefetch_retrieval_context
from app.agents.agent_loop.turn_log import AgentRunTiming
from app.agents.chat_modes.policy import ChatModePolicy, resolve_chat_mode_policy
from app.agents.chat_modes.setup import (
    ChatSetup,
    TurnScopeRefused,
    apply_prefetch,
    prepare_chat_state,
    start_prefetch,
)
from app.core.org_context import spawn_in_request
from app.modules.demo_data.chat import (
    demo_exclusions_for_run,
    exclude_from_query,
    exclude_from_state,
    note_org_real_data,
)
from app.modules.retrieval import degraded_notice
from app.utils.conversation_tasks import begin_run, discard_tasks
from app.utils.error_class import log_failure
from app.utils.logging_fields import bind_log_fields
from app.utils.stage_timer import RunTimer
from app.utils.time_conversion import set_llm_time_zone
from app.utils.turn_memo import per_turn_lookups
from harness_core.contracts.generated.enums import CUT_SHORT_REASONS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agents.agent_loop.cancellation.registry import RunCancellationRegistry
    from app.config.configuration_service import ConfigurationService
    from app.modules.retrieval.retrieval_service import RetrievalService
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.utils.stage_timer import StageTimer

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


async def _await_tasks_draining(queue: "asyncio.Queue[Any]", tasks: list[asyncio.Task]) -> None:
    """Wait for *tasks* after the client has gone, emptying *queue* meanwhile.

    Nobody reads the SSE queue once the consumer stops, and it is bounded:
    the producer's final flush and `_DONE` would otherwise wait for space
    forever, and its cleanup would never finish.
    """
    async def _discard() -> None:
        while True:
            await queue.get()

    drain = spawn_in_request(_discard(), name="sse-queue-drain")
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        drain.cancel()


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
    get merged into each other even though both use the same event name.

    `STATE_DELTA` carries two different kinds of JSON Patch ops that both
    use this event name: `answer_delta`'s `replace` ops (a supersedable
    snapshot — the newer accumulated text/citations always wins, so
    merging is correct) and `artifact()`'s `add` op on `/artifacts/-` (an
    APPEND to a list — merging or dropping one would lose an artifact the
    client never sees again). So any `STATE_DELTA` carrying an `add` op
    must never coalesce with anything, including another `add`. Neither may
    the snapshot marked `/discarded`: the client drops snapshots until it
    sees that marker, so merging it into the next one would lose it."""
    name = event.get("event")
    if name in _COALESCABLE_DELTA_FIELD:
        data = event.get("data") or {}
        return (name, data.get("messageId", ""))
    if name in _COALESCABLE_SNAPSHOT_EVENTS:
        delta = (event.get("data") or {}).get("delta") or []
        if any(op.get("op") == "add" or op.get("path") == "/discarded" for op in delta):
            return None
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
    `asyncio.Queue` — `Agent.run()` has no built-in queueing/backpressure of
    its own, so this sink provides it for every consumer of the stream.

    Bounding the queue alone would just turn "unbounded memory growth" into
    "the agent run stalls on `write()` the moment the SSE consumer falls
    behind" — fine for tool/lifecycle events (there SHOULD be backpressure
    there), but token-level text/thinking deltas arrive far faster than any
    SSE consumer needs to render them at, so a burst that fills the queue
    would otherwise stall the whole run on I/O nobody is waiting on. Instead,
    coalescable events (see `_coalesce_key`) are held in a per-key `_pending`
    slot and merged as they arrive; a slot is flushed to the real queue as soon
    as `write()` sees room, or — for every slot, in arrival order — immediately
    ahead of any non-coalescable event (which must never be reordered or
    dropped).

    Slots are per-key rather than one shared slot because the answer stream
    interleaves two coalescable kinds: a `TEXT_MESSAGE_CONTENT` delta and an
    `answer_delta` `STATE_DELTA` snapshot per token. With one slot, each write
    evicted the other kind, so neither ever actually coalesced.
    """

    def __init__(self, queue: "asyncio.Queue[Any]") -> None:
        self._queue = queue
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}

    async def write(self, event: dict[str, Any]) -> bool:
        key = _coalesce_key(event)
        if key is None:
            await self._flush_pending()
            await self._queue.put(event)
            return True

        held = self._pending.get(key)
        self._pending[key] = _merge_coalesced(held, event) if held is not None else event
        if not self._queue.full():
            await self._flush_key(key)
        return True

    async def flush(self) -> None:
        """Must be called before the queue's `_DONE` sentinel is enqueued —
        otherwise a coalesced delta still sitting in `_pending` when the run
        ends (nothing left to call `write()` and trigger the opportunistic
        flush above) would never reach the client at all."""
        await self._flush_pending()

    async def _flush_key(self, key: tuple[str, str]) -> None:
        event = self._pending.pop(key, None)
        if event is None:
            return
        await self._queue.put(event)

    async def _flush_pending(self) -> None:
        for key in list(self._pending):
            await self._flush_key(key)


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


class GoalShaping(Protocol):
    """A route's own additions to the turn around `PipesHubAgentFactory.create()`."""

    def start(self, context: AgentContext) -> None:
        """Called just before `create()`, so work can overlap it."""
        ...

    async def apply(self, context: AgentContext, goal: Any, runtime: Any) -> None:  # noqa: ANN401
        """Called with `create()`'s goal and runtime, before the agent runs."""
        ...


def _open_context(
    chat_state: dict[str, Any],
    *,
    protocol: str,
    llm_provider: str,
    context_length: int | None,
    is_reasoning_model: bool,
    run_id: str,
    cancellation_token: CancellationToken,
    configured_context_window: int | None = None,
) -> tuple[AgentContext, asyncio.Queue[Any], QueueEventSink]:
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=sse_queue_maxsize())
    event_sink = QueueEventSink(queue)
    # Store event_sink and protocol in chat_state so ArtifactManager tools
    # (promote_artifact, etc.) can emit live SSE events without needing
    # direct access to AgentContext — chat_state IS tool_state in AgentContext.
    chat_state["event_sink"] = event_sink
    chat_state["sse_protocol"] = protocol
    # Model profile fields from etcd llm_config go in at construction, not
    # after: `model_post_init` resolves this request's image policy from
    # `llm_provider`, and a later assignment would leave it on the
    # unknown-provider default (2 images) while the wire cap used the real one.
    context = AgentContext.from_chat_state(
        chat_state, event_sink=event_sink, protocol=protocol,
        llm_provider=llm_provider, context_length=context_length,
        configured_context_window=configured_context_window,
        is_reasoning_model=is_reasoning_model,
        run_id=run_id, cancellation_token=cancellation_token,
    )
    return context, queue, event_sink


async def _stream_run(
    context: AgentContext,
    queue: asyncio.Queue[Any],
    event_sink: QueueEventSink,
    *,
    llm: BaseChatModel,
    chat_mode: str,
    query: str,
    model_name: str | None,
    model_key: str | None,
    log: logging.Logger,
    protocol: str,
    run_id: str,
    cancellation_registry: RunCancellationRegistry | None,
    run_timer: RunTimer,
    run_timing: AgentRunTiming,
    shaping: GoalShaping | None = None,
    report_confidence: bool = True,
    report_needs_input: bool = True,
    stage_timer: StageTimer | None = None,
    stream_label: str = "agent-loop stream",
    stage_fields: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Runs `PipesHubAgentFactory` + `Agent.stream()` + `AnswerFinalizer` for
    one turn as a background task and drains its `QueueEventSink` into SSE
    strings: the legacy request producer both `/chat/stream` and the agent
    route stream through. `run_timing` (with its `run_timer`) was opened by the
    route before the state was built, so it ends the turn's `agent_turn.end`."""
    # Read by the search tools for their pattern-match wait span.
    context.tool_state["_run_timer"] = run_timer

    async def _produce() -> None:
        # Every line of the run carries run_id; the task dies with the turn, so no reset.
        bind_log_fields(run_id=run_id)
        # Record headers rendered by tools show dates in the user's zone,
        # weekday included. Set inside this task so it reaches every tool
        # call of the run and nothing outside it.
        set_llm_time_zone(context.timezone)
        begin_run()
        agent: Any = None
        result: Any = None
        stop_reason: str | None = "error"
        parked = False
        failure: BaseException | None = None
        try:
            if card_already_answered(context.previous_conversations, query):
                await emit_already_answered(context, event_sink=context.event_sink)
                stop_reason = "done"
                return
            factory = PipesHubAgentFactory()
            if shaping is not None:
                shaping.start(context)
            agent, _runtime, goal, clarifying_questions = await factory.create(
                context, llm, chat_mode,
                query=query,
                model_name=model_name or "",
                model_key=model_key,
            )
            if stage_timer:
                stage_timer.mark("factory.create")
            if shaping is not None:
                await shaping.apply(context, goal, _runtime)

            if clarifying_questions:
                # Too ambiguous to safely reorganize into a Goal — skip
                # Agent.run()/AnswerFinalizer entirely and end the turn
                # with the same ask_user_question SSE contract the main
                # agent's own tool call would produce mid-run.
                await emit_pre_run_clarification(
                    context, goal.description, clarifying_questions,
                    event_sink=context.event_sink,
                )
                # The intent call that asked the question has been paid for.
                await emit_run_usage(context, agent, model=model_name or None)
                stop_reason = "awaiting_input"
            else:
                collector = CitationCollector(context)
                streamer = TerminalAnswerStreamer(context, collector, context.event_sink)
                checkpoint = (
                    await _runtime.checkpoint_store.latest(context.durable_run_id)
                    if _runtime.checkpoint_store is not None
                    and context.durable_run_id
                    else None
                )
                event_stream = (
                    agent.stream_resume(context.durable_run_id)
                    if checkpoint is not None
                    else agent.stream(
                        goal,
                        _skip_start=bool(context.tool_state.get("ask_user_question_resume")),
                        _skip_user_message=bool(context.tool_state.get(tool_approvals.RESUMING)),
                    )
                )
                async for event in event_stream:
                    run_timing.on_event(event)
                    await streamer.on_event(event)
                result = agent.last_stream_result
                stop_reason = None  # from `result`, inside `end()`
                parked = tool_approvals.awaiting_approval(context)
                # A run that stopped for the person keeps its turn for the answer to carry on from.
                await tool_approvals.keep_stopped_turn(context, agent)
                if result.needs_input:
                    await parked_turn.keep_question(context, agent)

                # Tool `retrieval_context` frames come from the factory hook;
                # the usage totals from here.
                await emit_run_usage(context, agent, result, model=model_name or None)

                finalizer = AnswerFinalizer(context, collector)
                confidence: dict[str, Any] = {}
                if report_needs_input:
                    confidence["agent_needs_input"] = result.needs_input
                if report_confidence:
                    # Map the internal Confidence enum (low/medium/high) to the
                    # user-facing display label via confidence.py's _INTERNAL_TO_DISPLAY.
                    # Only populated when final_answer tool is used (Phase 6+).
                    confidence["agent_confidence"] = normalize_confidence(
                        result.confidence.value if result.confidence is not None else None
                    )
                run_timer.start("finish", "finish")
                await finalizer.run(
                    agent_success=result.success, agent_error=run_error_text(result),
                    agent_output=result.output, event_sink=context.event_sink,
                    streamed_answer=streamer.streamed_answer,
                    reasoning_turns=streamer.reasoning_turns,
                    **confidence,
                    # Stop Generation (Phase 3b): `result.cancelled` is an
                    # immutable snapshot `Agent.fail(..., status=
                    # "cancelled")` took at the moment the agent loop itself
                    # (PRE_TURN guard, per-tool-call guard, or
                    # `LangChainTransport.stream()`'s mid-chunk check)
                    # observed cancellation — NOT a live re-read of
                    # `cancellation_token.is_cancelled` here. A live read
                    # races a cancel() that arrives (a late/duplicate stop
                    # request) after `agent.stream(goal)` already returned
                    # a genuinely successful — or independently failed —
                    # result, which would otherwise mislabel it "stopped".
                    agent_cancelled=result.cancelled,
                    # Only on an answer a limit cut short (BUDGETEND), so every other completion is called as before.
                    **({"truncated_by": cut} if (cut := getattr(result, "truncated_by", None)) in CUT_SHORT_REASONS else {}),
                )
                run_timer.stop("finish")
        except asyncio.CancelledError:
            stop_reason = "client_disconnect"
            raise
        except Exception as exc:
            # Before any await: a disconnect during the awaits below must not lose it.
            failure = exc
            stop_reason = "error"
            # What the turn spent before failing; nothing when the frame
            # already went out on the success path.
            await emit_run_usage(context, agent, model=model_name or None)
            error_code, user_message = classify_exception(exc)
            # Genuine unhandled failure (never reached AnswerFinalizer's
            # graceful error-answer path) -- RUN_ERROR in AG-UI mode, same
            # as the pre-stream build-failure yields.
            for evt in context.formatter.error(context, message=user_message, code=error_code):
                await context.event_sink.write(evt)
        finally:
            # First, and without awaiting: a disconnect can cancel the awaits
            # below, and this must still run.
            discard_tasks(context.conversation_id)
            # Logged before _DONE so the line is written even when the
            # consumer stops waiting on this task after the last event.
            run_timing.end(
                log, stop_reason, result=result, awaiting_approval=parked, ledger=context.usage_ledger, exc=failure,
            )
            # Flush any pending coalesced events and signal the SSE
            # consumer loop FIRST, so the HTTP response closes promptly
            # after the last real event.  Cleanup (orphan cancellation,
            # sandbox teardown) runs AFTER — it can take seconds when
            # Docker working directories contain large node_modules trees,
            # and blocking _DONE behind that would keep the SSE connection
            # (and the user-facing spinner) open unnecessarily.
            await event_sink.flush()
            await queue.put(_DONE)
            log.debug("%s: _DONE enqueued, starting cleanup", stream_label)
            if cancellation_registry is not None:
                await cancellation_registry.unregister(run_id)
            await _cancel_orphaned_agent_tasks(agent)
            if context.sandbox_manager is not None:
                try:
                    await context.sandbox_manager.destroy_all()
                except Exception as exc:
                    log_failure(log, "agent_turn.cleanup_failed", exc, step="sandbox")
            # Every MCP session this request opened and every pooled session it leased
            # (MCP_HUB); a request with no MCP server attached builds nothing.
            try:
                from app.agents.agent_loop.mcp_session import close_request_sessions

                await close_request_sessions(context)
            except Exception as exc:
                log_failure(log, "agent_turn.cleanup_failed", exc, step="mcp_sessions")
            try:
                await context.close_credentials()
            except Exception as exc:
                log_failure(log, "agent_turn.cleanup_failed", exc, step="credentials")
            await event_sink.flush()
            await queue.put(_DONE)

    producer = asyncio.create_task(_produce())
    heartbeat = asyncio.create_task(_heartbeat(queue)) if protocol == "agui" else None
    normal_exit = False
    first_event_seen = False
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                normal_exit = True
                break
            if not first_event_seen:
                first_event_seen = True
                if stage_timer:
                    # Everything up to the model's first emission — the number
                    # that actually decides how long the UI sits on "Thinking".
                    stage_timer.mark("first_agent_event")
                    stage_timer.emit(log, "agent chat stream", **(stage_fields or {}))
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
    except Exception as exc:
        # A frame that cannot be written: the turn failed here, not by a disconnect.
        run_timing.end(log, "error", exc=exc)
        raise
    finally:
        # Idempotent: a stream that errored or was cancelled before its first
        # event still reports where the time went.
        if stage_timer:
            stage_timer.emit(log, "agent chat stream (incomplete)", **(stage_fields or {}))
        if heartbeat and not heartbeat.done():
            heartbeat.cancel()
        if not normal_exit and not producer.done():
            # Abnormal exit (client disconnect) — cancel the producer so
            # the agent doesn't keep running against nothing, then wait
            # for it to finish its finally-block cleanup.
            producer.cancel()
        if not normal_exit:
            # Abnormal: must await the producer so cleanup runs to
            # completion even after cancellation.
            tasks = [producer]
            if heartbeat:
                tasks.append(heartbeat)
            await _await_tasks_draining(queue, tasks)
        else:
            # Normal exit: _DONE was received, but the producer's
            # cleanup tail (orphan cancellation, sandbox teardown) is
            # still running.  Don't await it — the HTTP response should
            # close promptly.  The cleanup task stays alive in the event
            # loop and finishes on its own; each request has its own
            # sandbox_manager, so there's no cross-request interference.
            if heartbeat:
                await asyncio.gather(heartbeat, return_exceptions=True)


def _run_registration(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    cancellation_owner: RunOwner | None,
) -> tuple[str, CancellationToken, RunOwner]:
    """The run id, its token and owner. `cancellation_owner` overrides the
    default owner built from `user_info`: service-account agents pass
    `enriched_user_info` (the agent creator's identity, needed for retrieval
    ACL) as `user_info`, but the RUN is owned by the authenticated caller
    (who issued the request and whose cancel request will carry their own
    userId). Without this, `cancel()` returns 403."""
    run_id = query_info.get("runId") or str(uuid.uuid4())
    owner = cancellation_owner or RunOwner(
        user_id=user_info.get("userId", ""),
        org_id=user_info.get("orgId", ""),
        conversation_id=query_info.get("conversationId"),
    )
    return run_id, CancellationToken(), owner


@degraded_notice.scoped
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
    llm_provider: str = "",
    context_length: int | None = None,
    is_reasoning_model: bool = False,
    stage_timer: "StageTimer | None" = None,
    cancellation_registry: "RunCancellationRegistry | None" = None,
    cancellation_owner: "RunOwner | None" = None,
    entity_vector_store: Any = None,
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
    from app.connectors.core.registry.connector_builder import ConnectorScope
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.modules.code_graph.connectors import connector_instances_have_code
    from app.utils.connector_instances import fetch_user_connector_instances
    from app.utils.execute_query import (
        agent_knowledge_sql_connector_ids,
        connector_instances_have_sql,
        sql_connector_instance_ids,
    )
    from app.utils.fetch_slack_thread import connector_instances_have_slack

    # Stop Generation (Phase 3a): registered BEFORE `build_initial_state()`
    # (never mind the try/except below) so even the "Thinking" phase —
    # connector-flag fetch, tool/prompt wiring inside `factory.create()` —
    # is cancellable, not just the model call. The route layer already
    # validated `runId` (format) and, when present, that it isn't already
    # active (see `chatbot.py`/`agent.py`'s pre-stream 409 check) before
    # this generator ever started running.
    run_id, cancellation_token, run_owner = _run_registration(
        query_info, user_info, cancellation_owner,
    )
    if cancellation_registry is not None:
        await cancellation_registry.register(run_id, cancellation_token, run_owner)
    # `stage_timer` stops at the first agent event; this one times the rest
    # of the run (turns, tools, pattern-match wait, finish) for the turn's
    # end line. Same flag, so off together; the counts are kept either way.
    run_timer = RunTimer(enabled=bool(stage_timer is not None and stage_timer.enabled))
    run_timing = AgentRunTiming(run_timer, run_id=run_id, model=model_name, mode=query_info.get("chatMode"))

    try:
        # One query feeds every flag; they used to be one identical lookup each.
        connector_instances = await fetch_user_connector_instances(
            graph_provider, user_info["userId"], user_info["orgId"], log,
        )
        sql_instances = connector_instances
        if query_info.get("is_service_account"):
            # Anyone in the org runs this agent on one identity, so only the org's databases.
            sql_instances = [c for c in connector_instances if c.get("scope") == ConnectorScope.TEAM.value]
        has_sql_connector = connector_instances_have_sql(sql_instances)
        has_slack_connector = connector_instances_have_slack(connector_instances)
        has_code_connector = connector_instances_have_code(connector_instances)
        if stage_timer:
            stage_timer.mark("connector_flags")
        demo_excluded = await demo_exclusions_for_run(graph_provider, config_service, user_info, log)
        query_info = exclude_from_query(query_info, demo_excluded)
        chat_state = build_initial_state(
            query_info, user_info, llm, log, retrieval_service, graph_provider,
            reranker_service, config_service, model_name, model_key, org_info,
            "react", has_sql_connector=has_sql_connector, is_multimodal_llm=is_multimodal_llm,
            has_slack_connector=has_slack_connector,
            has_code_connector=has_code_connector, client_name=client_name,
            entity_vector_store=entity_vector_store,
        )
        exclude_from_state(chat_state, demo_excluded)
        await note_org_real_data(chat_state, graph_provider, user_info.get("orgId", ""), log)
        chat_state["allowed_sql_connector_ids"] = (
            sql_connector_instance_ids(sql_instances, user_info["orgId"])
            & agent_knowledge_sql_connector_ids(chat_state.get("agent_knowledge"))
        )
    except asyncio.CancelledError:
        run_timing.end(log, "client_disconnect")
        raise
    except Exception as exc:
        error_code, user_message = classify_exception(exc)
        run_timing.end(log, "error", exc=exc, code=error_code)
        if cancellation_registry is not None:
            await cancellation_registry.unregister(run_id)
        yield _pre_stream_error_frame(protocol, user_message, error_code)
        return

    # Picked up by PipesHubAgentFactory.create() for its own sub-stage marks.
    chat_state["_stage_timer"] = stage_timer
    context, queue, event_sink = _open_context(
        chat_state, protocol=protocol, llm_provider=llm_provider,
        context_length=context_length, is_reasoning_model=is_reasoning_model,
        run_id=run_id, cancellation_token=cancellation_token,
        configured_context_window=context_length,
    )
    # `aclosing`: a client that goes away closes this generator, and only an explicit close reaches
    # `_stream_run`'s cleanup (cancel the producer) at once instead of whenever it is collected.
    async with contextlib.aclosing(_stream_run(
        context, queue, event_sink,
        llm=llm, chat_mode=query_info.get("chatMode", "quick"), query=query_info.get("query", ""),
        model_name=model_name, model_key=model_key, log=log, protocol=protocol, run_id=run_id,
        cancellation_registry=cancellation_registry, run_timer=run_timer, run_timing=run_timing,
        stage_timer=stage_timer, stage_fields={"mode": query_info.get("chatMode"), "model": model_name},
    )) as frames:
        async for frame in frames:
            yield frame


class _ChatTurn:
    """`/chat/stream`'s additions: the mode's prefetch, overlapped with
    `create()`, and the attachments' text, both folded into the goal."""

    def __init__(
        self, setup: ChatSetup, policy: ChatModePolicy, *, retrieval_service: Any,  # noqa: ANN401
        graph_provider: Any, is_multimodal_llm: bool, log: logging.Logger,  # noqa: ANN401
    ) -> None:
        self._setup = setup
        self._policy = policy
        self._retrieval = retrieval_service
        self._graph = graph_provider
        self._multimodal = is_multimodal_llm
        self._log = log
        self._prefetch: asyncio.Future[Any] | None = None

    def start(self, context: AgentContext) -> None:
        self._prefetch = start_prefetch(
            self._setup, context, self._policy, retrieval_service=self._retrieval,
            graph_provider=self._graph, is_multimodal_llm=self._multimodal, log=self._log,
        )

    async def apply(self, context: AgentContext, goal: Any, runtime: Any) -> None:  # noqa: ANN401
        prefetched = await self._prefetch if self._prefetch is not None else None
        apply_prefetch(context, goal, prefetched, registry=runtime.tool_registry, log=self._log)
        # After the merge, so the frame reports what reached the model; emitted even when the
        # prefetch was skipped or found nothing, so a consumer can tell those apart.
        await emit_prefetch_retrieval_context(context, scheduled=self._prefetch is not None, result=prefetched)


@degraded_notice.scoped
@per_turn_lookups  # knowledge-hub access is asked about by several stages of a turn
async def run_chat_stream(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    llm: "BaseChatModel",
    policy: ChatModePolicy | None,
    log: logging.Logger,
    *,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    reranker_service: object | None,
    config_service: ConfigurationService,
    org_info: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_key: str | None = None,
    is_multimodal_llm: bool = False,
    context_length: int = 128000,
    configured_context_window: int | None = None,
    llm_provider: str = "",
    system_prompts_config: dict[str, Any] | None = None,
    protocol: str = "legacy",
    client_name: str | None = None,
    cancellation_registry: "RunCancellationRegistry | None" = None,
    cancellation_owner: "RunOwner | None" = None,
    entity_vector_store: EntityVectorStore | None = None,
) -> "AsyncGenerator[str, None]":
    """`/chat/stream` (the built-in assistant, every mode): the turn
    `chat_modes.setup` describes, streamed like an agent turn.

    ``entity_vector_store`` (optional) backs the knowledge-graph
    ``search_entities``/``find_records_by_entity`` tools; with ``None`` they
    are not granted (see ``factory._initial_entity_tool_grant``).
    """
    policy = policy or resolve_chat_mode_policy(query_info.get("chatMode"))
    # Stop Generation (Phase 3a): registered BEFORE the state is built so the
    # "Thinking" phase is cancellable too.
    run_id, cancellation_token, run_owner = _run_registration(
        query_info, user_info, cancellation_owner,
    )
    if cancellation_registry is not None:
        await cancellation_registry.register(run_id, cancellation_token, run_owner)
    run_timer = RunTimer()
    run_timing = AgentRunTiming(run_timer, run_id=run_id, model=model_name, mode=query_info.get("chatMode"))

    try:
        setup = await prepare_chat_state(
            query_info, user_info, llm, policy, log,
            retrieval_service=retrieval_service, graph_provider=graph_provider,
            reranker_service=reranker_service, config_service=config_service,
            org_info=org_info, model_name=model_name, model_key=model_key,
            is_multimodal_llm=is_multimodal_llm, system_prompts_config=system_prompts_config or {},
            client_name=client_name, entity_vector_store=entity_vector_store,
        )
    except TurnScopeRefused as refused:
        run_timing.end(log, "error", error_class=refused.error_class, code=refused.code)
        if cancellation_registry is not None:
            await cancellation_registry.unregister(run_id)
        yield _pre_stream_error_frame(protocol, refused.message, refused.code)
        return
    except asyncio.CancelledError:
        run_timing.end(log, "client_disconnect")
        raise
    except Exception as exc:
        error_code, user_message = classify_exception(exc)
        run_timing.end(log, "error", exc=exc, code=error_code)
        if cancellation_registry is not None:
            await cancellation_registry.unregister(run_id)
        yield _pre_stream_error_frame(protocol, user_message, error_code)
        return

    context, queue, event_sink = _open_context(
        setup.chat_state, protocol=protocol, llm_provider=llm_provider,
        context_length=context_length, is_reasoning_model=False,
        run_id=run_id, cancellation_token=cancellation_token,
        configured_context_window=configured_context_window,
    )
    shaping = _ChatTurn(
        setup, policy, retrieval_service=retrieval_service, graph_provider=graph_provider,
        is_multimodal_llm=is_multimodal_llm, log=log,
    )
    async with contextlib.aclosing(_stream_run(
        context, queue, event_sink,
        llm=llm, chat_mode=policy.loop_chat_mode, query=setup.query_info.get("query", ""),
        model_name=model_name, model_key=model_key, log=log, protocol=protocol, run_id=run_id,
        cancellation_registry=cancellation_registry, run_timer=run_timer, run_timing=run_timing, shaping=shaping,
        # `/chat/stream` never reported a `final_answer` confidence or a pending
        # question (main #3592 reports it on the agent route only); kept as it was.
        report_confidence=False, report_needs_input=False, stream_label="run_chat_stream",
    )) as frames:
        async for frame in frames:
            yield frame


__all__ = [
    "GoalShaping",
    "QueueEventSink",
    "run_agent_loop_stream",
    "run_chat_stream",
    "sse_queue_maxsize",
]
