"""`TerminalAnswerStreamer`: live token streaming of the agent's own final
answer (Phase 1 of the "no separate responder" fix — see `respond.py`'s
module docstring for why the answer is the ReAct loop's own terminal text,
never a second LLM call).

Before this, `stream_bridge.py` ran `agent.run(goal)` to full completion
non-streaming, then `AnswerFinalizer` replayed the ALREADY-FINISHED text
word-by-word to simulate streaming. That meant: live tool-orchestration
events, then silence for the whole duration of the final answer's
generation, then a fake burst of "streamed" chunks. `Agent.stream()`
(`agent_loop_lib/agent/streaming.py`) already turns on real per-token
`TEXT_MESSAGE_START/CONTENT/END` events (`Agent.step()`'s streaming branch)
— this class is the consumer that turns those into PipesHub's `answer_chunk`
SSE events as they actually arrive, one per model turn's text.

A model turn's text is not known to be the final answer until the turn
ends: a tool-calling turn can also emit text first (a "let me search for
that..." preamble) before its tool_calls arrive. So every `TEXT_MESSAGE_START`
resets the buffer (new turn), and a `TOOL_CALL_START` for the turn just
streamed clears whatever was shown so far (the frontend renders
`accumulated` by replacement — see `frontend/app/(main)/chat/streaming.ts`).
The one turn that ends via `AGENT_COMPLETE` instead of a tool call is, by
`Agent.step()`'s own contract, always the terminal turn — see
`agent_loop_lib/agent/__init__.py::step()`'s no-tool-calls branch — so
whatever is in the buffer at that point IS the streamed answer.

Citation refs (`[source](refN)`) are resolved progressively via
`normalize_citations_and_chunks` on every delta, using the `ref_to_url`
mapping from `CitationCollector` — the same function `AnswerFinalizer`
uses for the authoritative `complete` event, so numbered citations appear
live as the answer streams in. The citation state (`ref_to_url`,
`final_results`, `web_records`) is snapshotted once per turn at
`TEXT_MESSAGE_START` — stable within a turn since no tools execute between
the model call's first token and its completion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.events.base import EventType
from app.utils.citations import normalize_citations_and_chunks

if TYPE_CHECKING:
    from app.agent_loop_lib.events.base import AgentEvent
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.hooks.citations import CitationCollector
    from app.modules.agents.event_sink import EventSink

__all__ = ["TerminalAnswerStreamer"]


class TerminalAnswerStreamer:
    """Consumes the `AgentEvent`s yielded by `Agent.stream(goal)` (top-level
    agent only — see `stream_bridge.py`) and turns `TEXT_MESSAGE_CONTENT`
    deltas into live `answer_chunk` SSE writes with progressively resolved
    citations. `streamed_answer` holds the raw text of whichever turn last
    completed via `AGENT_COMPLETE`, for `AnswerFinalizer` to compare against
    `AgentResult.output`."""

    def __init__(
        self, context: "AgentContext", collector: "CitationCollector", event_sink: "EventSink",
    ) -> None:
        self._context = context
        self._collector = collector
        self._event_sink = event_sink

        self._buffer = ""
        self._web_records: list[dict[str, Any]] = []
        self._ref_to_url: dict[str, str] | None = None
        self.streamed_answer = ""

        # Reasoning/thinking accumulation (Phase 1f) — one entry per model
        # turn that actually reasoned. Populated from the SAME `AgentEvent`
        # stream `Agent.stream()` fans this class's events out of, so it
        # requires no separate subscription; `AnswerFinalizer` reads
        # `reasoning_turns` once the run completes to decide whether/how
        # much to persist (see `reasoning_persistence.py`).
        self._reasoning_buffer = ""
        self.reasoning_turns: list[dict[str, Any]] = []

    async def on_event(self, event: "AgentEvent") -> None:
        if event.event_type == EventType.TEXT_MESSAGE_START:
            self._start_turn()
        elif event.event_type == EventType.TEXT_MESSAGE_CONTENT:
            await self._on_delta(event.payload.get("delta", ""))
        elif event.event_type == EventType.TOOL_CALL_START:
            await self._clear_preamble()
        elif event.event_type == EventType.AGENT_COMPLETE:
            self.streamed_answer = self._buffer
        elif event.event_type == EventType.REASONING_MESSAGE_START:
            self._reasoning_buffer = ""
        elif event.event_type == EventType.REASONING_MESSAGE_CONTENT:
            self._reasoning_buffer += event.payload.get("delta", "")
        elif event.event_type == EventType.REASONING_MESSAGE_END:
            if self._reasoning_buffer:
                self.reasoning_turns.append({
                    "turnIndex": event.payload.get("turn_index"),
                    "content": self._reasoning_buffer,
                })
            self._reasoning_buffer = ""

    def _start_turn(self) -> None:
        """Snapshot the citation state for this turn's normalization calls.
        Stable within a turn since no tools execute mid-model-call."""
        from app.modules.agents.qna.nodes import _extract_web_records_from_tool_results

        self._buffer = ""
        tool_results = self._context.tool_state.get("all_tool_results", [])
        self._web_records = _extract_web_records_from_tool_results(tool_results, self._context.org_id)
        ref_mapper = self._collector.citation_ref_mapper
        self._ref_to_url = ref_mapper.ref_to_url if ref_mapper is not None else None

    async def _on_delta(self, delta: str) -> None:
        if not delta:
            return
        self._buffer += delta
        normalized, citations = normalize_citations_and_chunks(
            self._buffer,
            self._collector.final_results,
            self._collector.tool_records,
            ref_to_url=self._ref_to_url,
            virtual_record_id_to_result=self._collector.virtual_records,
            web_records=self._web_records,
        )
        # Legacy: the raw+normalized `answer_chunk` IS the text delivery
        # mechanism. AG-UI: the raw per-token text already streamed via
        # `AGUIEventEmitter` reacting to the SAME `TEXT_MESSAGE_CONTENT`
        # AgentEvent — this only carries the progressively-normalized
        # citation state (see `AGUIFormatter.answer_delta`/Phase 1e).
        for evt in self._context.formatter.answer_delta(
            self._context, chunk=delta, accumulated=normalized, citations=citations,
        ):
            await self._event_sink.write(evt)

    async def _clear_preamble(self) -> None:
        """A tool call is about to run for the turn that was just streamed
        — it was never the final answer. Clear it immediately rather than
        leaving it on screen until the next turn's `TEXT_MESSAGE_START`
        (which could be many seconds away, or never, if this was the run's
        last tool call). No-ops on the 2nd+ tool call within the same turn
        (buffer already cleared by the 1st).

        AG-UI mode needs no separate signal here: `Agent.step()`'s
        streaming branch always emits `TEXT_MESSAGE_END` for a turn before
        its tool calls are dispatched, so `AGUIEventEmitter` has already
        closed that turn's message by the time this fires — the frontend
        drops an ended message with no following `RUN_FINISHED` the same
        way it drops this legacy empty-buffer reset."""
        if not self._buffer:
            return
        self._buffer = ""
        for evt in self._context.formatter.answer_delta(self._context, chunk="", accumulated="", citations=[]):
            await self._event_sink.write(evt)
